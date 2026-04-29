"""Deterministic keyword/pattern-based categorization.

No AI here — that's the Reporter's job. This stage is fast and predictable so
later modules can reason about a stable structure. Rules cover Turkish first
(the user's primary language) plus common English fallbacks.

Each rule maps keyword/regex hits to a `(category, subcategory)`. A single
sentence can match multiple buckets; we keep all matches because losing
information at this stage would silently corrupt downstream reports. If
nothing matches, the sentence falls into general.{reflections|observations}
based on length, with `uncategorized` as the last resort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.modules.parser.schemas import CategoryName, SubCategoryName

REFLECTION_MIN_CHARS = 50

# Python's `str.lower()` on Turkish "İ" yields "i" + U+0307 (combining dot),
# which prevents naive substring matches against ASCII-cased keywords like
# "ilk kez". Stripping the combining dot after casefold gives consistent
# Turkish-aware matching without pulling in a unicode library.
_COMBINING_DOT_ABOVE = "̇"


def _norm(text: str) -> str:
    return text.casefold().replace(_COMBINING_DOT_ABOVE, "")


@dataclass(frozen=True)
class CategoryRule:
    category: CategoryName
    subcategory: SubCategoryName
    # Plain substring matches (case-insensitive). Avoids regex overhead for
    # the common case.
    keywords: tuple[str, ...] = ()
    # Regex patterns for richer matches (checkbox markers, word boundaries).
    patterns: tuple[re.Pattern[str], ...] = ()


# Order matters: more specific rules first so e.g. "yapamadım" hits failures
# before any open-todo heuristic.
RULES: tuple[CategoryRule, ...] = (
    # --- todos.completed (checkbox + verbs)
    # Note: bare "yaptım" is intentionally NOT a keyword — it appears inside
    # "yapamadım" / "hata yaptım" and would mis-flag failures as completed.
    # Concrete completion verbs only.
    CategoryRule(
        category="todos",
        subcategory="completed",
        keywords=("tamamladım", "bitirdim", "hallettim", "halledildi", "completed", "done"),
        patterns=(re.compile(r"\[\s*[xX]\s*\]"),),
    ),
    # --- todos.deferred
    CategoryRule(
        category="todos",
        subcategory="deferred",
        keywords=("ertelendi", "ertelendim", "yarına", "yarına bıraktım", "sonraya", "deferred", "postponed"),
    ),
    # --- todos.open
    CategoryRule(
        category="todos",
        subcategory="open",
        keywords=("yapacağım", "yapmalıyım", "yapılacak", "halletmeliyim", "todo", "to do"),
        patterns=(re.compile(r"\[\s*\]"),),
    ),
    # --- concerns.failures (must come before generic concerns to win on "yapamadım")
    CategoryRule(
        category="concerns",
        subcategory="failures",
        keywords=(
            "başaramadım",
            "yapamadım",
            "hata yaptım",
            "beceremedim",
            "failed",
            "messed up",
        ),
    ),
    # --- concerns.fears
    CategoryRule(
        category="concerns",
        subcategory="fears",
        keywords=("korkuyorum", "korkuyor", "korkutucu", "ürküyorum", "afraid", "scared"),
    ),
    # --- concerns.anxieties
    CategoryRule(
        category="concerns",
        subcategory="anxieties",
        keywords=(
            "endişe",
            "endişeliyim",
            "kaygı",
            "kaygılıyım",
            "stres",
            "stresliyim",
            "merak ediyorum",
            "anxious",
            "worried",
        ),
    ),
    # --- successes.milestones (must come before achievements: "ilk kez başardım")
    CategoryRule(
        category="successes",
        subcategory="milestones",
        keywords=("ilk kez", "sonunda", "nihayet", "first time", "finally", "at last"),
    ),
    # --- successes.achievements
    CategoryRule(
        category="successes",
        subcategory="achievements",
        keywords=("başardım", "kazandım", "çözdüm", "tamamladığım", "achieved", "won", "solved"),
    ),
    # --- successes.positive_moments
    CategoryRule(
        category="successes",
        subcategory="positive_moments",
        keywords=("mutluyum", "iyiydi", "harikaydı", "güzeldi", "keyifliydi", "happy", "great"),
    ),
)


def split_sentences(text: str) -> list[str]:
    """Lightweight sentence splitter.

    We don't pull in NLTK — Turkish journal entries are short and a regex on
    `.!?\n` boundaries plus checkbox-line preservation is good enough.
    Checkbox lines are kept whole so `[x]`/`[ ]` markers travel with their
    content.
    """
    if not text:
        return []
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\[\s*[xX ]\s*\]", line):
            sentences.append(line)
            continue
        for piece in re.split(r"(?<=[.!?…])\s+", line):
            piece = piece.strip()
            if piece:
                sentences.append(piece)
    return sentences


def classify_sentence(sentence: str) -> list[tuple[CategoryName, SubCategoryName]]:
    """Return every (category, subcategory) the sentence matches.

    Multi-match is intentional: "İlk kez başardım ama hala endişeliyim" should
    surface in milestones, achievements, AND anxieties — all three are real
    signals about the user's state. Deduped while preserving rule order.
    """
    normalized = _norm(sentence)
    seen: set[tuple[str, str]] = set()
    hits: list[tuple[CategoryName, SubCategoryName]] = []
    for rule in RULES:
        matched = any(_norm(kw) in normalized for kw in rule.keywords) or any(
            p.search(sentence) for p in rule.patterns
        )
        if matched:
            key = (rule.category, rule.subcategory)
            if key not in seen:
                seen.add(key)
                hits.append((rule.category, rule.subcategory))
    return hits


def fallback_subcategory(sentence: str) -> tuple[CategoryName, SubCategoryName]:
    """Pick a general bucket for sentences no rule claimed."""
    if not sentence.strip():
        return ("general", "uncategorized")
    if len(sentence) >= REFLECTION_MIN_CHARS:
        return ("general", "reflections")
    return ("general", "observations")
