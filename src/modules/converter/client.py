"""HTTP client for the Cornell Journal `/api/entries` endpoint.

Thin wrapper around `httpx.AsyncClient`. Adds auth header, enforces timeout,
and translates transport errors into our domain exceptions so callers never
see raw httpx errors.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from src.config import Settings, get_settings
from src.exceptions import CornellAuthError, CornellUnavailableError, ConverterError
from src.logger import get_logger

log = get_logger(__name__)


class CornellClient:
    """Async client for Cornell journal endpoint. Use as `async with`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "CornellClient":
        self._client = httpx.AsyncClient(
            base_url=self._settings.cornell_api_url,
            timeout=self._settings.http_timeout_seconds,
            headers={"X-API-Key": self._settings.cornell_api_key} if self._settings.cornell_api_key else {},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_entries(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        fetch_all: bool = False,
    ) -> dict[str, Any]:
        if self._client is None:
            raise ConverterError("CornellClient must be used inside `async with`")

        params: dict[str, Any] = {}
        if fetch_all:
            params["fetch_all"] = "true"
        else:
            if start is not None:
                params["start"] = start.isoformat()
            if end is not None:
                params["end"] = end.isoformat()

        try:
            response = await self._client.get("/api/entries", params=params)
        except httpx.TimeoutException as e:
            log.warning("cornell_timeout", extra={"endpoint": "/api/entries"})
            raise CornellUnavailableError("Cornell endpoint timed out", detail=str(e)) from e
        except httpx.RequestError as e:
            log.warning("cornell_unreachable", extra={"endpoint": "/api/entries"})
            raise CornellUnavailableError("Cornell endpoint unreachable", detail=str(e)) from e

        if response.status_code == 401 or response.status_code == 403:
            raise CornellAuthError(f"Cornell endpoint rejected credentials ({response.status_code})")
        if response.status_code >= 500:
            raise CornellUnavailableError(f"Cornell endpoint returned {response.status_code}")
        if response.status_code >= 400:
            raise ConverterError(
                f"Cornell endpoint client error ({response.status_code})",
                detail=response.text[:200],
            )

        try:
            return response.json()
        except ValueError as e:
            raise ConverterError("Cornell endpoint returned non-JSON body", detail=str(e)) from e
