"""SQLite read-only adapter.

Opens the Cornell Diary database with `mode=ro&immutable=1` so we cannot
accidentally write through. Cornell Diary's schema is fixed
(`diary_entries`), and queries use parameter placeholders — no string
interpolation of user input ever reaches SQL.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


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


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open the Cornell SQLite file read-only.

    `mode=ro` enforces read-only at the URI level (writes raise
    OperationalError, verified in tests) but still tracks file changes —
    crucial because Cornell Diary writes to this same file from the Tauri
    app while we read. We previously used `immutable=1` which is a
    performance hint that promises the file won't change; SQLite then
    skips change detection entirely and the sidecar serves a stale view
    until restart. Tauri's WAL mode handles concurrent reads safely, so
    `mode=ro` alone is the right level.
    """
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"Cornell DB not found at {db_path}")
    uri = f"file:{p.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_rows(
    conn: sqlite3.Connection,
    *,
    start: date_type | None,
    end: date_type | None,
    fetch_all: bool,
) -> list[sqlite3.Row]:
    if fetch_all:
        cur = conn.execute(SELECT_BASE + " ORDER BY date DESC")
    else:
        if start is None or end is None:
            end = end or date_type.today()
            start = start or (end - timedelta(days=30))
        cur = conn.execute(
            SELECT_BASE + " WHERE date BETWEEN ? AND ? ORDER BY date DESC",
            (start.isoformat(), end.isoformat()),
        )
    return cur.fetchall()


def row_to_entry_dict(row: sqlite3.Row) -> dict:
    """Project a Cornell row onto the RawEntry shape used by the Reporter."""

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
    """Convert SQLite timestamp text into RFC3339 UTC, defaulting to now()."""
    if not value:
        return datetime.now(tz=timezone.utc).isoformat()
    # Cornell stores `datetime('now')`-style values, e.g. '2026-04-29 10:30:00'
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
