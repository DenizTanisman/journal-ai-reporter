"""Hybrid classifier orchestrator — wires keywords + cache + LLM.

Decision tree per sentence:

    1. Cache hit → return.
    2. Keyword match in HIGH tier → accept, cache, return (no LLM).
    3. Keyword match in MEDIUM tier → call LLM to disambiguate.
    4. No keyword match → call LLM for full classification.
    5. LLM disabled → return MEDIUM hits (legacy keyword-only mode).

The cache stores HIGH and successful LLM verdicts. The safe-fallback
`[('general', '')]` is intentionally NOT cached so a transient Gemini
outage doesn't pin a useless entry forever.
"""

from __future__ import annotations

from typing import Protocol

from src.logger import get_logger
from src.modules.parser.cache import CategorySubcategory, ClassificationCache
from src.modules.parser.keyword_patterns import match_keywords

log = get_logger(__name__)


class _LLMClassifierLike(Protocol):
    async def classify(self, sentence: str) -> list[CategorySubcategory]:
        ...


_FALLBACK = [("general", "")]


class HybridClassifier:
    def __init__(
        self,
        llm_classifier: _LLMClassifierLike,
        cache: ClassificationCache | None = None,
        llm_enabled: bool = True,
    ) -> None:
        self._llm = llm_classifier
        self._cache = cache or ClassificationCache()
        self._llm_enabled = llm_enabled
        self._stats = {
            "keyword_hits": 0,
            "llm_calls": 0,
            "cache_hits": 0,
        }

    async def classify(self, sentence: str) -> list[CategorySubcategory]:
        if not sentence or not sentence.strip():
            return []

        # 1. Cache
        cached = self._cache.get(sentence)
        if cached is not None:
            self._stats["cache_hits"] += 1
            return cached

        # 2. Keyword match
        matches = match_keywords(sentence)
        high = [(c, s) for c, s, conf in matches if conf == "HIGH"]
        medium = [(c, s) for c, s, conf in matches if conf == "MEDIUM"]

        # 2a. HIGH → accept, never call LLM
        if high:
            self._stats["keyword_hits"] += 1
            self._cache.set(sentence, high)
            return high

        # 5. LLM disabled → legacy keyword-only fallback
        if not self._llm_enabled:
            return medium  # may be []

        # 3. MEDIUM with LLM → let the LLM disambiguate
        if medium:
            self._stats["llm_calls"] += 1
            llm_result = await self._llm.classify(sentence)
            verdict = medium if llm_result == _FALLBACK else llm_result
            if llm_result != _FALLBACK:
                self._cache.set(sentence, verdict)
            return verdict

        # 4. NO match → full LLM classification
        self._stats["llm_calls"] += 1
        llm_result = await self._llm.classify(sentence)
        if llm_result != _FALLBACK:
            self._cache.set(sentence, llm_result)
        return llm_result

    def get_stats(self) -> dict[str, int | float]:
        total = sum(self._stats.values())
        if total == 0:
            return dict(self._stats)
        return {
            **self._stats,
            "keyword_hit_rate": self._stats["keyword_hits"] / total,
            "cache_hit_rate": self._stats["cache_hits"] / total,
            "llm_call_rate": self._stats["llm_calls"] / total,
        }
