"""ConverterService — fetches raw entries from Cornell and validates them.

Single responsibility: get bytes off the network, parse to `RawEntryCollection`.
No categorization here (that's the Parser).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from src.config import Settings, get_settings
from src.exceptions import ConverterError
from src.logger import get_logger
from src.modules.converter.client import CornellClient
from src.modules.converter.schemas import RawEntry, RawEntryCollection

log = get_logger(__name__)

DEFAULT_LOOKBACK_DAYS = 30


class ConverterService:
    """Pulls and normalizes Cornell journal data."""

    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: Any = CornellClient,
    ) -> None:
        self._settings = settings or get_settings()
        self._client_factory = client_factory

    async def fetch(self, start: date, end: date) -> RawEntryCollection:
        if start > end:
            raise ConverterError("start date must be <= end date")
        return await self._do_fetch(start=start, end=end, fetch_all=False)

    async def fetch_last_days(self, days: int = DEFAULT_LOOKBACK_DAYS) -> RawEntryCollection:
        if days <= 0:
            raise ConverterError("days must be positive")
        end = date.today()
        start = end - timedelta(days=days - 1)
        return await self.fetch(start, end)

    async def fetch_all(self) -> RawEntryCollection:
        return await self._do_fetch(start=None, end=None, fetch_all=True)

    async def _do_fetch(
        self,
        *,
        start: date | None,
        end: date | None,
        fetch_all: bool,
    ) -> RawEntryCollection:
        async with self._client_factory(self._settings) as client:
            payload = await client.fetch_entries(start=start, end=end, fetch_all=fetch_all)

        return self._normalize(payload, start=start, end=end)

    def _normalize(
        self,
        payload: dict[str, Any],
        *,
        start: date | None,
        end: date | None,
    ) -> RawEntryCollection:
        if not isinstance(payload, dict):
            raise ConverterError("Cornell response was not a JSON object")

        raw_entries = payload.get("entries", [])
        if not isinstance(raw_entries, list):
            raise ConverterError("Cornell response 'entries' field was not a list")

        try:
            entries = [RawEntry.model_validate(e) for e in raw_entries]
        except ValidationError as e:
            raise ConverterError("Cornell entry failed schema validation", detail=str(e)) from e

        rng = payload.get("range") or {}
        range_start = _parse_date(rng.get("start")) or start or _min_date(entries)
        range_end = _parse_date(rng.get("end")) or end or _max_date(entries)

        collection = RawEntryCollection(
            entries=entries,
            count=len(entries),
            range_start=range_start,
            range_end=range_end,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        log.info(
            "converter_fetched",
            extra={"endpoint": "converter.fetch", "status": "ok"},
        )
        return collection


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _min_date(entries: list[RawEntry]) -> date | None:
    return min((e.date for e in entries), default=None)


def _max_date(entries: list[RawEntry]) -> date | None:
    return max((e.date for e in entries), default=None)
