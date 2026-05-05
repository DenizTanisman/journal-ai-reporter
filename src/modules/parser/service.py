"""ParserService — turns raw journal entries into a categorized tree.

Two paths inside one async API:

- **Legacy** (default): the existing deterministic regex catalogue
  (`categorizer.classify_sentence`). Sync work wrapped in async to
  match the new signature, byte-for-byte the same output.
- **Hybrid** (opt-in via `Settings.hybrid_classifier_enabled`): a
  `HybridClassifier` consults keyword patterns + cache + LLM per
  sentence. See parser.md for the decision tree.

The path is decided at construction time (`hybrid` arg). If `hybrid`
is None, we run legacy; otherwise we delegate every sentence to the
orchestrator.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from src.exceptions import ParserError
from src.logger import get_logger
from src.modules.converter.schemas import RawEntry, RawEntryCollection
from src.modules.parser.categorizer import (
    classify_sentence,
    fallback_subcategory,
    split_sentences,
)
from src.modules.parser.hybrid_classifier import HybridClassifier
from src.modules.parser.schemas import (
    CategoryName,
    DateRange,
    FieldsTree,
    ParsedCollection,
    ParsedItem,
    ParsedMetadata,
    SubCategoryName,
)

log = get_logger(__name__)

# Subcategories the legacy schema accepts. The hybrid LLM may return
# names outside this set (`regrets`, `sadness`, …) — we coerce those
# into `general.uncategorized` rather than crash, since the schema is
# a Literal that pydantic would reject.
_VALID_SUBCATEGORIES = {
    ("todos", "open"),
    ("todos", "completed"),
    ("todos", "deferred"),
    ("concerns", "anxieties"),
    ("concerns", "fears"),
    ("concerns", "failures"),
    ("successes", "achievements"),
    ("successes", "milestones"),
    ("successes", "positive_moments"),
    ("general", "reflections"),
    ("general", "observations"),
    ("general", "uncategorized"),
}


class ParserService:
    """Categorizes raw entries into the Parsed schema."""

    def __init__(self, hybrid: HybridClassifier | None = None) -> None:
        # `hybrid is None` ⇒ legacy keyword-only path.
        self._hybrid = hybrid

    async def parse(self, raw: RawEntryCollection) -> ParsedCollection:
        if not isinstance(raw, RawEntryCollection):
            raise ParserError("ParserService.parse expected RawEntryCollection")

        fields = FieldsTree()
        by_date: dict[str, FieldsTree] = {}

        for entry in raw.entries:
            day_key = entry.date.isoformat()
            day_tree = by_date.setdefault(day_key, FieldsTree())
            await self._categorize_entry(entry, fields, day_tree)

        metadata = ParsedMetadata(
            entry_count=len(raw.entries),
            date_range=DateRange(
                start=raw.range_start or _min_entry_date(raw.entries),
                end=raw.range_end or _max_entry_date(raw.entries),
            ),
        )

        log.info(
            "parser_parsed",
            extra={
                "endpoint": "parser.parse",
                "status": "ok",
                "mode": "hybrid" if self._hybrid else "legacy",
            },
        )
        return ParsedCollection(metadata=metadata, fields=fields, by_date=by_date)

    # ------------------------------------------------------------------
    # internal helpers
    async def _categorize_entry(
        self,
        entry: RawEntry,
        fields: FieldsTree,
        day_tree: FieldsTree,
    ) -> None:
        sentences_seen: set[str] = set()

        for source in (entry.planlar, entry.cue_column, entry.notes_column, entry.summary):
            for sentence in split_sentences(source):
                norm = sentence.strip()
                if not norm or norm in sentences_seen:
                    continue
                sentences_seen.add(norm)

                hits = await self._classify(norm)
                if not hits:
                    hits = [fallback_subcategory(norm)]

                for category, sub in hits:
                    if (category, sub) not in _VALID_SUBCATEGORIES:
                        # Hybrid LLM produced a category outside the
                        # legacy literal — coerce instead of crashing.
                        category, sub = "general", "uncategorized"
                    item = ParsedItem(date=entry.date, text=norm, source_entry_id=entry.id)
                    _append(fields, category, sub, item)
                    _append(day_tree, category, sub, item)

        if not sentences_seen:
            placeholder = ParsedItem(
                date=entry.date,
                text="(empty entry)",
                source_entry_id=entry.id,
            )
            _append(fields, "general", "uncategorized", placeholder)
            _append(day_tree, "general", "uncategorized", placeholder)

    async def _classify(
        self, sentence: str
    ) -> list[tuple[CategoryName, SubCategoryName]]:
        if self._hybrid is not None:
            return await self._hybrid.classify(sentence)
        # Legacy: existing sync catalogue.
        return classify_sentence(sentence)


def _append(
    tree: FieldsTree,
    category: CategoryName,
    sub: SubCategoryName,
    item: ParsedItem,
) -> None:
    bucket = getattr(tree, category)
    bucket_list = getattr(bucket, sub)
    bucket_list.append(item)


def _min_entry_date(entries: Iterable[RawEntry]) -> date | None:
    return min((e.date for e in entries), default=None)


def _max_entry_date(entries: Iterable[RawEntry]) -> date | None:
    return max((e.date for e in entries), default=None)
