"""Unit tests for ParserService and the categorizer.

Each subcategory gets at least two examples (the prompt's acceptance criterion).
We also assert no entry is silently dropped, and `by_date` mirrors `fields`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.exceptions import ParserError
from src.modules.converter.schemas import RawEntry, RawEntryCollection
from src.modules.parser.categorizer import (
    classify_sentence,
    fallback_subcategory,
    split_sentences,
)
from src.modules.parser.service import ParserService

pytestmark = pytest.mark.unit


def _entry(
    *,
    eid: int = 1,
    d: date = date(2026, 4, 15),
    cue: str = "",
    notes: str = "",
    summary: str = "",
    planlar: str = "",
) -> RawEntry:
    ts = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    return RawEntry(
        id=eid,
        date=d,
        cue_column=cue,
        notes_column=notes,
        summary=summary,
        planlar=planlar,
        created_at=ts,
        updated_at=ts,
    )


def _collection(entries: list[RawEntry]) -> RawEntryCollection:
    return RawEntryCollection(
        entries=entries,
        count=len(entries),
        range_start=min((e.date for e in entries), default=None),
        range_end=max((e.date for e in entries), default=None),
    )


# ---------------------------------------------------------------------------
# split_sentences / classify_sentence — categorizer level
# ---------------------------------------------------------------------------
def test_split_sentences_keeps_checkbox_lines_whole():
    out = split_sentences("[ ] Endpoint yaz\n[x] Sunum")
    assert "[ ] Endpoint yaz" in out
    assert "[x] Sunum" in out


def test_split_sentences_handles_punctuation():
    out = split_sentences("Bugün başardım! Yarın daha iyi olacak. Devam.")
    assert out == ["Bugün başardım!", "Yarın daha iyi olacak.", "Devam."]


def test_split_sentences_empty_returns_empty():
    assert split_sentences("") == []
    assert split_sentences("   \n  \n") == []


def test_classify_sentence_multi_match():
    hits = classify_sentence("İlk kez başardım ama hala endişeliyim")
    cats = {sub for _, sub in hits}
    assert "milestones" in cats
    assert "achievements" in cats
    assert "anxieties" in cats


def test_classify_sentence_no_match_returns_empty():
    assert classify_sentence("Bugün hava güneşliydi") == []


def test_fallback_subcategory_long_is_reflection():
    long_text = "x" * 80
    assert fallback_subcategory(long_text) == ("general", "reflections")


def test_fallback_subcategory_short_is_observation():
    assert fallback_subcategory("Hava güzeldi") == ("general", "observations")


def test_fallback_subcategory_empty_is_uncategorized():
    assert fallback_subcategory("   ") == ("general", "uncategorized")


# ---------------------------------------------------------------------------
# ParserService — full categorization, two examples per bucket
# ---------------------------------------------------------------------------
async def test_todos_open_two_examples():
    raw = _collection(
        [
            _entry(eid=1, planlar="[ ] Sunum hazırla"),
            _entry(eid=2, notes="Yarın endpoint yapacağım."),
        ]
    )
    out = await ParserService().parse(raw)
    texts = [it.text for it in out.fields.todos.open]
    assert any("[ ] Sunum hazırla" in t for t in texts)
    assert any("yapacağım" in t.lower() for t in texts)


async def test_todos_completed_two_examples():
    raw = _collection(
        [
            _entry(eid=1, planlar="[x] Sunum"),
            _entry(eid=2, notes="Ödevi bitirdim, harika hissediyorum."),
        ]
    )
    out = await ParserService().parse(raw)
    texts = [it.text for it in out.fields.todos.completed]
    assert any("[x] Sunum" in t for t in texts)
    assert any("bitirdim" in t.lower() for t in texts)


async def test_todos_deferred_two_examples():
    raw = _collection(
        [
            _entry(eid=1, planlar="Prova ertelendi."),
            _entry(eid=2, notes="Bu işi yarına bıraktım."),
        ]
    )
    out = await ParserService().parse(raw)
    texts = " | ".join(it.text.lower() for it in out.fields.todos.deferred)
    assert "ertelendi" in texts
    assert "yarına" in texts


async def test_concerns_anxieties_two_examples():
    raw = _collection(
        [
            _entry(eid=1, notes="Sunum stresi yüzünden uyuyamadım."),
            _entry(eid=2, notes="Performans değerlendirmesinden endişeliyim."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.concerns.anxieties) >= 2


async def test_concerns_fears_two_examples():
    raw = _collection(
        [
            _entry(eid=1, notes="Yeni projeden korkuyorum."),
            _entry(eid=2, notes="Bu konuşmayı yapmak korkutucu geliyor."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.concerns.fears) >= 2


async def test_concerns_fears_catches_inflected_stems():
    """Regression: previously 'korkarım' (-arım form) wasn't in the keyword
    list, so live Cornell entries like 'ben aslandan korkarım' fell into
    general/uncategorized and /concern came back empty even though /detail
    surfaced them via Gemini's reading of general."""
    raw = _collection(
        [
            _entry(eid=1, notes="ben aslandan korkarım"),
            _entry(eid=2, notes="Karanlıktan korkarsın bazen."),
            _entry(eid=3, notes="Ürkütücü bir his var."),
        ]
    )
    out = await ParserService().parse(raw)
    fears_texts = " | ".join(it.text.lower() for it in out.fields.concerns.fears)
    assert "aslandan korkarım" in fears_texts
    assert "korkarsın" in fears_texts
    assert "ürkütücü" in fears_texts


async def test_concerns_anxieties_catches_inflected_stems():
    raw = _collection(
        [
            _entry(eid=1, notes="Endişem büyüyor."),
            _entry(eid=2, notes="Kaygılarım var bugün."),
            _entry(eid=3, notes="Stresliyiz şu sıralar."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.concerns.anxieties) >= 3


async def test_concerns_failures_two_examples():
    raw = _collection(
        [
            _entry(eid=1, notes="Deploy sırasında hata yaptım."),
            _entry(eid=2, notes="Soruyu yapamadım, başaramadım."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.concerns.failures) >= 2


async def test_successes_achievements_two_examples():
    raw = _collection(
        [
            _entry(eid=1, notes="Bugu çözdüm, harika."),
            _entry(eid=2, notes="Yarışı kazandım."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.successes.achievements) >= 2


async def test_successes_milestones_two_examples():
    raw = _collection(
        [
            _entry(eid=1, notes="İlk kez topluluk önünde konuştum."),
            _entry(eid=2, notes="Sonunda projeyi teslim ettim."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.successes.milestones) >= 2


async def test_successes_positive_moments_two_examples():
    raw = _collection(
        [
            _entry(eid=1, notes="Bugün mutluyum."),
            _entry(eid=2, notes="Toplantı çok iyiydi."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.successes.positive_moments) >= 2


async def test_general_reflections_two_examples():
    long1 = "x" * 60 + " bir refleksiyon"
    long2 = "y" * 70 + " başka bir refleksiyon"
    raw = _collection([_entry(eid=1, notes=long1), _entry(eid=2, notes=long2)])
    out = await ParserService().parse(raw)
    assert len(out.fields.general.reflections) >= 2


async def test_general_observations_two_examples():
    raw = _collection(
        [
            _entry(eid=1, notes="Hava güneşli."),
            _entry(eid=2, notes="Kahve içtim."),
        ]
    )
    out = await ParserService().parse(raw)
    assert len(out.fields.general.observations) >= 2


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
async def test_no_entry_is_dropped_with_empty_columns():
    raw = _collection([_entry(eid=42, d=date(2026, 4, 1))])
    out = await ParserService().parse(raw)
    placeholder_text = out.fields.general.uncategorized[0].text
    assert placeholder_text == "(empty entry)"
    assert out.fields.general.uncategorized[0].source_entry_id == 42


async def test_by_date_index_mirrors_fields():
    raw = _collection(
        [
            _entry(eid=1, d=date(2026, 4, 10), notes="Bugün başardım."),
            _entry(eid=2, d=date(2026, 4, 11), notes="Yarın yapacağım."),
        ]
    )
    out = await ParserService().parse(raw)
    assert "2026-04-10" in out.by_date
    assert "2026-04-11" in out.by_date
    assert len(out.by_date["2026-04-10"].successes.achievements) == 1
    assert len(out.by_date["2026-04-11"].todos.open) == 1


async def test_metadata_reflects_input():
    raw = _collection(
        [
            _entry(eid=1, d=date(2026, 4, 10), notes="a"),
            _entry(eid=2, d=date(2026, 4, 20), notes="b"),
        ]
    )
    out = await ParserService().parse(raw)
    assert out.metadata.entry_count == 2
    assert out.metadata.date_range.start == date(2026, 4, 10)
    assert out.metadata.date_range.end == date(2026, 4, 20)
    assert out.metadata.parsed_at.tzinfo == timezone.utc


async def test_duplicate_sentences_in_same_entry_are_deduped():
    raw = _collection([_entry(eid=1, cue="Bugün başardım.", notes="Bugün başardım.")])
    out = await ParserService().parse(raw)
    assert len(out.fields.successes.achievements) == 1


async def test_parse_rejects_wrong_input_type():
    service = ParserService()
    with pytest.raises(ParserError):
        await service.parse({"not": "a collection"})  # type: ignore[arg-type]


async def test_empty_collection_produces_empty_tree():
    raw = RawEntryCollection(entries=[], count=0)
    out = await ParserService().parse(raw)
    assert out.metadata.entry_count == 0
    assert out.fields.todos.open == []
    assert out.by_date == {}
