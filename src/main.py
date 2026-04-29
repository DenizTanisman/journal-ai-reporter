"""FastAPI entry point.

Phase 0 keeps this minimal: just `/health` so we can verify the skeleton boots.
Module routes (Jarvis Bridge) plug in starting Phase 4.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import __version__
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.app_env}

    return app


app = create_app()
