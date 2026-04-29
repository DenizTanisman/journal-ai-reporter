"""Pydantic models for the Converter module.

`RawEntry` mirrors the Cornell `/api/entries` row exactly. `RawEntryCollection`
wraps a fetch result with metadata (range, count, fetched_at).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RawEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    date: date
    cue_column: str = ""
    notes_column: str = ""
    summary: str = ""
    planlar: str = ""
    created_at: datetime
    updated_at: datetime


class RawEntryCollection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entries: list[RawEntry] = Field(default_factory=list)
    count: int = 0
    range_start: date | None = None
    range_end: date | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="after")
    def _sync_count(self) -> Self:
        # The endpoint sends `count`, but if it's missing or stale we trust the list.
        if self.count != len(self.entries):
            self.count = len(self.entries)
        return self
