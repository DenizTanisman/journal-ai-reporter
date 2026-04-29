"""Cross-cutting HTTP concerns.

Two layers:
- `RequestIdLoggingMiddleware` stamps each request with a uuid and emits a
  start/finish log line carrying only metadata (no body, no headers).
- `journal_reporter_exception_handler` maps domain exceptions to safe HTTP
  responses so handlers can `raise` and never need to know status codes.
"""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.exceptions import JournalReporterError
from src.logger import get_logger

log = get_logger("src.api")


class RequestIdLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.exception(
                "request_unhandled_error",
                extra={
                    "request_id": request_id,
                    "endpoint": request.url.path,
                    "duration_ms": duration_ms,
                    "status": 500,
                },
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "endpoint": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(JournalReporterError)
    async def _domain_handler(request: Request, exc: JournalReporterError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.warning(
            "domain_error",
            extra={
                "request_id": request_id,
                "endpoint": request.url.path,
                "status": exc.http_status,
            },
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message},
            headers={"X-Request-ID": request_id} if request_id else None,
        )
