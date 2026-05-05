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
    # Turkish stems with case-insensitive flag catch the full inflection ladder:
    # yapacağ-ım/-sın/-ız, yapmalı-yım/-sın, halletmel-iyim, etc. The regex is
    # word-boundary aware so "yapacaktı" still hits but "yapamayacağım" (negative
    # future) is intentionally caught by a more specific failures rule above.
    CategoryRule(
        category="todos",
        subcategory="open",
        keywords=("todo", "to do"),
        patterns=(
            re.compile(r"\[\s*\]"),
            re.compile(r"\byapaca\w*\b", re.IGNORECASE),
            re.compile(r"\byapmalı\w*\b", re.IGNORECASE),
            re.compile(r"\byapılaca\w*\b", re.IGNORECASE),
            re.compile(r"\bhalletme\w*\b", re.IGNORECASE),
        ),
    ),
    # --- concerns.failures (must come before generic concerns to win on "yapamadım")
    CategoryRule(
        category="concerns",
        subcategory="failures",
        keywords=("failed", "messed up"),
        patterns=(
            re.compile(r"\bbaşaramad\w*\b", re.IGNORECASE),
            re.compile(r"\byapamad\w*\b", re.IGNORECASE),
            re.compile(r"\bbeceremed\w*\b", re.IGNORECASE),
            re.compile(r"\bhata yapt\w*\b", re.IGNORECASE),
        ),
    ),
    # --- concerns.fears
    # `kork*` covers korkarım / korkarsın / korkma / korkuyor / korkutucu /
    # korkuyu — the Turkish "fear" stem. `ürk*` covers ürkütücü, ürküyorum.
    CategoryRule(
        category="concerns",
        subcategory="fears",
        keywords=("afraid", "scared"),
        patterns=(
            re.compile(r"\bkork\w*\b", re.IGNORECASE),
            re.compile(r"\bürk\w*\b", re.IGNORECASE),
        ),
    ),
    # --- concerns.anxieties
    CategoryRule(
        category="concerns",
        subcategory="anxieties",
        keywords=("merak ediyorum", "anxious", "worried"),
        patterns=(
            re.compile(r"\bendişe\w*\b", re.IGNORECASE),
            re.compile(r"\bkaygı\w*\b", re.IGNORECASE),
            re.compile(r"\bstres\w*\b", re.IGNORECASE),
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
        keywords=("achieved", "won", "solved"),
        patterns=(
            re.compile(r"\bbaşard\w*\b", re.IGNORECASE),
            re.compile(r"\bkazand\w*\b", re.IGNORECASE),
            re.compile(r"\bçözd\w*\b", re.IGNORECASE),
            re.compile(r"\btamamlad\w*\b", re.IGNORECASE),
            # `başarı` (noun) without the negation suffix `-sız`. The
            # negative lookahead keeps "başarısız(lık)" from matching.
            re.compile(r"\bbaşarı(?!s[ıi]z)\w*\b", re.IGNORECASE),
            # `oldum`/`oldu` style framings: "F1 pilotu oldum" / "doktor
            # oldum" / "anne oldum" — milestone-flavoured statements that
            # the verb-only rules above miss.
            re.compile(r"\b\w+\s+oldu[mn]?\b", re.IGNORECASE),
            # `yendim` / `yendi` — defeat/overcome verb. "kanseri yendim",
            # "rakibi yendi". Past-tense form keeps it tight enough to
            # avoid the passive "yemek yenildi" false-positive.
            re.compile(r"\byendi[mn]?\b", re.IGNORECASE),
        ),
    ),
    # --- successes.positive_moments
    CategoryRule(
        category="successes",
        subcategory="positive_moments",
        keywords=("happy", "great"),
        patterns=(
            re.compile(r"\bmutlu\w*\b", re.IGNORECASE),
            re.compile(r"\biyiyd\w*\b", re.IGNORECASE),
            re.compile(r"\bharika\w*\b", re.IGNORECASE),
            re.compile(r"\bgüzeld\w*\b", re.IGNORECASE),
            re.compile(r"\bkeyif\w*\b", re.IGNORECASE),
        ),
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
        # Run patterns against the normalized (Turkish-case-folded) text too,
        # so that "Korkarım" / "Endişe" / "İlk" etc. match a stem regex even
        # when Python's Unicode lowering of dotted İ would otherwise insert
        # a combining dot between letters.
        matched = (
            any(_norm(kw) in normalized for kw in rule.keywords)
            or any(p.search(sentence) for p in rule.patterns)
            or any(p.search(normalized) for p in rule.patterns)
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
