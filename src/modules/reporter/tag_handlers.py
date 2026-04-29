"""Tag → (template key, payload, markdown renderer) mapping.

Each handler:
  1. picks the slice of ParsedCollection that matters for the tag
  2. names the prompt template key
  3. renders Gemini's JSON output as Turkish markdown for chat clients
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from src.exceptions import DateNotInRangeError, InvalidTagError, NoEntriesError
from src.modules.parser.schemas import FieldsTree, ParsedCollection
from src.modules.reporter.schemas import (
    DateRange,
    TAG_WHITELIST,
    is_valid_tag,
    parse_date_tag,
)


@dataclass(frozen=True)
class HandlerOutput:
    template_key: str
    payload_json: str  # JSON string of the slice the AI should analyze
    date_range: DateRange
    entry_count: int
    markdown_renderer: Callable[[dict[str, Any]], str]


def _fields_to_jsonable(tree: FieldsTree) -> dict[str, Any]:
    return tree.model_dump(mode="json")


def _count_items(tree: FieldsTree) -> int:
    total = 0
    for bucket_name in ("todos", "concerns", "successes", "general"):
        bucket = getattr(tree, bucket_name)
        for sub_name, sub_list in bucket.model_dump().items():
            total += len(sub_list)
    return total


def _section_md(title: str, body: Any) -> str:
    if isinstance(body, list):
        if not body:
            return ""
        items = "\n".join(f"- {x}" for x in body)
        return f"### {title}\n{items}"
    if isinstance(body, dict):
        chunks = [f"**{k}:** {v}" for k, v in body.items() if v]
        if not chunks:
            return ""
        return f"### {title}\n" + "\n".join(chunks)
    if body:
        return f"### {title}\n{body}"
    return ""


def _render_detail(content: dict[str, Any]) -> str:
    parts: list[str] = ["# Günlük Raporu — /detail"]
    if summary := content.get("summary"):
        parts.append(summary)
    parts.append(_section_md("Yapılacaklar", content.get("todos")))
    parts.append(_section_md("Kaygılar", content.get("concerns")))
    parts.append(_section_md("Başarılar", content.get("successes")))
    parts.append(_section_md("Patternler", content.get("patterns")))
    if rec := content.get("recommendation"):
        parts.append(f"### Öneri\n{rec}")
    return "\n\n".join(p for p in parts if p)


def _render_todo(content: dict[str, Any]) -> str:
    parts: list[str] = ["# /todo Raporu"]
    parts.append(_section_md("Açık", content.get("open")))
    parts.append(_section_md("Tamamlanan", content.get("completed")))
    parts.append(_section_md("Ertelenmiş", content.get("deferred")))
    if a := content.get("analysis"):
        parts.append(f"### Analiz\n{a}")
    return "\n\n".join(p for p in parts if p)


def _render_concern(content: dict[str, Any]) -> str:
    parts: list[str] = ["# /concern Raporu"]
    parts.append(_section_md("Kaygılar", content.get("anxieties")))
    parts.append(_section_md("Korkular", content.get("fears")))
    parts.append(_section_md("Başarısızlıklar", content.get("failures")))
    if s := content.get("empathic_summary"):
        parts.append(f"### Özet\n{s}")
    return "\n\n".join(p for p in parts if p)


def _render_success(content: dict[str, Any]) -> str:
    parts: list[str] = ["# /success Raporu"]
    parts.append(_section_md("Başarılar", content.get("achievements")))
    parts.append(_section_md("Kilometre Taşları", content.get("milestones")))
    parts.append(_section_md("Pozitif Anlar", content.get("positive_moments")))
    if s := content.get("celebratory_summary"):
        parts.append(f"### Özet\n{s}")
    return "\n\n".join(p for p in parts if p)


def _render_date(content: dict[str, Any]) -> str:
    parts: list[str] = ["# Gün Raporu"]
    if n := content.get("narrative"):
        parts.append(n)
    parts.append(_section_md("Öne Çıkanlar", content.get("highlights")))
    parts.append(_section_md("Yapılacaklar", content.get("todos")))
    if t := content.get("emotional_tone"):
        parts.append(f"### Duygusal Ton\n{t}")
    return "\n\n".join(p for p in parts if p)


def _slice_for_tag(parsed: ParsedCollection, tag: str) -> dict[str, Any]:
    """Pick the JSON slice we hand to Gemini for the given tag."""
    if tag == "/detail":
        return _fields_to_jsonable(parsed.fields)
    if tag == "/todo":
        return parsed.fields.todos.model_dump(mode="json")
    if tag == "/concern":
        return parsed.fields.concerns.model_dump(mode="json")
    if tag == "/success":
        return parsed.fields.successes.model_dump(mode="json")
    raise InvalidTagError(f"unsupported tag for slicing: {tag}")


def prepare(parsed: ParsedCollection, tag: str) -> HandlerOutput:
    """Translate a tag into the inputs the ReporterService needs."""
    if not is_valid_tag(tag):
        raise InvalidTagError(f"unsupported tag: {tag!r}")

    if parsed.metadata.entry_count == 0:
        raise NoEntriesError("no journal entries to report on")

    if tag == "/detail":
        renderer = _render_detail
    elif tag == "/todo":
        renderer = _render_todo
    elif tag == "/concern":
        renderer = _render_concern
    elif tag == "/success":
        renderer = _render_success
    else:
        renderer = _render_date

    if tag in TAG_WHITELIST:
        slice_payload = _slice_for_tag(parsed, tag)
        date_range = DateRange(
            start=parsed.metadata.date_range.start,
            end=parsed.metadata.date_range.end,
        )
        entry_count = parsed.metadata.entry_count
        template_key = tag
    else:
        target = parse_date_tag(tag)
        if target is None:
            raise InvalidTagError(f"invalid /date tag: {tag!r}")

        day_tree = parsed.by_date.get(target.isoformat())
        if day_tree is None:
            raise DateNotInRangeError(
                f"no entries found for {target.isoformat()}",
                detail=f"available range: {parsed.metadata.date_range.start} → {parsed.metadata.date_range.end}",
            )
        slice_payload = _fields_to_jsonable(day_tree)
        date_range = DateRange(start=target, end=target)
        entry_count = _count_items(day_tree)
        template_key = "/date"

    payload_json = json.dumps(slice_payload, ensure_ascii=False)

    return HandlerOutput(
        template_key=template_key,
        payload_json=payload_json,
        date_range=date_range,
        entry_count=entry_count,
        markdown_renderer=renderer,
    )
