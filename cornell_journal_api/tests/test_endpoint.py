"""Tests for the Cornell Journal API sidecar (Postgres source).

Diary FAZ 1.3 moved storage to Postgres, so these tests connect to a
real DB through asyncpg, seed three fixture rows under a far-future
date prefix that won't collide with real data, exercise auth + the
schema mapping + range filtering + fetch_all + the pool-down 503
path, and clean up after themselves.

Gated on `CORNELL_DATABASE_URL` — when unset the suite is skipped so
fresh checkouts on machines without Postgres still get a green run.
"""

from __future__ import annotations

import importlib
import os
from datetime import date, timedelta
from typing import Iterator

import asyncpg
import pytest
from fastapi.testclient import TestClient


# Schema mirrored from cornell-diary/src-tauri/postgres_migrations/0001_initial.sql.
# We only need the columns the sidecar reads; the real Diary schema has more.
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

# Future date so we never collide with the user's real diary content.
# Pick mid-year so subtracting a few days never wraps into 2998 and
# escapes our cleanup pattern.
FIXTURE_BASE = date(2999, 6, 15)
FIXTURE_PREFIX = "2999-"


def _database_url() -> str | None:
    url = os.environ.get("CORNELL_DATABASE_URL")
    return url if url else None


@pytest.fixture
def database_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("CORNELL_DATABASE_URL not set — skipping Postgres-backed tests")
    return url


@pytest.fixture
def seeded_db(database_url: str) -> Iterator[str]:
    """Inserts three fixture rows under year-2999 dates, yields the DB
    URL, then deletes the fixture rows on teardown so the suite is
    re-runnable against a shared dev DB."""
    import asyncio

    async def setup():
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute(SCHEMA_SQL)
            # Make sure no leftovers from a previous failed run survive.
            await conn.execute(
                "DELETE FROM diary_entries WHERE date LIKE $1", f"{FIXTURE_PREFIX}%"
            )
            for i in range(3):
                d = (FIXTURE_BASE - timedelta(days=i)).isoformat()
                await conn.execute(
                    """INSERT INTO diary_entries (
                        date, diary,
                        title_1, content_1,
                        summary, quote,
                        created_at, updated_at, device_id, version
                    ) VALUES (
                        $1, $2,
                        $3, $4,
                        $5, $6,
                        $7, $8, $9, $10
                    )""",
                    d,
                    f"Bugün diary {i}",
                    "Reflection",
                    f"Cue notu {i}",
                    f"summary {i}",
                    f"plan {i}",
                    "2026-04-29 10:00:00",
                    "2026-04-29 10:00:00",
                    "device-1",
                    1,
                )
        finally:
            await conn.close()

    async def cleanup():
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute(
                "DELETE FROM diary_entries WHERE date LIKE $1", f"{FIXTURE_PREFIX}%"
            )
        finally:
            await conn.close()

    # asyncio.run() always builds a fresh loop and tears it down — avoids
    # the "no current event loop" deprecation pytest-asyncio's auto mode
    # raises for sync fixtures touching async code.
    asyncio.run(setup())
    try:
        yield database_url
    finally:
        asyncio.run(cleanup())


@pytest.fixture
def client(seeded_db: str, monkeypatch) -> Iterator[TestClient]:
    """Yields a TestClient inside a `with` block so Starlette's lifespan
    fires (and our async pool is built). Without the context manager
    `app.state.pg_pool` is never set and every request 503s."""
    monkeypatch.setenv("CORNELL_DATABASE_URL", seeded_db)
    monkeypatch.setenv("CORNELL_API_KEY", "test-key")

    from cornell_journal_api.src import config as config_mod
    from cornell_journal_api.src import main as main_mod

    config_mod.get_settings.cache_clear()
    importlib.reload(main_mod)
    main_mod.app.state.limiter.reset()
    with TestClient(main_mod.app) as c:
        yield c


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
    """Default range is "last 30 days" — our fixtures are dated 2999, so
    the default returns 0. We test the range-filtered case explicitly."""
    r = client.get(
        "/api/entries",
        headers={"X-API-Key": "test-key"},
        params={
            "start": (FIXTURE_BASE - timedelta(days=10)).isoformat(),
            "end": FIXTURE_BASE.isoformat(),
        },
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["count"] == 3
    entry = payload["entries"][0]
    for k in ("id", "date", "cue_column", "notes_column", "summary", "planlar", "created_at", "updated_at"):
        assert k in entry
    assert "Reflection" in entry["cue_column"]
    assert entry["notes_column"].startswith("Bugün diary")
    assert entry["planlar"].startswith("plan ")


def test_entries_range_filter(client):
    r = client.get(
        "/api/entries",
        headers={"X-API-Key": "test-key"},
        params={
            "start": FIXTURE_BASE.isoformat(),
            "end": FIXTURE_BASE.isoformat(),
        },
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_entries_inverted_range_400(client):
    today = date.today()
    r = client.get(
        "/api/entries",
        headers={"X-API-Key": "test-key"},
        params={
            "start": (today + timedelta(days=10)).isoformat(),
            "end": today.isoformat(),
        },
    )
    assert r.status_code == 400


def test_entries_fetch_all(client):
    r = client.get(
        "/api/entries",
        headers={"X-API-Key": "test-key"},
        params={"fetch_all": "true"},
    )
    assert r.status_code == 200
    # fetch_all returns every row in the DB; assert we at least see our 3
    # fixture rows (other tests / real data may add more).
    payload = r.json()
    fixture_dates = {(FIXTURE_BASE - timedelta(days=i)).isoformat() for i in range(3)}
    seen = {e["date"] for e in payload["entries"]}
    assert fixture_dates.issubset(seen), f"missing fixtures in response: {fixture_dates - seen}"


def test_entries_id_is_stable(client):
    """The deterministic id derived from the date must be identical
    across calls, so the Reporter Converter can dedupe."""
    params = {
        "start": (FIXTURE_BASE - timedelta(days=10)).isoformat(),
        "end": FIXTURE_BASE.isoformat(),
    }
    r1 = client.get("/api/entries", headers={"X-API-Key": "test-key"}, params=params).json()
    r2 = client.get("/api/entries", headers={"X-API-Key": "test-key"}, params=params).json()
    assert [e["id"] for e in r1["entries"]] == [e["id"] for e in r2["entries"]]


def test_db_unreachable_returns_503(monkeypatch):
    """If the asyncpg pool fails to build at startup, /api/entries 503s
    instead of crashing the app."""
    # Port 1 is reserved (TCP port multiplexer); a connect there always fails.
    monkeypatch.setenv(
        "CORNELL_DATABASE_URL", "postgres://noone@127.0.0.1:1/nope"
    )
    monkeypatch.setenv("CORNELL_API_KEY", "test-key")
    from cornell_journal_api.src import config as config_mod
    from cornell_journal_api.src import main as main_mod

    config_mod.get_settings.cache_clear()
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        r = c.get("/api/entries", headers={"X-API-Key": "test-key"})
    assert r.status_code == 503
