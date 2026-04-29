"""Pydantic models for the Parser module.

Output structure mirrors the spec in §5: a hierarchical `fields` tree (todos /
concerns / successes / general, each with sub-buckets) plus a `by_date` index
for day-specific queries the Reporter needs (`/date{...}` tag).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Top-level category names, kept in one place so the categorizer, schema, and
# tests stay in sync.
CategoryName = Literal["todos", "concerns", "successes", "general"]
SubCategoryName = Literal[
    "open",
    "completed",
    "deferred",
    "anxieties",
    "fears",
    "failures",
    "achievements",
    "milestones",
    "positive_moments",
    "reflections",
    "observations",
    "uncategorized",
]


class ParsedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    text: str
    source_entry_id: int


class TodosBucket(BaseModel):
    open: list[ParsedItem] = Field(default_factory=list)
    completed: list[ParsedItem] = Field(default_factory=list)
    deferred: list[ParsedItem] = Field(default_factory=list)


class ConcernsBucket(BaseModel):
    anxieties: list[ParsedItem] = Field(default_factory=list)
    fears: list[ParsedItem] = Field(default_factory=list)
    failures: list[ParsedItem] = Field(default_factory=list)


class SuccessesBucket(BaseModel):
    achievements: list[ParsedItem] = Field(default_factory=list)
    milestones: list[ParsedItem] = Field(default_factory=list)
    positive_moments: list[ParsedItem] = Field(default_factory=list)


class GeneralBucket(BaseModel):
    reflections: list[ParsedItem] = Field(default_factory=list)
    observations: list[ParsedItem] = Field(default_factory=list)
    uncategorized: list[ParsedItem] = Field(default_factory=list)


class FieldsTree(BaseModel):
    todos: TodosBucket = Field(default_factory=TodosBucket)
    concerns: ConcernsBucket = Field(default_factory=ConcernsBucket)
    successes: SuccessesBucket = Field(default_factory=SuccessesBucket)
    general: GeneralBucket = Field(default_factory=GeneralBucket)


class DateRange(BaseModel):
    start: date | None = None
    end: date | None = None


class ParsedMetadata(BaseModel):
    entry_count: int
    date_range: DateRange
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class ParsedCollection(BaseModel):
    """Top-level Parser output. `by_date` is a flat date->FieldsTree index."""

    model_config = ConfigDict(extra="forbid")

    metadata: ParsedMetadata
    fields: FieldsTree = Field(default_factory=FieldsTree)
    by_date: dict[str, FieldsTree] = Field(default_factory=dict)
