"""LRU cache for classification results.

A `(category, subcategory)` list is small and bounded, so we can keep
~1000 entries in memory cheaply. The cache key is a sha256 prefix of
the whitespace-normalised + casefolded sentence so trivial variants
("Hello World" vs "hello   world") collapse to a single slot.

Cache invalidation strategy: process restart. The Reporter is
stateless beyond this in-process cache; clearing it has no
correctness effect, only a latency one. A future admin endpoint can
expose `clear()` if/when we need it.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Tuple

# Public type alias so callers don't reach into pydantic models.
CategorySubcategory = Tuple[str, str]


class ClassificationCache:
    """LRU cache keyed by sha256(normalised sentence)."""

    def __init__(self, max_size: int = 1000) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._store: OrderedDict[str, list[CategorySubcategory]] = OrderedDict()

    @staticmethod
    def _key(sentence: str) -> str:
        normalised = " ".join(sentence.casefold().split())
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]

    def get(self, sentence: str) -> list[CategorySubcategory] | None:
        key = self._key(sentence)
        value = self._store.get(key)
        if value is None:
            return None
        # Promote to most-recently-used.
        self._store.move_to_end(key)
        return value

    def set(self, sentence: str, value: list[CategorySubcategory]) -> None:
        key = self._key(sentence)
        if key in self._store:
            # Update in place; keep size constant.
            self._store[key] = value
            self._store.move_to_end(key)
            return
        self._store[key] = value
        self._store.move_to_end(key)
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
