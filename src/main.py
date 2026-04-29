"""FastAPI entry point.

Wires the lifespan, CORS whitelist, request-id logger, exception handler,
slowapi rate limiter, and the Jarvis Bridge router. `/health` and `/tags`
deliberately stay fast paths (no DB, no AI) so liveness probes don't compete
with rate-limited /report calls.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from src import __version__
from src.api.limiter import limiter
from src.api.middleware import RequestIdLoggingMiddleware, register_exception_handlers
from src.api.routes import router as bridge_router
from src.config import get_settings
from src.logger import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log = get_logger(__name__)
    log.info("application_startup", extra={"endpoint": "lifespan"})
    yield
    log.info("application_shutdown", extra={"endpoint": "lifespan"})


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Journal AI Reporter",
        version=__version__,
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"code": "rate_limit", "message": "too many requests"},
        )

    app.add_middleware(RequestIdLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    register_exception_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.app_env}

    app.include_router(bridge_router)
    return app


app = create_app()
