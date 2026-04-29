"""Public HTTP surface — the Jarvis Bridge.

POST /report runs the full pipeline (Converter + Parser + Reporter). The
client sends a tag plus an optional date_range or fetch_all=true; we return
the structured ReportResponse.

Two convenience endpoints:
- GET /tags — what the bridge accepts
- POST /report/file — upload a pre-built ParsedCollection JSON and run only
  the Reporter. Useful for offline debugging when Cornell is unreachable.
"""

# NOTE: `from __future__ import annotations` is intentionally omitted. With it,
# FastAPI's ForwardRef resolution for body/parameter types fails when modules
# are imported via the test client (PydanticUndefinedAnnotation: ReportRequest).

from datetime import date as date_type, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from src.api.dependencies import (
    get_converter_service,
    get_parser_service,
    get_reporter_service,
    verify_internal_api_key,
)
from src.api.limiter import limiter, report_rate_limit
from src.exceptions import (
    InvalidTagError,
    NoEntriesError,
    ParserError,
)
from src.modules.converter.schemas import RawEntryCollection
from src.modules.converter.service import ConverterService
from src.modules.parser.schemas import ParsedCollection
from src.modules.parser.service import ParserService
from src.modules.reporter.schemas import (
    DateRange,
    ReportRequest,
    ReportResponse,
    TAG_WHITELIST,
)
from src.modules.reporter.service import ReporterService

router = APIRouter(dependencies=[Depends(verify_internal_api_key)])


class _TagsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    whitelist: list[str]
    date_pattern: str = Field(
        default="/date{dd.mm.yyyy}",
        description="Date-tag template; replace dd.mm.yyyy with a real calendar date in range.",
    )


@router.get("/tags", response_model=_TagsResponse)
async def supported_tags() -> _TagsResponse:
    return _TagsResponse(whitelist=list(TAG_WHITELIST))


@router.post("/report", response_model=ReportResponse)
@limiter.limit(report_rate_limit)
async def generate_report(
    request: Request,
    body: ReportRequest,
    converter: Annotated[ConverterService, Depends(get_converter_service)],
    parser: Annotated[ParserService, Depends(get_parser_service)],
    reporter: Annotated[ReporterService, Depends(get_reporter_service)],
) -> ReportResponse:
    raw = await _fetch_raw(converter, body)
    parsed = parser.parse(raw)

    if parsed.metadata.entry_count == 0:
        raise NoEntriesError("no journal entries available for the requested range")

    return await reporter.generate(parsed, body.tag)


@router.post("/report/file", response_model=ReportResponse)
async def generate_report_from_file(
    tag: str,
    parsed_file: UploadFile = File(..., alias="parsed"),
    reporter: ReporterService = Depends(get_reporter_service),
) -> ReportResponse:
    raw_bytes = await parsed_file.read()
    if len(raw_bytes) > 5 * 1024 * 1024:
        # Refuse oversized uploads early; Reporter prompts have practical limits
        # and we don't want to ship unbounded user payloads to Gemini.
        raise InvalidTagError("uploaded file exceeds 5MB limit")
    try:
        parsed = ParsedCollection.model_validate_json(raw_bytes)
    except Exception as e:  # pragma: no cover — pydantic raises ValidationError
        raise ParserError("uploaded JSON failed schema validation", detail=str(e)[:200]) from e
    return await reporter.generate(parsed, tag)


async def _fetch_raw(converter: ConverterService, body: ReportRequest) -> RawEntryCollection:
    """Apply the request's range semantics to the Converter."""
    if body.fetch_all:
        return await converter.fetch_all()

    rng = body.date_range
    if rng and rng.start and rng.end:
        return await converter.fetch(rng.start, rng.end)
    if rng and (rng.start or rng.end):
        # Partial range: fill the missing side with a 30-day window.
        end = rng.end or date_type.today()
        start = rng.start or (end - timedelta(days=30))
        return await converter.fetch(start, end)
    return await converter.fetch_last_days(days=30)


__all__ = [
    "router",
    "ReportRequest",
    "ReportResponse",
    "DateRange",
]
