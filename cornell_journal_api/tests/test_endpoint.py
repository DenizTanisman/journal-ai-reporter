"""Tests for the Cornell Journal API sidecar.

We build a real on-disk SQLite file matching the Cornell Diary schema, point
the sidecar at it, and exercise auth, the schema mapping, range filtering,
fetch_all, and the read-only safety guarantee.
"""

from __future__ import annotations

import importlib
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Cornell schema mirrored from cornell-diary/src-tauri/migrations/001_initial.sql
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS diary_entries (
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


@pytest.fixture
def sidecar_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "cornell.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)

    today = date.today()
    rows = [
        (
            (today - timedelta(days=i)).isoformat(),
            f"Bugün diary {i}",
            "Reflection", f"Cue notu {i}",
            None, None, None, None, None, None, None, None, None, None, None, None,
            f"summary {i}",
            f"plan {i}",
            "2026-04-29 10:00:00",
            "2026-04-29 10:00:00",
            "device-1",
            1,
        )
        for i in range(3)
    ]
    conn.executemany(
        """INSERT INTO diary_entries VALUES (
            ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )""",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(sidecar_db, monkeypatch):
    monkeypatch.setenv("CORNELL_DB_PATH", str(sidecar_db))
    monkeypatch.setenv("CORNELL_API_KEY", "test-key")

    # Reload config + main so the new env wins.
    from cornell_journal_api.src import config as config_mod
    from cornell_journal_api.src import main as main_mod

    config_mod.get_settings.cache_clear()
    importlib.reload(main_mod)
    main_mod.app.state.limiter.reset()
    return TestClient(main_mod.app)


def test_health_open(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_entries_requires_api_key(client):
    r = client.get("/api/entries")
    assert r.status_code == 401


def test_entries_wrong_api_key(client):
    r = client.get("/api/entries", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_entries_default_returns_recent(client):
    r = client.get("/api/entries", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["count"] == 3
    entry = payload["entries"][0]
    # Verify schema mapping
    for k in ("id", "date", "cue_column", "notes_column", "summary", "planlar", "created_at", "updated_at"):
        assert k in entry
    assert "Reflection" in entry["cue_column"]
    assert entry["notes_column"].startswith("Bugün diary")
    assert entry["planlar"].startswith("plan ")


def test_entries_range_filter(client):
    today = date.today()
    r = client.get(
        "/api/entries",
        headers={"X-API-Key": "test-key"},
        params={"start": today.isoformat(), "end": today.isoformat()},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_entries_inverted_range_400(client):
    today = date.today()
    r = client.get(
        "/api/entries",
        headers={"X-API-Key": "test-key"},
        params={"start": (today + timedelta(days=10)).isoformat(), "end": today.isoformat()},
    )
    assert r.status_code == 400


def test_entries_fetch_all(client):
    r = client.get(
        "/api/entries",
        headers={"X-API-Key": "test-key"},
        params={"fetch_all": "true"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 3


def test_entries_id_is_stable(client):
    r1 = client.get("/api/entries", headers={"X-API-Key": "test-key"}).json()
    r2 = client.get("/api/entries", headers={"X-API-Key": "test-key"}).json()
    assert [e["id"] for e in r1["entries"]] == [e["id"] for e in r2["entries"]]


def test_db_missing_returns_503(monkeypatch, tmp_path):
    monkeypatch.setenv("CORNELL_DB_PATH", str(tmp_path / "nope.db"))
    monkeypatch.setenv("CORNELL_API_KEY", "test-key")
    from cornell_journal_api.src import config as config_mod
    from cornell_journal_api.src import main as main_mod

    config_mod.get_settings.cache_clear()
    importlib.reload(main_mod)
    c = TestClient(main_mod.app)
    r = c.get("/api/entries", headers={"X-API-Key": "test-key"})
    assert r.status_code == 503


def test_readonly_mode_rejects_writes(sidecar_db):
    """Direct sanity check on the adapter — proves writes really are blocked."""
    from cornell_journal_api.src.db import open_readonly

    conn = open_readonly(str(sidecar_db))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO diary_entries (date, diary, created_at, updated_at) "
                     "VALUES ('1999-01-01', 'x', 'now', 'now')")
    conn.close()
