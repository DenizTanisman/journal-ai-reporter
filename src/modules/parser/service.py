"""ParserService — turns raw journal entries into a categorized tree.

Deterministic. No network, no AI. Input is the Converter's output schema;
output is `ParsedCollection` ready for the Reporter to slice by tag.
"""

from __future__ import annotations

from datetime import date

from src.exceptions import ParserError
from src.logger import get_logger
from src.modules.converter.schemas import RawEntry, RawEntryCollection
from src.modules.parser.categorizer import (
    classify_sentence,
    fallback_subcategory,
    split_sentences,
)
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


class ParserService:
    """Categorizes raw entries into the Parsed schema."""

    def parse(self, raw: RawEntryCollection) -> ParsedCollection:
        if not isinstance(raw, RawEntryCollection):
            raise ParserError("ParserService.parse expected RawEntryCollection")

        fields = FieldsTree()
        by_date: dict[str, FieldsTree] = {}

        for entry in raw.entries:
            day_key = entry.date.isoformat()
            day_tree = by_date.setdefault(day_key, FieldsTree())
            self._categorize_entry(entry, fields, day_tree)

        metadata = ParsedMetadata(
            entry_count=len(raw.entries),
            date_range=DateRange(
                start=raw.range_start or _min_entry_date(raw.entries),
                end=raw.range_end or _max_entry_date(raw.entries),
            ),
        )

        log.info(
            "parser_parsed",
            extra={"endpoint": "parser.parse", "status": "ok"},
        )
        return ParsedCollection(metadata=metadata, fields=fields, by_date=by_date)

    # ------------------------------------------------------------------
    # internal helpers
    def _categorize_entry(
        self,
        entry: RawEntry,
        fields: FieldsTree,
        day_tree: FieldsTree,
    ) -> None:
        sentences_seen: set[str] = set()

        # The four Cornell columns are concatenated into one stream so we can
        # categorize them uniformly. We process planlar separately *first*
        # because checkbox markers are most reliable there.
        for source in (entry.planlar, entry.cue_column, entry.notes_column, entry.summary):
            for sentence in split_sentences(source):
                norm = sentence.strip()
                if not norm or norm in sentences_seen:
                    continue
                sentences_seen.add(norm)

                hits = classify_sentence(norm)
                if not hits:
                    hits = [fallback_subcategory(norm)]

                for category, sub in hits:
                    item = ParsedItem(date=entry.date, text=norm, source_entry_id=entry.id)
                    _append(fields, category, sub, item)
                    _append(day_tree, category, sub, item)

        # Guarantee the entry isn't lost even if every column was empty.
        if not sentences_seen:
            placeholder = ParsedItem(
                date=entry.date,
                text="(empty entry)",
                source_entry_id=entry.id,
            )
            _append(fields, "general", "uncategorized", placeholder)
            _append(day_tree, "general", "uncategorized", placeholder)


def _append(
    tree: FieldsTree,
    category: CategoryName,
    sub: SubCategoryName,
    item: ParsedItem,
) -> None:
    bucket = getattr(tree, category)
    bucket_list = getattr(bucket, sub)
    bucket_list.append(item)


def _min_entry_date(entries: list[RawEntry]) -> date | None:
    return min((e.date for e in entries), default=None)


def _max_entry_date(entries: list[RawEntry]) -> date | None:
    return max((e.date for e in entries), default=None)
