"""End-to-end pipeline test.

Spans both processes that ship in this repo: the Cornell sidecar serves a
real on-disk SQLite file, and the Reporter Bridge runs in the same Python
process via TestClient. Cornell HTTP is stubbed with respx (so the bridge
hits "the sidecar" without us starting a second uvicorn), and Gemini is
swapped out via dependency_overrides.

This catches schema-mapping regressions between the sidecar and the
Reporter that unit tests in either project alone could miss.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

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


# Mirror Cornell's actual shipping schema.
SCHEMA_SQL = """
CREATE TABLE diary_entries (
    date TEXT PRIMARY KEY,
    diary TEXT NOT NULL DEFAULT '',
    title_1 TEXT, content_1 TEXT,
    title_2 TEXT, content_2 TEXT,
    title_3 TEXT, content_3 TEXT,
    title_4 TEXT, content_4 TEXT,
    title_5 TEXT, content_5 TEXT,
    title_6 TEXT, content_6 TEXT,
    title_7 TEXT, content_7 TEXT,
    summary TEXT DEFAULT '',
    quote TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    device_id TEXT,
    version INTEGER NOT NULL DEFAULT 1
);
"""


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    today = date.today()
    rows = [
        (
            (today - timedelta(days=i)).isoformat(),
            f"Bugün için günlük notlarım {i}.",
            "Reflection",
            f"Sunum stresi var, endişeliyim {i}.",
            None, None, None, None, None, None,
            None, None, None, None, None, None,
            f"Genel özet {i}",
            f"plan-{i}",
            "2026-04-29 10:00:00",
            "2026-04-29 10:00:00",
            "device-1",
            1,
        )
        for i in range(3)
    ]
    conn.executemany(
        "INSERT INTO diary_entries VALUES ("
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


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
def sidecar_client(tmp_path: Path, monkeypatch):
    db = tmp_path / "cornell.db"
    _seed_db(db)
    monkeypatch.setenv("CORNELL_DB_PATH", str(db))
    monkeypatch.setenv("CORNELL_API_KEY", "sidecar-key")
    from cornell_journal_api.src import config as sidecar_config

    sidecar_config.get_settings.cache_clear()
    app = create_sidecar(sidecar_config.get_settings())
    return TestClient(app)


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
