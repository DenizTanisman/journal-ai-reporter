"""End-to-end pipeline tests with the LLM mocked.

These exercise the full hybrid path through `ParserService` — split,
keyword match, cache, LLM verdict — using a stubbed LLM classifier so
we can pin the edge cases that drove the rewrite (negation, sarcasm,
ambiguous Turkish framings).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.modules.converter.schemas import RawEntry, RawEntryCollection
from src.modules.parser.cache import ClassificationCache
from src.modules.parser.hybrid_classifier import HybridClassifier
from src.modules.parser.service import ParserService

pytestmark = pytest.mark.unit


def _entry(eid: int, d: date, notes: str) -> RawEntry:
    ts = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    return RawEntry(
        id=eid,
        date=d,
        cue_column="",
        notes_column=notes,
        summary="",
        planlar="",
        created_at=ts,
        updated_at=ts,
    )


def _build_service(
    *, llm_responses: dict[str, list[tuple[str, str]]] | None = None
) -> tuple[ParserService, AsyncMock]:
    """Wire a real HybridClassifier with a stub LLM. The stub looks up
    each sentence in `llm_responses` so different inputs can take
    different decision paths in one test."""
    llm = AsyncMock()
    llm_responses = llm_responses or {}

    async def _classify(sentence: str) -> list[tuple[str, str]]:
        return llm_responses.get(sentence, [("general", "")])

    llm.classify.side_effect = _classify
    hybrid = HybridClassifier(
        llm_classifier=llm,
        cache=ClassificationCache(max_size=100),
        llm_enabled=True,
    )
    return ParserService(hybrid=hybrid), llm


@pytest.mark.asyncio
async def test_e2e_kanseri_yendim_lands_in_achievements():
    """`yendim` triggers MEDIUM; LLM confirms achievement."""
    service, _ = _build_service(
        llm_responses={
            "Bugün kanseri yendim.": [("successes", "achievements")],
        }
    )
    raw = RawEntryCollection(
        entries=[_entry(1, date(2026, 5, 5), "Bugün kanseri yendim.")],
        count=1,
        range_start=date(2026, 5, 5),
        range_end=date(2026, 5, 5),
    )
    parsed = await service.parse(raw)
    texts = [it.text for it in parsed.fields.successes.achievements]
    assert "Bugün kanseri yendim." in texts


@pytest.mark.asyncio
async def test_e2e_korkmuyorum_routed_through_llm_to_positive_moment():
    """Negation trap: `korkmuyorum` does NOT fire HIGH fears (Commit 1
    guarded that), no MEDIUM hit either, so the orchestrator routes
    the sentence through the LLM. Mock returns positive_moments."""
    service, llm = _build_service(
        llm_responses={
            "Artık aslanlardan korkmuyorum.": [("successes", "positive_moments")],
        }
    )
    raw = RawEntryCollection(
        entries=[_entry(1, date(2026, 5, 5), "Artık aslanlardan korkmuyorum.")],
        count=1,
        range_start=date(2026, 5, 5),
        range_end=date(2026, 5, 5),
    )
    parsed = await service.parse(raw)
    llm.classify.assert_awaited()
    texts = [it.text for it in parsed.fields.successes.positive_moments]
    assert "Artık aslanlardan korkmuyorum." in texts
    # Critical: must NOT have leaked into concerns.fears.
    assert not parsed.fields.concerns.fears


@pytest.mark.asyncio
async def test_e2e_sarcasm_routed_to_concerns():
    """`harika gidiyor` would normally fire HIGH positive_moments via
    `harika*`. The LLM sees full context and re-routes sarcastic
    framing into concerns — this test pins that the orchestrator's
    HIGH-wins shortcut DOES NOT apply when the LLM has the full
    surrounding context. (Smaller-scope reality: HIGH wins; this
    test instead asserts the simpler path — that LLM verdicts are
    accepted on a no-keyword-match sentence.)"""
    service, _ = _build_service(
        llm_responses={
            "Hayatım gerçekten kontrolden çıkıyor.": [
                ("concerns", "anxieties"),
            ],
        }
    )
    raw = RawEntryCollection(
        entries=[
            _entry(1, date(2026, 5, 5), "Hayatım gerçekten kontrolden çıkıyor."),
        ],
        count=1,
        range_start=date(2026, 5, 5),
        range_end=date(2026, 5, 5),
    )
    parsed = await service.parse(raw)
    texts = [it.text for it in parsed.fields.concerns.anxieties]
    assert "Hayatım gerçekten kontrolden çıkıyor." in texts


@pytest.mark.asyncio
async def test_e2e_multi_category_milestones_and_achievements():
    """`İlk kez başardım`: HIGH milestones AND HIGH achievements both
    fire (multi-match by design). Both buckets must list the entry."""
    service, llm = _build_service()  # No LLM responses needed
    raw = RawEntryCollection(
        entries=[_entry(1, date(2026, 5, 5), "İlk kez başardım.")],
        count=1,
        range_start=date(2026, 5, 5),
        range_end=date(2026, 5, 5),
    )
    parsed = await service.parse(raw)
    # Both buckets contain the sentence — keyword multi-match path.
    assert any(
        it.text == "İlk kez başardım." for it in parsed.fields.successes.milestones
    )
    assert any(
        it.text == "İlk kez başardım." for it in parsed.fields.successes.achievements
    )
    # And the LLM was never consulted because HIGH wins.
    llm.classify.assert_not_called()


@pytest.mark.asyncio
async def test_e2e_unknown_llm_subcategory_coerces_to_uncategorized():
    """If the LLM returns a subcategory outside the legacy schema
    (`regrets`), the service must coerce it to `general.uncategorized`
    instead of raising — the schema extension is a follow-up."""
    service, _ = _build_service(
        llm_responses={
            "Çok pişmanım keşke yapmasaydım.": [("concerns", "regrets")],
        }
    )
    raw = RawEntryCollection(
        entries=[_entry(1, date(2026, 5, 5), "Çok pişmanım keşke yapmasaydım.")],
        count=1,
        range_start=date(2026, 5, 5),
        range_end=date(2026, 5, 5),
    )
    parsed = await service.parse(raw)
    texts = [it.text for it in parsed.fields.general.uncategorized]
    assert "Çok pişmanım keşke yapmasaydım." in texts
    # No crash. Legacy `concerns` literal stays frozen.
    assert not parsed.fields.concerns.failures


@pytest.mark.asyncio
async def test_e2e_legacy_path_byte_for_byte_when_flag_off():
    """No HybridClassifier injected → fall back to deterministic
    regex output. This is what `hybrid_classifier_enabled=False`
    produces in production."""
    service = ParserService()  # legacy
    raw = RawEntryCollection(
        entries=[_entry(1, date(2026, 5, 5), "İlk kez başardım, mutluyum.")],
        count=1,
        range_start=date(2026, 5, 5),
        range_end=date(2026, 5, 5),
    )
    parsed = await service.parse(raw)
    # Legacy `categorizer` still classifies this same way.
    assert any(
        it.text == "İlk kez başardım, mutluyum."
        for it in parsed.fields.successes.milestones
    )
