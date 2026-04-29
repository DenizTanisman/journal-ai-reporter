"""Pydantic models for the Reporter module.

Request models validate tag syntax (whitelist + /date{dd.mm.yyyy} regex).
Response models keep raw markdown beside the structured content so callers
can render either form.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Whitelisted base tags. /date{...} validates separately.
TAG_WHITELIST: tuple[str, ...] = ("/detail", "/todo", "/concern", "/success")
DATE_TAG_PATTERN = re.compile(r"^/date\{(\d{2})\.(\d{2})\.(\d{4})\}$")


def is_valid_tag(tag: str) -> bool:
    if tag in TAG_WHITELIST:
        return True
    return DATE_TAG_PATTERN.match(tag) is not None


class DateRange(BaseModel):
    start: date | None = None
    end: date | None = None


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    date_range: DateRange | None = None
    fetch_all: bool = False

    @field_validator("tag")
    @classmethod
    def _validate_tag(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_tag(v):
            raise ValueError(f"unsupported tag: {v!r}")
        return v


def parse_date_tag(tag: str) -> date | None:
    """Return the date encoded in `/date{dd.mm.yyyy}`, else None."""
    m = DATE_TAG_PATTERN.match(tag)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    try:
        return date(int(yyyy), int(mm), int(dd))
    except ValueError:
        return None


class ReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    date_range: DateRange
    entry_count: int
    content: dict[str, Any]
    raw_markdown: str
