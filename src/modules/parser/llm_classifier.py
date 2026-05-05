"""Hybrid Layer 2 — LLM-backed classifier with prompt-injection guards.

Wraps the existing `GeminiClient` (`reporter.ai_client`) so we don't
spin up a second SDK instance. Sentences are sandboxed inside triple
quotes and capped at 500 chars before they reach the prompt; Gemini
exceptions never propagate — we fall back to `[("general", "")]` so
the orchestrator can still produce a report when the LLM is down.
"""

from __future__ import annotations

from textwrap import dedent
from typing import Any, Protocol

from src.logger import get_logger

log = get_logger(__name__)

_MAX_INPUT_CHARS = 500
_FALLBACK_RESULT: list[tuple[str, str]] = [("general", "")]


_PROMPT_TEMPLATE = dedent(
    """
    Sen bir Türkçe günlük analiz uzmanısın. Aşağıdaki cümleyi sınıflandır.

    KATEGORİLER:
    - successes.achievements: Tamamlanmış somut başarılar
    - successes.milestones: İlkler, dönüm noktaları
    - successes.positive_moments: Pozitif duygu/an
    - concerns.fears: Korkular
    - concerns.anxieties: Kaygılar, endişeler
    - concerns.failures: Başarısızlıklar
    - todos.open: Açık yapılacaklar
    - general.reflections: Uzun düşünce
    - general.observations: Kısa gözlem

    KRİTİK KURALLAR:
    1. Olumsuzlamayı yakala: "korkmuyorum artık" → successes.positive_moments
    2. İroniyi yakala: "harika gidiyor!" sarcastic ise → concerns
    3. Bağlama bak: "kanseri yendim" → successes.achievements
    4. Bir cümle birden fazla kategoriye girebilir.

    Cümle: \"\"\"{sentence}\"\"\"

    SADECE şu JSON şemasında cevap ver, başka hiçbir şey yazma:
    {{"categories": [["successes", "achievements"]], "reasoning": "kısa açıklama"}}
    """
).strip()


class _GeminiLike(Protocol):
    async def generate_json(
        self, *, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        ...


class LLMClassifier:
    """Single-sentence classifier backed by an injectable Gemini client."""

    def __init__(self, gemini_client: _GeminiLike) -> None:
        self._gemini = gemini_client

    @staticmethod
    def _sanitize(sentence: str) -> str:
        """Cap length and neutralise the triple-quote sandbox boundary."""
        # Escape any `\"\"\"` sequences first so the cap doesn't slice through
        # an escape we just inserted.
        cleaned = sentence.replace('"""', "QQQ")
        if len(cleaned) > _MAX_INPUT_CHARS:
            cleaned = cleaned[:_MAX_INPUT_CHARS] + "..."
        return cleaned

    async def classify(self, sentence: str) -> list[tuple[str, str]]:
        """Return categories Gemini extracted from `sentence`. Always safe:
        any failure (network, schema, parse) yields the safe fallback."""
        safe = self._sanitize(sentence)
        prompt = _PROMPT_TEMPLATE.format(sentence=safe)
        try:
            payload = await self._gemini.generate_json(
                system_prompt="",
                user_prompt=prompt,
            )
        except Exception as exc:  # SDK errors are heterogeneous
            log.warning(
                "llm_classifier_failed",
                extra={
                    "endpoint": "llm_classifier.classify",
                    "error": str(exc)[:200],
                    "input_len": len(sentence),
                },
            )
            return list(_FALLBACK_RESULT)

        return self._parse_payload(payload)

    @staticmethod
    def _parse_payload(payload: Any) -> list[tuple[str, str]]:
        """Coerce Gemini's reply into a clean list of category tuples,
        dropping any malformed entries silently."""
        if not isinstance(payload, dict):
            return list(_FALLBACK_RESULT)
        categories = payload.get("categories")
        if not isinstance(categories, list):
            return list(_FALLBACK_RESULT)
        out: list[tuple[str, str]] = []
        for entry in categories:
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            cat, sub = entry
            if not isinstance(cat, str) or not isinstance(sub, str):
                continue
            out.append((cat, sub))
        if not out:
            return list(_FALLBACK_RESULT)
        return out
