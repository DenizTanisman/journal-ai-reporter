"""Unit tests for ConverterService.

httpx is mocked with `respx`. We never hit a real Cornell endpoint here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
import respx

from src.config import get_settings
from src.exceptions import (
    ConverterError,
    CornellAuthError,
    CornellUnavailableError,
)
from src.modules.converter.schemas import RawEntryCollection
from src.modules.converter.service import ConverterService

pytestmark = pytest.mark.unit


def _mock_payload(num_entries: int = 1) -> dict:
    entries = [
        {
            "id": i,
            "date": f"2026-04-{i + 10:02d}",
            "cue_column": f"cue {i}",
            "notes_column": f"notes {i}",
            "summary": f"summary {i}",
            "planlar": f"planlar {i}",
            "created_at": "2026-04-10T08:00:00+00:00",
            "updated_at": "2026-04-10T08:00:00+00:00",
        }
        for i in range(num_entries)
    ]
    return {
        "entries": entries,
        "count": num_entries,
        "range": {"start": "2026-04-10", "end": "2026-04-20"},
    }


@respx.mock
async def test_fetch_range_returns_collection():
    settings = get_settings()
    route = respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_mock_payload(3))
    )

    service = ConverterService(settings=settings)
    result = await service.fetch(date(2026, 4, 1), date(2026, 4, 30))

    assert route.called
    assert isinstance(result, RawEntryCollection)
    assert result.count == 3
    assert len(result.entries) == 3
    assert result.range_start == date(2026, 4, 10)
    assert result.range_end == date(2026, 4, 20)
    assert isinstance(result.fetched_at, datetime)
    assert result.fetched_at.tzinfo == timezone.utc


@respx.mock
async def test_fetch_all_sends_flag():
    settings = get_settings()
    route = respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_mock_payload(0))
    )

    service = ConverterService(settings=settings)
    result = await service.fetch_all()

    assert route.called
    assert route.calls.last.request.url.params["fetch_all"] == "true"
    assert result.count == 0
    assert result.entries == []


@respx.mock
async def test_fetch_last_days_computes_range():
    settings = get_settings()
    route = respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_mock_payload(1))
    )

    service = ConverterService(settings=settings)
    result = await service.fetch_last_days(days=7)

    assert route.called
    params = route.calls.last.request.url.params
    assert "start" in params
    assert "end" in params
    assert result.count == 1


@respx.mock
async def test_empty_response_does_not_raise():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(
            200,
            json={"entries": [], "count": 0, "range": {"start": "2026-04-01", "end": "2026-04-30"}},
        )
    )
    service = ConverterService(settings=settings)
    result = await service.fetch(date(2026, 4, 1), date(2026, 4, 30))
    assert result.count == 0


@respx.mock
async def test_cornell_500_raises_unavailable():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(500, text="boom")
    )
    service = ConverterService(settings=settings)
    with pytest.raises(CornellUnavailableError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


@respx.mock
async def test_cornell_401_raises_auth():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(401, json={"detail": "nope"})
    )
    service = ConverterService(settings=settings)
    with pytest.raises(CornellAuthError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


@respx.mock
async def test_timeout_raises_unavailable():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        side_effect=httpx.TimeoutException("slow")
    )
    service = ConverterService(settings=settings)
    with pytest.raises(CornellUnavailableError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


@respx.mock
async def test_invalid_entry_schema_raises():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(
            200,
            json={"entries": [{"id": "not-an-int"}], "count": 1, "range": {}},
        )
    )
    service = ConverterService(settings=settings)
    with pytest.raises(ConverterError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


async def test_fetch_rejects_inverted_range():
    service = ConverterService()
    with pytest.raises(ConverterError):
        await service.fetch(date(2026, 4, 30), date(2026, 4, 1))


async def test_fetch_last_days_rejects_non_positive():
    service = ConverterService()
    with pytest.raises(ConverterError):
        await service.fetch_last_days(days=0)


@respx.mock
async def test_sends_api_key_header():
    settings = get_settings()
    route = respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=_mock_payload(0))
    )
    service = ConverterService(settings=settings)
    await service.fetch(date(2026, 4, 1), date(2026, 4, 30))
    assert route.calls.last.request.headers.get("x-api-key") == "test-cornell-key"


@respx.mock
async def test_cornell_400_raises_converter_error():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(400, text="bad request")
    )
    service = ConverterService(settings=settings)
    with pytest.raises(ConverterError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


@respx.mock
async def test_non_json_body_raises_converter_error():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    service = ConverterService(settings=settings)
    with pytest.raises(ConverterError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


@respx.mock
async def test_request_error_raises_unavailable():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    service = ConverterService(settings=settings)
    with pytest.raises(CornellUnavailableError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


@respx.mock
async def test_payload_not_dict_raises():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json=["a", "list"])
    )
    service = ConverterService(settings=settings)
    with pytest.raises(ConverterError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


@respx.mock
async def test_entries_not_list_raises():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(200, json={"entries": "nope", "count": 0})
    )
    service = ConverterService(settings=settings)
    with pytest.raises(ConverterError):
        await service.fetch(date(2026, 4, 1), date(2026, 4, 30))


async def test_client_outside_context_manager_raises():
    from src.modules.converter.client import CornellClient

    client = CornellClient()
    with pytest.raises(ConverterError):
        await client.fetch_entries(start=date(2026, 4, 1), end=date(2026, 4, 30))


@respx.mock
async def test_range_falls_back_to_entry_dates_when_payload_missing_range():
    settings = get_settings()
    respx.get(f"{settings.cornell_api_url}/api/entries").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "id": 1,
                        "date": "2026-03-15",
                        "cue_column": "",
                        "notes_column": "",
                        "summary": "",
                        "planlar": "",
                        "created_at": "2026-03-15T08:00:00+00:00",
                        "updated_at": "2026-03-15T08:00:00+00:00",
                    }
                ],
                "count": 1,
            },
        )
    )
    service = ConverterService(settings=settings)
    result = await service.fetch_all()
    assert result.range_start == date(2026, 3, 15)
    assert result.range_end == date(2026, 3, 15)


def test_collection_count_self_corrects_when_mismatched():
    from src.modules.converter.schemas import RawEntry, RawEntryCollection

    entry = RawEntry(
        id=1,
        date=date(2026, 4, 1),
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    coll = RawEntryCollection(entries=[entry], count=99)
    assert coll.count == 1
