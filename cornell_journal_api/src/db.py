"""Postgres read-only adapter (Diary FAZ 1.3 sidecar).

Diary moved from SQLite to Postgres in FAZ 1.3. The sidecar now connects
to the same Postgres instance and reads through asyncpg. Read-only is
enforced **operationally** (deploy with a Postgres role that only has
SELECT on `diary_entries`) — Postgres has no per-connection mode=ro flag
the way SQLite did.

The projection (`row_to_entry_dict`) is byte-for-byte the same shape the
SQLite adapter produced, so the Reporter Converter is unchanged.
"""

from __future__ import annotations

import hashlib
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Iterable

import asyncpg


SELECT_BASE = """
    SELECT
        date,
        diary,
        title_1, content_1,
        title_2, content_2,
        title_3, content_3,
        title_4, content_4,
        title_5, content_5,
        title_6, content_6,
        title_7, content_7,
        summary,
        quote,
        created_at,
        updated_at
    FROM diary_entries
"""


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Build a small asyncpg pool (cap 5) pointed at the Diary DB.

    The pool is reused across every `/api/entries` request via FastAPI's
    `app.state` — opening a fresh connection per call would hammer the
    DB on rate-limit-burst windows.
    """
    return await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=5,
        command_timeout=10,
        server_settings={"application_name": "cornell_journal_api"},
    )


async def fetch_rows(
    pool: asyncpg.Pool,
    *,
    start: date_type | None,
    end: date_type | None,
    fetch_all: bool,
) -> list[asyncpg.Record]:
    if fetch_all:
        async with pool.acquire() as conn:
            return await conn.fetch(SELECT_BASE + " ORDER BY date DESC")
    if start is None or end is None:
        end = end or date_type.today()
        start = start or (end - timedelta(days=30))
    async with pool.acquire() as conn:
        return await conn.fetch(
            SELECT_BASE + " WHERE date BETWEEN $1 AND $2 ORDER BY date DESC",
            start.isoformat(),
            end.isoformat(),
        )


def row_to_entry_dict(row: asyncpg.Record) -> dict:
    """Project a Diary Postgres row onto the RawEntry shape.

    asyncpg.Record exposes both indexing by column name and `.get(name)`,
    so the body matches the SQLite adapter we replaced — the Converter
    on the Reporter side sees the exact same JSON.
    """

    cue_pairs: list[str] = []
    for i in range(1, 8):
        title = row[f"title_{i}"] or ""
        content = row[f"content_{i}"] or ""
        if title.strip() or content.strip():
            cue_pairs.append(f"{title.strip()}: {content.strip()}".strip(": ").strip())

    return {
        "id": _stable_id(row["date"]),
        "date": row["date"],
        "cue_column": "\n".join(cue_pairs),
        "notes_column": row["diary"] or "",
        "summary": row["summary"] or "",
        "planlar": row["quote"] or "",
        "created_at": _normalize_ts(row["created_at"]),
        "updated_at": _normalize_ts(row["updated_at"]),
    }


def _stable_id(date_str: str) -> int:
    """Deterministic 32-bit id from the date string. Stable across calls."""
    h = hashlib.sha1(date_str.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big", signed=False)


def _normalize_ts(value: str | None) -> str:
    """Convert Diary's stored timestamp text into RFC3339 UTC.

    Diary stores ISO strings (Postgres column type is TEXT to match the
    SQLite-era schema, kept for migration parity). Falls back to now()
    for malformed values so a single bad row doesn't break the response.
    """
    if not value:
        return datetime.now(tz=timezone.utc).isoformat()
    s = value.strip()
    try:
        if "T" in s:
            return datetime.fromisoformat(s).astimezone(timezone.utc).isoformat()
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return datetime.now(tz=timezone.utc).isoformat()


def derive_range(rows: Iterable[dict], requested_start: date_type | None, requested_end: date_type | None) -> dict:
    """Pick a range to echo back to the client. Prefer the requested values,
    fall back to actual rows."""
    if requested_start and requested_end:
        return {"start": requested_start.isoformat(), "end": requested_end.isoformat()}
    rows_list = list(rows)
    if not rows_list:
        today = date_type.today()
        return {"start": today.isoformat(), "end": today.isoformat()}
    dates = [r["date"] for r in rows_list]
    return {"start": min(dates), "end": max(dates)}
