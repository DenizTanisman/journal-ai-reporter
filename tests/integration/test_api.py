"""End-to-end API tests with mocked Cornell + Gemini.

We exercise the real FastAPI app, but `respx` short-circuits the Cornell HTTP
client and `app.dependency_overrides` swaps in a fake Gemini-backed
ReporterService. Anything in between (auth, parser, tag dispatch, error
mapping, rate limit, request-id logging) runs for real.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.api.dependencies import get_reporter_service
from src.api.limiter import limiter
from src.config import get_settings
from src.modules.reporter.ai_client import GeminiClient
from src.modules.reporter.service import ReporterService

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _cornell_payload(num_entries: int = 3) -> dict:
    return {
        "entries": [
            {
                "id": i,
                "date": f"2026-04-{i + 10:02d}",
                "cue_column": "",
                "notes_column": (
                    "Sunum stresi var, endişeliyim."
                    if i == 0
                    else "İlk kez başardım, mutluyum."
                ),
                "summary": "",
                "planlar": "[ ] Endpoint yaz" if i == 0 else "",
                "created_at": "2026-04-10T08:00:00+00:00",
                "updated_at": "2026-04-10T08:00:00+00:00",
            }
            for i in range(num_entries)
        ],
        "count": num_entries,
        "range": {"start": "2026-04-10", "end": "2026-04-20"},
    }


GEMINI_DETAIL = json.dumps(
    {
        "summary": "Karışık ama umut verici bir hafta.",
        "todos": {"open": "1 açık iş", "completed": "", "deferred": ""},
        "concerns": {"anxieties": "Sunum stresi", "fears": "", "failures": ""},
        "successes": {"achievements": "İlk başarı", "milestones": "İlk kez", "positive_moments": "Mutlu"},
        "patterns": ["Stres + ilerleme"],
        "recommendation": "Mola ver.",
    }
)


class FakeBackend:
    def __init__(self, responses=None, raise_exc=None):
        self.responses = list(responses or [])
        self.raise_exc = raise_exc

    async def generate(self, *, system_prompt: str, user_prompt: str, timeout: float) -> str:
        if self.raise_exc:
            raise self.raise_exc
        return self.responses.pop(0)


@pytest.fixture
def client():
    """TestClient with rate-limit storage reset per test.

    slowapi keeps in-memory hit counts on the limiter, which leak across tests
    and would make the rate-limit assertion order-dependent.
    """
    limiter.reset()
    from src.main import create_app

    app = create_app()
    return app, TestClient(app)


@pytest.fixture
def auth_headers():
    settings = get_settings()
    return {"Authorization": f"Bearer {settings.internal_api_key}"}


def _override_reporter(app, backend: FakeBackend) -> None:
    def factory():
        return ReporterService(ai_client=GeminiClient(backend=backend))

    app.dependency_overrides[get_reporter_service] = factory


# ---------------------------------------------------------------------------
# /health and /tags
# ---------------------------------------------------------------------------
def test_health_open(client):
    _, c = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "X-Request-ID" in r.headers


def test_tags_requires_auth(client):
    _, c = client
    r = c.get("/tags")
    assert r.status_code == 401


def test_tags_returns_whitelist(client, auth_headers):
    _, c = client
    r = c.get("/tags", headers=auth_headers)
    assert r.status_code == 200
    assert "/detail" in r.json()["whitelist"]


# ---------------------------------------------------------------------------
# /report — happy path per tag
# ---------------------------------------------------------------------------
@respx.mock
@pytest.mark.parametrize("tag", ["/detail", "/todo", "/concern", "/success"])
def test_report_happy_path_for_each_whitelisted_tag(tag, client, auth_headers):
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_cornell_payload(3))
    )
    app, c = client
    backend = FakeBackend(responses=[GEMINI_DETAIL])
    _override_reporter(app, backend)

    body = {"tag": tag, "date_range": {"start": "2026-04-10", "end": "2026-04-20"}}
    r = c.post("/report", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["tag"] == tag
    assert payload["entry_count"] == 3
    assert payload["raw_markdown"]


@respx.mock
def test_report_date_tag_in_range(client, auth_headers):
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_cornell_payload(3))
    )
    app, c = client
    backend = FakeBackend(
        responses=[
            json.dumps(
                {
                    "narrative": "12 Nisan'da iyi bir gün.",
                    "highlights": ["İlk kez başardım"],
                    "todos": [],
                    "emotional_tone": "Sevinçli",
                }
            )
        ]
    )
    _override_reporter(app, backend)

    body = {"tag": "/date{12.04.2026}", "date_range": {"start": "2026-04-10", "end": "2026-04-20"}}
    r = c.post("/report", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["tag"] == "/date{12.04.2026}"
    assert payload["date_range"]["start"] == "2026-04-12"


# ---------------------------------------------------------------------------
# /report — error mapping
# ---------------------------------------------------------------------------
def test_report_missing_auth(client):
    _, c = client
    r = c.post("/report", json={"tag": "/detail"})
    assert r.status_code == 401


def test_report_wrong_token(client):
    _, c = client
    r = c.post(
        "/report",
        headers={"Authorization": "Bearer wrong"},
        json={"tag": "/detail"},
    )
    assert r.status_code == 401


def test_report_invalid_tag(client, auth_headers):
    _, c = client
    r = c.post("/report", headers=auth_headers, json={"tag": "/nonsense"})
    # Pydantic body-validation surfaces as 422
    assert r.status_code == 422


@respx.mock
def test_report_no_entries_returns_404(client, auth_headers):
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(
            200,
            json={"entries": [], "count": 0, "range": {"start": "2026-04-01", "end": "2026-04-30"}},
        )
    )
    _, c = client
    r = c.post(
        "/report",
        headers=auth_headers,
        json={"tag": "/detail", "date_range": {"start": "2026-04-01", "end": "2026-04-30"}},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "no_entries"


@respx.mock
def test_report_date_tag_out_of_range_404(client, auth_headers):
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_cornell_payload(3))
    )
    app, c = client
    _override_reporter(app, FakeBackend(responses=[GEMINI_DETAIL]))
    r = c.post(
        "/report",
        headers=auth_headers,
        json={"tag": "/date{01.01.2020}", "date_range": {"start": "2026-04-10", "end": "2026-04-20"}},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "date_not_in_range"


@respx.mock
def test_report_cornell_down_returns_502(client, auth_headers):
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(500, text="boom")
    )
    _, c = client
    r = c.post(
        "/report",
        headers=auth_headers,
        json={"tag": "/detail", "date_range": {"start": "2026-04-01", "end": "2026-04-30"}},
    )
    assert r.status_code == 502
    assert r.json()["code"] == "cornell_unavailable"


@respx.mock
def test_report_gemini_rate_limit_propagates_429(client, auth_headers):
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_cornell_payload(3))
    )
    app, c = client
    _override_reporter(app, FakeBackend(raise_exc=RuntimeError("HTTP 429: rate limit")))
    r = c.post(
        "/report",
        headers=auth_headers,
        json={"tag": "/detail", "date_range": {"start": "2026-04-10", "end": "2026-04-20"}},
    )
    assert r.status_code == 429
    assert r.json()["code"] == "gemini_rate_limit"


# ---------------------------------------------------------------------------
# Rate limiting (slowapi)
# ---------------------------------------------------------------------------
@respx.mock
def test_report_local_rate_limit_kicks_in(auth_headers, monkeypatch):
    """Drop the limit to a tiny value, then reload the modules that bind
    the decorator string at import time so the new limit takes effect."""
    import importlib
    import src.api.limiter as limiter_mod
    import src.api.routes as routes_mod
    import src.main as main_mod

    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()
    importlib.reload(limiter_mod)
    importlib.reload(routes_mod)
    importlib.reload(main_mod)

    limiter_mod.limiter.reset()
    app = main_mod.create_app()
    c = TestClient(app)

    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_cornell_payload(3))
    )
    fake = FakeBackend(responses=[GEMINI_DETAIL] * 5)
    app.dependency_overrides[routes_mod.get_reporter_service] = lambda: ReporterService(
        ai_client=GeminiClient(backend=fake)
    )

    body = {"tag": "/detail", "date_range": {"start": "2026-04-10", "end": "2026-04-20"}}
    assert c.post("/report", headers=auth_headers, json=body).status_code == 200
    assert c.post("/report", headers=auth_headers, json=body).status_code == 200
    third = c.post("/report", headers=auth_headers, json=body)
    assert third.status_code == 429
    assert third.json()["code"] == "rate_limit"

    # Restore default modules so subsequent tests see the real limit again.
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE")
    get_settings.cache_clear()
    importlib.reload(limiter_mod)
    importlib.reload(routes_mod)
    importlib.reload(main_mod)


# ---------------------------------------------------------------------------
# /report/file — debug upload path
# ---------------------------------------------------------------------------
async def test_report_file_runs_only_reporter(client, auth_headers):
    from src.modules.converter.schemas import RawEntry, RawEntryCollection
    from src.modules.parser.service import ParserService

    raw = RawEntryCollection(
        entries=[
            RawEntry(
                id=1,
                date=date(2026, 4, 12),
                notes_column="İlk kez başardım, mutluyum.",
                created_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
                updated_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
            )
        ],
        count=1,
        range_start=date(2026, 4, 12),
        range_end=date(2026, 4, 12),
    )
    parsed = await ParserService().parse(raw)
    payload = parsed.model_dump_json().encode("utf-8")

    app, c = client
    _override_reporter(app, FakeBackend(responses=[GEMINI_DETAIL]))

    r = c.post(
        "/report/file?tag=/detail",
        headers=auth_headers,
        files={"parsed": ("parsed.json", payload, "application/json")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["entry_count"] == 1
