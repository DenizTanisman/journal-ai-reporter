"""Tests for the new keyword_patterns module (hybrid Layer 1).

Each pattern is annotated with a confidence level so the orchestrator
can decide whether to call the LLM. HIGH = unambiguous, accept.
MEDIUM = needs LLM verification.

These tests pin the pattern coverage so future regex tweaks don't
silently lose hits — and equally important, don't let known traps
("korkmuyorum", "başarısız oldum") creep back into the wrong bucket.
"""

from __future__ import annotations

from src.modules.parser.keyword_patterns import (
    Confidence,
    KeywordRule,
    match_keywords,
)


def _hits(sentence: str, *, conf: Confidence | None = None) -> set[tuple[str, str]]:
    out = match_keywords(sentence)
    if conf is not None:
        out = [(c, s, k) for c, s, k in out if k == conf]
    return {(c, s) for c, s, _ in out}


# ---------------------------------------------------------------------------
# HIGH-confidence success patterns
# ---------------------------------------------------------------------------

def test_high_success_achievement_verbs():
    assert ("successes", "achievements") in _hits("başardım", conf="HIGH")
    assert ("successes", "achievements") in _hits("tamamladım", conf="HIGH")
    assert ("successes", "achievements") in _hits("bitirdim", conf="HIGH")
    assert ("successes", "achievements") in _hits("hallettik", conf="HIGH")


def test_high_success_milestone_phrasing():
    # "ilk kez başardım" both milestones AND achievements (multi-match)
    cats = _hits("ilk kez başardım", conf="HIGH")
    assert ("successes", "milestones") in cats
    assert ("successes", "achievements") in cats


def test_high_success_positive_emotion():
    assert ("successes", "positive_moments") in _hits("mutluyum", conf="HIGH")
    assert ("successes", "positive_moments") in _hits("gurur duyuyorum", conf="HIGH")


# ---------------------------------------------------------------------------
# HIGH-confidence concern patterns
# ---------------------------------------------------------------------------

def test_high_concern_fear_verbs():
    assert ("concerns", "fears") in _hits("korkuyorum", conf="HIGH")
    assert ("concerns", "fears") in _hits("ben aslanlardan korkarım", conf="HIGH")


def test_high_concern_anxiety_words():
    assert ("concerns", "anxieties") in _hits("endişeliyim", conf="HIGH")
    assert ("concerns", "anxieties") in _hits("kaygılıyım bugün", conf="HIGH")
    assert ("concerns", "anxieties") in _hits("çok stresli bir gündü", conf="HIGH")


def test_high_concern_failure_verbs():
    # `başaramadım` MUST land in failures HIGH, never in achievements HIGH.
    cats_high = _hits("dün başaramadım", conf="HIGH")
    assert ("concerns", "failures") in cats_high
    assert ("successes", "achievements") not in cats_high
    # `başarısız oldum`: HIGH says failures. The MEDIUM rule `X oldum`
    # also fires, but the orchestrator's HIGH-wins rule makes that
    # benign — what this test pins is that no HIGH success rule misfires.
    high_for_negation = _hits("başarısız oldum", conf="HIGH")
    assert ("concerns", "failures") in high_for_negation
    assert ("successes", "achievements") not in high_for_negation


# ---------------------------------------------------------------------------
# MEDIUM-confidence patterns (require LLM follow-up)
# ---------------------------------------------------------------------------

def test_medium_yendim_is_ambiguous():
    # "yendim" alone is medium because object decides meaning.
    cats = _hits("kanseri yendim", conf="MEDIUM")
    assert ("successes", "achievements") in cats
    # And it should NOT appear as HIGH for the same input.
    assert ("successes", "achievements") not in _hits("kanseri yendim", conf="HIGH")


def test_medium_x_oldum_is_ambiguous():
    cats = _hits("F1 pilotu oldum", conf="MEDIUM")
    assert ("successes", "achievements") in cats
    # "hasta oldum" is also `X oldum` shape but means concern — both
    # patterns may fire and the LLM disambiguates.
    cats2 = _hits("hasta oldum", conf="MEDIUM")
    # Either side may fire; the orchestrator decides via LLM.
    assert ("successes", "achievements") in cats2


def test_medium_basari_noun_alone():
    # `başarı` as a bare noun is MEDIUM — sarcasm/negation/etc.
    cats = _hits("bu bir başarı", conf="MEDIUM")
    assert ("successes", "achievements") in cats
    # `başarısız` (negation) must NOT match this medium rule.
    medium_for_negation = _hits("bu bir başarısızlık", conf="MEDIUM")
    assert ("successes", "achievements") not in medium_for_negation


# ---------------------------------------------------------------------------
# Negation traps — must NOT fire HIGH concern.fears
# ---------------------------------------------------------------------------

def test_korkmuyorum_does_not_fire_high_fear():
    """The classic Turkish negation trap: regex `\\bkork[uy]\\w*` matches
    `korkmuyorum`. The hybrid layer must NOT mark this HIGH — it has to
    bubble up to the LLM (or at most MEDIUM)."""
    cats_high = _hits("korkmuyorum artık", conf="HIGH")
    assert ("concerns", "fears") not in cats_high


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty_list():
    assert match_keywords("") == []
    assert match_keywords("   ") == []


# ---------------------------------------------------------------------------
# Rule structure invariants
# ---------------------------------------------------------------------------

def test_keywordrule_has_explicit_confidence():
    """Every rule must declare HIGH or MEDIUM — no defaults, no None."""
    from src.modules.parser.keyword_patterns import ALL_RULES

    for rule in ALL_RULES:
        assert isinstance(rule, KeywordRule)
        assert rule.confidence in {"HIGH", "MEDIUM"}, (
            f"Rule {rule.category}.{rule.subcategory} has invalid confidence "
            f"{rule.confidence!r}"
        )
