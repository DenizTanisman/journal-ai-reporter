"""Tests for the LRU classification cache (hybrid Layer 1.5)."""

from __future__ import annotations

from src.modules.parser.cache import ClassificationCache


def test_get_returns_none_on_miss():
    c = ClassificationCache(max_size=10)
    assert c.get("hello") is None


def test_set_then_get_returns_stored_value():
    c = ClassificationCache(max_size=10)
    c.set("hello", [("successes", "achievements")])
    assert c.get("hello") == [("successes", "achievements")]


def test_normalisation_collapses_whitespace_and_case():
    """`Hello   World` and `hello world` must hit the same cache slot."""
    c = ClassificationCache(max_size=10)
    c.set("Hello   World", [("general", "reflections")])
    assert c.get("hello world") == [("general", "reflections")]
    assert c.get(" hello   world  ") == [("general", "reflections")]


def test_lru_eviction_drops_least_recent():
    """When the cache is full, the least-recently-used key is dropped
    on the next set, not the most recently added one."""
    c = ClassificationCache(max_size=2)
    c.set("a", [("successes", "achievements")])
    c.set("b", [("concerns", "fears")])
    # Touch `a` so `b` becomes the LRU candidate.
    assert c.get("a") is not None
    c.set("c", [("general", "reflections")])
    assert c.get("a") is not None       # still here (recently used)
    assert c.get("b") is None           # evicted
    assert c.get("c") is not None


def test_clear_drops_everything():
    c = ClassificationCache(max_size=10)
    c.set("a", [("successes", "achievements")])
    c.set("b", [("concerns", "fears")])
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None


def test_overwriting_existing_key_does_not_grow_cache():
    """Re-setting the same key must not bump us toward the eviction
    boundary — same-key writes are updates, not inserts."""
    c = ClassificationCache(max_size=2)
    c.set("a", [("general", "observations")])
    c.set("a", [("general", "reflections")])     # update
    c.set("b", [("concerns", "anxieties")])
    c.set("c", [("successes", "milestones")])
    # `a` was the LRU after the update + after `b`'s set, so it gets
    # evicted; `b` and `c` remain.
    assert c.get("a") is None
    assert c.get("b") is not None
    assert c.get("c") is not None
