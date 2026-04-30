# Cornell Journal API (Sidecar)

Read-only HTTP sidecar that exposes the Cornell Diary **Postgres** database
as an `/api/entries` endpoint, in the shape the Journal AI Reporter's
Converter expects.

Cornell Diary is a Tauri (Rust + React) desktop/mobile app. As of FAZ 1.3
it stores entries in Postgres (single backend across all platforms — no
SQLite fallback). The sidecar connects to the same Postgres instance via
asyncpg and serves rows back as JSON. **Read-only is enforced
operationally** — deploy with a Postgres role that has only `SELECT`
permission on `diary_entries`. There is no per-connection read-only flag
the way SQLite had.

## Schema mapping

Diary stores one row per day in `diary_entries`, with seven Cornell cue
boxes plus a `diary` notes column, a `summary`, a `quote`, and audit
columns. The sidecar maps each row to the `RawEntry` the Reporter pipeline
expects — identical to the pre-FAZ-1.3 SQLite version:

| RawEntry field   | Source column(s)                                             |
| ---------------- | ------------------------------------------------------------ |
| `id`             | deterministic hash of `date` (stable across calls)           |
| `date`           | `date`                                                       |
| `cue_column`     | concatenation of every `title_i: content_i` pair (non-empty) |
| `notes_column`   | `diary`                                                      |
| `summary`        | `summary`                                                    |
| `planlar`        | `quote` (a free-form field most users repurpose for plans)   |
| `created_at`     | `created_at` parsed as ISO datetime                          |
| `updated_at`     | `updated_at` parsed as ISO datetime                          |

## Running

```bash
CORNELL_DATABASE_URL='postgres://diary_user:change_me_in_dev@127.0.0.1:5435/diary_db' \
CORNELL_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
uvicorn cornell_journal_api.src.main:app --port 8001
```

For production, point `CORNELL_DATABASE_URL` at a SELECT-only role
(`CREATE ROLE reporter_ro LOGIN PASSWORD '...'; GRANT SELECT ON
diary_entries TO reporter_ro;`) so a sidecar bug can't write through.

## Tests

```bash
CORNELL_DATABASE_URL='postgres://...' pytest cornell_journal_api/
```

When `CORNELL_DATABASE_URL` is unset every test that touches Postgres
skips, so a fresh `pytest` on a machine without a DB still gets a clean
run (only the auth + missing-pool tests execute).
