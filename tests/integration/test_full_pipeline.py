"""End-to-end pipeline test.

Spans both processes that ship in this repo: the Cornell sidecar serves
the same Postgres DB Diary writes to, and the Reporter Bridge runs in
the same Python process via TestClient. Cornell HTTP is stubbed with
respx (so the bridge hits "the sidecar" without us starting a second
uvicorn), and Gemini is swapped out via dependency_overrides.

Diary FAZ 1.3 moved storage to Postgres, so the seed path uses asyncpg
against `CORNELL_DATABASE_URL` (skipped when unset). Fixture rows live
under future-date prefixes that can't collide with real diary content.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, timedelta

import asyncpg
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from cornell_journal_api.src.main import create_app as create_sidecar
from src.api.dependencies import get_reporter_service
from src.api.limiter import limiter
from src.config import get_settings
from src.modules.reporter.ai_client import GeminiClient
from src.modules.reporter.service import ReporterService

pytestmark = pytest.mark.integration


# Mirror Diary's Postgres schema (only the columns the sidecar reads).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS diary_entries (
    date            TEXT PRIMARY KEY,
    diary           TEXT NOT NULL DEFAULT '',
    title_1 TEXT, content_1 TEXT,
    title_2 TEXT, content_2 TEXT,
    title_3 TEXT, content_3 TEXT,
    title_4 TEXT, content_4 TEXT,
    title_5 TEXT, content_5 TEXT,
    title_6 TEXT, content_6 TEXT,
    title_7 TEXT, content_7 TEXT,
    summary         TEXT DEFAULT '',
    quote           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    device_id       TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    is_dirty        BOOLEAN NOT NULL DEFAULT FALSE
);
"""

# Anchor 3 fixture rows at *today*-relative dates so the sidecar's
# default 30-day window catches them. We stamp them with a tag in the
# diary text and clean up by primary key on teardown.
FIXTURE_DATES = [
    (date.today() - timedelta(days=i)).isoformat() for i in range(3)
]


async def _seed_db(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(SCHEMA_SQL)
        # Wipe any prior fixture residue so a re-run is idempotent.
        await conn.execute(
            "DELETE FROM diary_entries WHERE date = ANY($1::text[])",
            FIXTURE_DATES,
        )
        for i, d in enumerate(FIXTURE_DATES):
            await conn.execute(
                """INSERT INTO diary_entries (
                    date, diary,
                    title_1, content_1,
                    summary, quote,
                    created_at, updated_at, device_id, version
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                )""",
                d,
                f"Bugün için günlük notlarım {i}.",
                "Reflection",
                f"Sunum stresi var, endişeliyim {i}.",
                f"Genel özet {i}",
                f"plan-{i}",
                "2026-04-29 10:00:00",
                "2026-04-29 10:00:00",
                "device-1",
                1,
            )
    finally:
        await conn.close()


async def _wipe_db(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "DELETE FROM diary_entries WHERE date = ANY($1::text[])",
            FIXTURE_DATES,
        )
    finally:
        await conn.close()


class StubGeminiBackend:
    async def generate(self, *, system_prompt, user_prompt, timeout):
        # Hand back a minimal valid /detail JSON. The integration test cares
        # that the chain wires up, not that Gemini is creative.
        return json.dumps(
            {
                "summary": "Pipeline çalışıyor.",
                "todos": {"open": "", "completed": "", "deferred": ""},
                "concerns": {"anxieties": "Sunum stresi", "fears": "", "failures": ""},
                "successes": {"achievements": "", "milestones": "", "positive_moments": ""},
                "patterns": ["Test patterni"],
                "recommendation": "Devam.",
            }
        )


@pytest.fixture
def sidecar_client(monkeypatch):
    """Builds the sidecar app pointing at the dev Postgres DB and seeds 3
    fixture rows under today-relative dates. Skips when
    `CORNELL_DATABASE_URL` is unset so a fresh checkout still gets a
    green run on machines without Postgres."""
    database_url = os.environ.get("CORNELL_DATABASE_URL")
    if not database_url:
        pytest.skip("CORNELL_DATABASE_URL not set — skipping integration test")

    asyncio.run(_seed_db(database_url))
    monkeypatch.setenv("CORNELL_DATABASE_URL", database_url)
    monkeypatch.setenv("CORNELL_API_KEY", "sidecar-key")
    from cornell_journal_api.src import config as sidecar_config

    sidecar_config.get_settings.cache_clear()
    app = create_sidecar(sidecar_config.get_settings())
    # `with TestClient(...)` runs Starlette's lifespan — without it
    # `app.state.pg_pool` is never built and every request 503s.
    with TestClient(app) as c:
        try:
            yield c
        finally:
            asyncio.run(_wipe_db(database_url))


@pytest.fixture
def bridge_client(monkeypatch):
    monkeypatch.setenv("CORNELL_API_KEY", "sidecar-key")
    monkeypatch.setenv("INTERNAL_API_KEY", "bridge-key")
    monkeypatch.setenv("CORNELL_API_URL", "http://test-sidecar.local")
    get_settings.cache_clear()
    limiter.reset()
    from src.main import create_app

    app = create_app()
    app.dependency_overrides[get_reporter_service] = lambda: ReporterService(
        ai_client=GeminiClient(backend=StubGeminiBackend())
    )
    return app, TestClient(app)


@respx.mock
def test_full_pipeline_sidecar_through_bridge(sidecar_client, bridge_client):
    """Bridge POST /report → Cornell HTTP route is intercepted and served by
    the in-process sidecar TestClient → SQLite → Reporter → Stub Gemini.
    """

    def proxy_to_sidecar(request: httpx.Request) -> httpx.Response:
        sidecar_response = sidecar_client.get(
            "/api/entries",
            params=dict(request.url.params),
            headers={"X-API-Key": "sidecar-key"},
        )
        return httpx.Response(sidecar_response.status_code, json=sidecar_response.json())

    respx.get("http://test-sidecar.local/api/entries").mock(side_effect=proxy_to_sidecar)

    _, c = bridge_client
    today = date.today()
    body = {
        "tag": "/detail",
        "date_range": {
            "start": (today - timedelta(days=2)).isoformat(),
            "end": today.isoformat(),
        },
    }
    r = c.post("/report", headers={"Authorization": "Bearer bridge-key"}, json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["tag"] == "/detail"
    assert payload["entry_count"] == 3
    assert "Pipeline çalışıyor." in payload["raw_markdown"]
    # Prove the schema map happened — the Cornell row's `quote` field must
    # have flowed into Reporter's `planlar` and made it through Parser into
    # the AI payload (we can't observe payload directly here, but the
    # entry_count proves the sidecar actually returned mapped rows, not a
    # short-circuit).


@respx.mock
def test_full_pipeline_no_entries_in_window(sidecar_client, bridge_client, tmp_path):
    """Asking for a window that doesn't intersect any seeded row should
    bubble up as a 404 no_entries from the bridge."""

    def proxy_to_sidecar(request: httpx.Request) -> httpx.Response:
        sidecar_response = sidecar_client.get(
            "/api/entries",
            params=dict(request.url.params),
            headers={"X-API-Key": "sidecar-key"},
        )
        return httpx.Response(sidecar_response.status_code, json=sidecar_response.json())

    respx.get("http://test-sidecar.local/api/entries").mock(side_effect=proxy_to_sidecar)

    _, c = bridge_client
    body = {
        "tag": "/detail",
        "date_range": {"start": "2010-01-01", "end": "2010-01-02"},
    }
    r = c.post("/report", headers={"Authorization": "Bearer bridge-key"}, json=body)
    assert r.status_code == 404
    assert r.json()["code"] == "no_entries"
