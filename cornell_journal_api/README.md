# Cornell Journal API (Sidecar)

Read-only HTTP sidecar that exposes the Cornell Diary SQLite database as an
`/api/entries` endpoint, in the shape the Journal AI Reporter's Converter
expects.

The Cornell Diary app itself is a Tauri (Rust + React) desktop/mobile
application that owns a single SQLite file. We do not modify it. Instead,
this sidecar opens the same file in read-only mode and serves the rows
back as JSON.

## Schema mapping

Cornell Diary stores one row per day in `diary_entries`, with seven Cornell
cue boxes plus a `diary` notes column, a `summary`, a `quote`, and audit
columns. The sidecar maps each row to the `RawEntry` the Reporter pipeline
expects:

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
CORNELL_DB_PATH=/path/to/cornell-diary.db \
CORNELL_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
uvicorn src.main:app --port 8001
```
