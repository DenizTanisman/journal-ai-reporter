"""Tests for HybridClassifier — the orchestrator that combines
keyword_patterns (Layer 1), the LLM classifier (Layer 2), and the
LRU cache. These tests pin the decision tree."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.modules.parser.cache import ClassificationCache
from src.modules.parser.hybrid_classifier import HybridClassifier


def _build(*, llm_return=None, llm_enabled: bool = True) -> tuple[HybridClassifier, AsyncMock]:
    llm = AsyncMock()
    if llm_return is not None:
        llm.classify.return_value = llm_return
    classifier = HybridClassifier(
        llm_classifier=llm,
        cache=ClassificationCache(max_size=100),
        llm_enabled=llm_enabled,
    )
    return classifier, llm


@pytest.mark.asyncio
async def test_high_confidence_keyword_skips_llm():
    """`korkuyorum` is HIGH `concerns.fears` — accept without LLM."""
    classifier, llm = _build()
    result = await classifier.classify("ben aslanlardan korkuyorum")
    assert ("concerns", "fears") in result
    llm.classify.assert_not_called()


@pytest.mark.asyncio
async def test_medium_confidence_calls_llm_for_verification():
    """`yendim` alone is MEDIUM — orchestrator must consult the LLM."""
    classifier, llm = _build(llm_return=[("successes", "achievements")])
    result = await classifier.classify("Bugün kanseri yendim")
    llm.classify.assert_awaited_once()
    assert ("successes", "achievements") in result


@pytest.mark.asyncio
async def test_no_keyword_match_routes_to_llm():
    """Generic sentence with no rule hits — fall through to LLM."""
    classifier, llm = _build(llm_return=[("general", "reflections")])
    result = await classifier.classify(
        "bugünkü gökyüzü çok ilginç bir renkteydi sanki"
    )
    llm.classify.assert_awaited_once()
    assert ("general", "reflections") in result


@pytest.mark.asyncio
async def test_cache_prevents_second_llm_call():
    """Same sentence twice — Gemini sees one call, cache serves the other."""
    classifier, llm = _build(llm_return=[("successes", "achievements")])
    await classifier.classify("kanseri yendim")
    await classifier.classify("kanseri yendim")
    assert llm.classify.call_count == 1


@pytest.mark.asyncio
async def test_llm_disabled_falls_back_to_keyword_only():
    """`llm_enabled=False` → no LLM, only the keyword layer's hits."""
    classifier, llm = _build(llm_enabled=False)
    result = await classifier.classify("F1 pilotu oldum")
    llm.classify.assert_not_called()
    # No HIGH match, so MEDIUM is what surfaces in the legacy fallback.
    assert ("successes", "achievements") in result


@pytest.mark.asyncio
async def test_llm_safe_fallback_does_not_pollute_cache():
    """Generic `('general','')` fallback must NOT be cached — next call
    might succeed when the LLM recovers."""
    classifier, llm = _build(llm_return=[("general", "")])
    await classifier.classify("garbled noise here")
    # Second call should still hit the LLM, not be served from cache.
    await classifier.classify("garbled noise here")
    assert llm.classify.call_count == 2


@pytest.mark.asyncio
async def test_high_match_wins_over_medium():
    """`başarısız oldum`: HIGH failures + MEDIUM `X oldum` both fire.
    The orchestrator must NOT call the LLM when at least one HIGH
    rule has a verdict — the HIGH tier is by definition unambiguous."""
    classifier, llm = _build()
    result = await classifier.classify("dün başarısız oldum")
    llm.classify.assert_not_called()
    assert ("concerns", "failures") in result


@pytest.mark.asyncio
async def test_get_stats_tracks_decision_paths():
    """Stats counters must reflect the path taken on each call."""
    classifier, llm = _build(llm_return=[("successes", "achievements")])
    await classifier.classify("başardım")               # keyword HIGH
    await classifier.classify("yendim")                 # MEDIUM → LLM
    await classifier.classify("yendim")                 # cache hit
    stats = classifier.get_stats()
    assert stats["keyword_hits"] == 1
    assert stats["llm_calls"] == 1
    assert stats["cache_hits"] == 1
