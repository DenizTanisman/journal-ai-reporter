"""FastAPI dependencies — auth and service factories.

Splitting these out keeps `routes.py` focused on HTTP shape and lets tests
override individual dependencies cleanly via `app.dependency_overrides`.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from src.config import Settings, get_settings
from src.modules.converter.service import ConverterService
from src.modules.parser.service import ParserService
from src.modules.reporter.service import ReporterService


def verify_internal_api_key(
    authorization: str | None = Header(None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Validate `Authorization: Bearer <INTERNAL_API_KEY>`.

    We deliberately raise a generic 401 so an attacker can't tell whether
    the header was missing, malformed, or just wrong.
    """
    expected = settings.internal_api_key
    if not expected:
        # Fail closed — refusing to authenticate a request when the server
        # itself wasn't configured beats accidentally allowing them.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "auth_misconfigured", "message": "internal auth not configured"},
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "missing or malformed bearer token"},
        )

    token = authorization[len("Bearer ") :].strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "invalid token"},
        )


def get_converter_service(settings: Settings = Depends(get_settings)) -> ConverterService:
    return ConverterService(settings=settings)


def get_parser_service() -> ParserService:
    return ParserService()


def get_reporter_service(settings: Settings = Depends(get_settings)) -> ReporterService:
    return ReporterService(settings=settings)
