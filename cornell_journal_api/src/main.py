"""Cornell Journal API sidecar.

Exposes a single read-only endpoint:
    GET /api/entries?start=YYYY-MM-DD&end=YYYY-MM-DD&fetch_all=false

Auth via `X-API-Key` header. Rate-limited per remote IP. Defaults to the
last 30 days when no range is given. Cornell rows are projected onto the
shape the Journal AI Reporter Converter expects (see README).
"""

# `from __future__ import annotations` intentionally omitted: FastAPI's
# ForwardRef resolution otherwise fails on Query/Header parameter types when
# the test client first imports the app.

from datetime import date
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from cornell_journal_api.src.config import SidecarSettings, get_settings
from cornell_journal_api.src.db import (
    derive_range,
    fetch_rows,
    open_readonly,
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

    app = FastAPI(title="Cornell Journal API", version="0.1.0")
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

        try:
            conn = open_readonly(settings.cornell_db_path)
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail={"code": "db_unavailable"})

        try:
            rows = fetch_rows(conn, start=start, end=end, fetch_all=fetch_all)
        finally:
            conn.close()

        entries = [row_to_entry_dict(r) for r in rows]
        return {
            "entries": entries,
            "count": len(entries),
            "range": derive_range(entries, start, end),
        }

    return app


app = create_app()
