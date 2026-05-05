"""Hybrid Layer 1 — keyword pattern catalogue.

Two confidence levels:

- **HIGH**  — the pattern is single-meaning enough that we accept it
  without consulting the LLM (e.g. `başardım`, `endişeliyim`).
- **MEDIUM** — the surface match is ambiguous in Turkish (negation,
  sarcasm, missing object) so we hand it to the LLM for verification
  (e.g. `yendim`, `X oldum`, bare-noun `başarı`).

Negation guards live in the regexes themselves where possible
(`başarı(?!s[ıi]z)`), and tricky cases ("korkmuyorum") are carved out
of the HIGH set so they fall through to MEDIUM / LLM rather than
firing the wrong bucket.

The legacy `categorizer.RULES` is intentionally kept around — the
hybrid pipeline is gated by a feature flag and the legacy path needs
to keep working byte-for-byte while the new path bakes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from src.modules.parser.schemas import CategoryName, SubCategoryName

Confidence = Literal["HIGH", "MEDIUM"]


@dataclass(frozen=True)
class KeywordRule:
    category: CategoryName
    subcategory: SubCategoryName
    confidence: Confidence
    patterns: tuple[re.Pattern[str], ...]


# ---------------------------------------------------------------------------
# Successes
# ---------------------------------------------------------------------------

_HIGH_SUCCESS_RULES: tuple[KeywordRule, ...] = (
    # achievements — unambiguous Turkish completion verbs
    KeywordRule(
        category="successes",
        subcategory="achievements",
        confidence="HIGH",
        patterns=(
            # `başard*` / `başarıyor*` but NOT `başaramadım` (negative
            # aspect) and NOT `başarısız` (negation noun) — both stems
            # have their own concerns.failures rules that fire first.
            re.compile(r"\bbaşard\w*\b", re.IGNORECASE),
            re.compile(r"\bbaşarıyorum\b", re.IGNORECASE),
            re.compile(r"\bbaşarabildi\w*\b", re.IGNORECASE),
            re.compile(r"\btamamlad\w*\b", re.IGNORECASE),
            re.compile(r"\bbitirdi\w*\b", re.IGNORECASE),
            re.compile(r"\bhalle?tti\w*\b", re.IGNORECASE),
            re.compile(r"\bçözdü\w*\b", re.IGNORECASE),
            re.compile(r"\bkazand\w*\b", re.IGNORECASE),
            re.compile(r"\böğrendi\w*\b", re.IGNORECASE),
            re.compile(r"\bkabul\s+edildi\w*\b", re.IGNORECASE),
            re.compile(r"\bonaylandı\w*\b", re.IGNORECASE),
            re.compile(r"\bmuvaffak\s+oldu\w*\b", re.IGNORECASE),
        ),
    ),
    # milestones — "ilk kez X-dı" / "sonunda X-dı"
    KeywordRule(
        category="successes",
        subcategory="milestones",
        confidence="HIGH",
        patterns=(
            re.compile(r"\bilk\s+kez\s+\w*[dt][ıi]\w*\b", re.IGNORECASE),
            re.compile(r"\bilk\s+defa\s+\w*[dt][ıi]\w*\b", re.IGNORECASE),
            re.compile(r"\bnihayet\s+\w*[dt][ıi]\w*\b", re.IGNORECASE),
            re.compile(r"\bsonunda\s+\w*[dt][ıi]\w*\b", re.IGNORECASE),
        ),
    ),
    # positive_moments — emotion verbs / states
    KeywordRule(
        category="successes",
        subcategory="positive_moments",
        confidence="HIGH",
        patterns=(
            re.compile(r"\bgurur\s+duy\w*\b", re.IGNORECASE),
            re.compile(r"\bmutluyum\b", re.IGNORECASE),
            re.compile(r"\bsevin[dt]i\w*\b", re.IGNORECASE),
            re.compile(r"\bharika\s+hissetti\w*\b", re.IGNORECASE),
            re.compile(r"\btakdir\s+edildi\w*\b", re.IGNORECASE),
            re.compile(r"\bövgü\s+aldım\b", re.IGNORECASE),
        ),
    ),
)

_MEDIUM_SUCCESS_RULES: tuple[KeywordRule, ...] = (
    # achievements — context-dependent
    KeywordRule(
        category="successes",
        subcategory="achievements",
        confidence="MEDIUM",
        patterns=(
            # `yendim` / `yendi` — defeated what? (cancer / fear / opponent)
            re.compile(r"\byendi[mn]?\b", re.IGNORECASE),
            # `X oldum` — F1 pilotu / doktor / hasta? milestone or concern.
            re.compile(r"\b\w+\s+oldu[mn]?\b", re.IGNORECASE),
            # Bare noun `başarı` (NOT `başarısız`/`başarısızlık`).
            re.compile(r"\bbaşarı(?!s[ıi]z)\w*\b", re.IGNORECASE),
            # `geçtim` — exam? opportunity? phase?
            re.compile(r"\bgeçti\w*\b", re.IGNORECASE),
            # `kurtuldum` — habit? illness?
            re.compile(r"\bkurtuldu\w*\b", re.IGNORECASE),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Concerns
# ---------------------------------------------------------------------------
#
# Order matters at the rule list level:  failures > fears > anxieties.
# Within HIGH-confidence we also need the failure-rules to be evaluated
# before the achievement-rules so `başaramadım` cannot trip the
# positive bucket. The list-level order in `ALL_RULES` enforces that.

_HIGH_CONCERN_RULES: tuple[KeywordRule, ...] = (
    # failures — must come BEFORE achievements at the rule list level
    KeywordRule(
        category="concerns",
        subcategory="failures",
        confidence="HIGH",
        patterns=(
            re.compile(r"\bbaşaramad\w*\b", re.IGNORECASE),
            re.compile(r"\bbaşarısız\w*\b", re.IGNORECASE),
            re.compile(r"\byapamad\w*\b", re.IGNORECASE),
            re.compile(r"\bbeceremed\w*\b", re.IGNORECASE),
            re.compile(r"\bhata\s+yapt\w*\b", re.IGNORECASE),
            re.compile(r"\bkaybetti\w*\b", re.IGNORECASE),
            re.compile(r"\byenild[ıi]\w*\b", re.IGNORECASE),
        ),
    ),
    # fears — `kork(u|y)*` BUT NOT `korkmuyorum` (negation). Negative
    # lookbehind blocks the `m[uü]` slot that turns the verb negative.
    KeywordRule(
        category="concerns",
        subcategory="fears",
        confidence="HIGH",
        patterns=(
            # `korkuyorum` / `korkarım` / `korkutucu` / `korkuyor` — yes.
            # `korkmuyorum` / `korkmuyor` — NO (negation).
            re.compile(r"\bkork(?!m[uü])(?:u|y|a|ar|ut)\w*\b", re.IGNORECASE),
            re.compile(r"\bürküy\w*\b", re.IGNORECASE),
            re.compile(r"\bdehşete\s+düş\w*\b", re.IGNORECASE),
            re.compile(r"\bçekini[yt]\w*\b", re.IGNORECASE),
        ),
    ),
    # anxieties
    KeywordRule(
        category="concerns",
        subcategory="anxieties",
        confidence="HIGH",
        patterns=(
            re.compile(r"\bkaygı(?!s[ıi]z)\w*\b", re.IGNORECASE),
            re.compile(r"\bendişe(?!s[ıi]z)\w*\b", re.IGNORECASE),
            re.compile(r"\btedirgin\w*\b", re.IGNORECASE),
            re.compile(r"\bhuzursuz\w*\b", re.IGNORECASE),
            re.compile(r"\bgergin\w*\b", re.IGNORECASE),
            re.compile(r"\bstresli\w*\b", re.IGNORECASE),
            re.compile(r"\bpanik\w*\b", re.IGNORECASE),
        ),
    ),
)

_MEDIUM_CONCERN_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        category="concerns",
        subcategory="anxieties",
        confidence="MEDIUM",
        patterns=(
            # `ya X olursa` — anxious anticipation
            re.compile(r"\bya\s+\w+\s+olursa\b", re.IGNORECASE),
            # bare `hastayım` — physical / metaphorical?
            re.compile(r"\bhastayım\b", re.IGNORECASE),
            # `yorgun` — concern or just tired?
            re.compile(r"\byorgun\w*\b", re.IGNORECASE),
            # `şüphe` / `tereddüt` — uncertainty
            re.compile(r"\bşüphe\w*\b", re.IGNORECASE),
            re.compile(r"\btereddüt\w*\b", re.IGNORECASE),
            # `yetersiz` — self-assessment, often anxiety-flavoured
            re.compile(r"\byetersiz\w*\b", re.IGNORECASE),
            re.compile(r"\btükenmiş\w*\b", re.IGNORECASE),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Order: failures FIRST so negation traps are caught before they hit
# the success bucket; then HIGH success; then HIGH fears/anxieties;
# then all MEDIUM rules.
ALL_RULES: tuple[KeywordRule, ...] = (
    *_HIGH_CONCERN_RULES,    # failures > fears > anxieties (HIGH)
    *_HIGH_SUCCESS_RULES,    # achievements > milestones > positive_moments (HIGH)
    *_MEDIUM_SUCCESS_RULES,
    *_MEDIUM_CONCERN_RULES,
)


def match_keywords(
    sentence: str,
) -> list[tuple[CategoryName, SubCategoryName, Confidence]]:
    """Match `sentence` against every rule. Returns one entry per
    distinct (category, subcategory, confidence) hit, dedup'd.

    Multi-match is intentional: a sentence can land in milestones AND
    achievements ("ilk kez başardım"), or in MEDIUM achievements AND
    HIGH failures ("oldum" + "başarısız" both fire, the orchestrator
    deduplicates by category and lets HIGH win).
    """
    if not sentence or not sentence.strip():
        return []
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[CategoryName, SubCategoryName, Confidence]] = []
    for rule in ALL_RULES:
        if any(p.search(sentence) for p in rule.patterns):
            key = (rule.category, rule.subcategory, rule.confidence)
            if key not in seen:
                seen.add(key)
                out.append((rule.category, rule.subcategory, rule.confidence))
    return out
