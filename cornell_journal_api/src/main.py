"""Cornell Journal API sidecar (Diary FAZ 1.3 — Postgres source).

Exposes a single read-only endpoint:
    GET /api/entries?start=YYYY-MM-DD&end=YYYY-MM-DD&fetch_all=false

Auth via `X-API-Key` header. Rate-limited per remote IP. Defaults to the
last 30 days when no range is given. Diary rows are projected onto the
shape the Journal AI Reporter Converter expects (see README).

The asyncpg pool is built once at app startup and reused for every
request via `app.state.pg_pool`. A pool that fails to build during the
lifespan startup short-circuits the request handler with 503.
"""

# `from __future__ import annotations` intentionally omitted: FastAPI's
# ForwardRef resolution otherwise fails on Query/Header parameter types when
# the test client first imports the app.

from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from cornell_journal_api.src.config import SidecarSettings, get_settings
from cornell_journal_api.src.db import (
    create_pool,
    derive_range,
    fetch_rows,
    row_to_entry_dict,
)


def _verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: SidecarSettings = Depends(get_settings),
) -> None:
    if not settings.cornell_api_key:
        raise HTTPException(status_code=503, detail={"code": "auth_misconfigured"})
    if not x_api_key or x_api_key != settings.cornell_api_key:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})


def create_app(settings: SidecarSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    limiter = Limiter(key_func=get_remote_address)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Lazy: an unreachable Postgres at boot doesn't kill the app —
        # /api/entries handlers degrade to 503 instead. This matches the
        # FileNotFoundError → 503 behaviour the SQLite adapter had.
        try:
            app.state.pg_pool = await create_pool(settings.cornell_database_url)
        except (asyncpg.PostgresError, OSError):
            app.state.pg_pool = None
        try:
            yield
        finally:
            pool = getattr(app.state, "pg_pool", None)
            if pool is not None:
                await pool.close()

    app = FastAPI(title="Cornell Journal API", version="0.2.0", lifespan=lifespan)
    app.state.limiter = limiter
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_methods=["GET"],
        allow_headers=["X-API-Key"],
    )

    @app.exception_handler(RateLimitExceeded)
    async def _rate_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(status_code=429, content={"code": "rate_limit"})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/entries")
    @limiter.limit(f"{settings.rate_limit_per_minute}/minute")
    async def get_entries(
        request: Request,
        start: date | None = Query(None),
        end: date | None = Query(None),
        fetch_all: bool = Query(False),
        _auth: None = Depends(_verify_api_key),
    ) -> dict:
        if start and end and start > end:
            raise HTTPException(status_code=400, detail={"code": "invalid_range"})

        pool: asyncpg.Pool | None = getattr(request.app.state, "pg_pool", None)
        if pool is None:
            raise HTTPException(status_code=503, detail={"code": "db_unavailable"})

        try:
            rows = await fetch_rows(pool, start=start, end=end, fetch_all=fetch_all)
        except (asyncpg.PostgresError, OSError):
            raise HTTPException(status_code=503, detail={"code": "db_unavailable"})

        entries = [row_to_entry_dict(r) for r in rows]
        return {
            "entries": entries,
            "count": len(entries),
            "range": derive_range(entries, start, end),
        }

    return app


app = create_app()
