# Parser Module

Turns the Converter's raw journal text into a categorized `ParsedCollection`
the Reporter can slice by tag (`/detail`, `/todo`, `/concern`, `/success`).

Two paths coexist behind one async API. The active path is decided once
per `ParserService` instance, controlled by a feature flag.

## Paths

### Legacy (default)

Construction:
```python
ParserService()           # no hybrid argument
```

Per-sentence pipeline:
```
sentence → categorizer.classify_sentence  →  list[(category, sub)]
```

The deterministic regex catalogue in `categorizer.RULES`. Sync internally,
wrapped in async only because the public method is async. **No network,
no LLM, no cache.** This is what `hybrid_classifier_enabled=False`
(default) gives you, and what production uses today.

### Hybrid (opt-in)

Construction:
```python
hybrid = HybridClassifier(
    llm_classifier=LLMClassifier(gemini_client=...),
    cache=ClassificationCache(max_size=1000),
    llm_enabled=True,
)
ParserService(hybrid=hybrid)
```

Per-sentence decision tree:
```
sentence
  ├─ cache hit                 → return
  ├─ HIGH keyword match        → accept, cache, return  (no LLM)
  ├─ MEDIUM keyword match      → LLM verifies / refines
  ├─ no keyword match          → LLM full-classifies
  └─ llm_enabled=False         → return MEDIUM hits (legacy fallback)
```

The orchestrator caches every successful verdict but never the
safe-fallback `[("general", "")]` — a transient Gemini outage
shouldn't poison the cache.

## Files

| File | Role |
|------|------|
| `categorizer.py` | Legacy sync catalogue + `split_sentences` + `fallback_subcategory` |
| `keyword_patterns.py` | Hybrid Layer 1: HIGH/MEDIUM rule catalogue + `match_keywords` |
| `cache.py` | LRU cache keyed by sha256(normalised sentence) |
| `llm_classifier.py` | Hybrid Layer 2: Gemini wrapper with prompt-injection guards |
| `hybrid_classifier.py` | Decision-tree orchestrator |
| `service.py` | Async `ParserService.parse()` (legacy or hybrid) |
| `schemas.py` | Pydantic `ParsedCollection` / `FieldsTree` (frozen Literal subcategories) |

## Confidence levels (HIGH vs MEDIUM)

The hybrid layer treats Turkish ambiguity explicitly:

- **HIGH**: single-meaning patterns. Example: `başardım` → success;
  `endişeliyim` → anxiety. Negation guards live in the regex
  (`başarı(?!s[ıi]z)`, `kork(?!m[uü])`) so traps like `başarısız oldum`
  and `korkmuyorum` can never fire HIGH on the wrong bucket.
- **MEDIUM**: surface match but the meaning depends on object,
  negation or sarcasm. Example: `yendim` (defeated what?), `X oldum`
  (became what?), bare-noun `başarı`. The orchestrator hands these
  to the LLM.

## Feature flag rollout

`Settings.hybrid_classifier_enabled` defaults to `false`. Production
keeps the legacy path until an operator sets `HYBRID_CLASSIFIER_ENABLED=true`
in `.env`. The flag is read in `dependencies.get_parser_service()` once
per request — flipping it requires no code change, only a re-deploy.

## Performance budget

| Path | Per-sentence latency |
|------|-----------------------|
| Cache hit | ~1 ms |
| HIGH keyword | ~10 ms (regex only) |
| MEDIUM / NO match (cache miss) | 500–2000 ms (Gemini 2.5 Flash) |

Targets pinned by `HybridClassifier.get_stats()`:
- LLM call rate < 30 % (rest served by cache + HIGH)
- Cache hit rate > 50 % (Cornell journals repeat phrases)

## Schema extensions

The Pydantic Literal `SubCategoryName` is intentionally frozen at the
12 legacy values. If the LLM names something outside that set
(`regrets`, `sadness`, …), `ParserService` coerces it to
`general.uncategorized` so the wire shape stays stable. Extending the
schema is a separate follow-up commit.

## Tests

| File | Layer | Count |
|------|-------|-------|
| `tests/unit/test_parser.py` | Legacy + service shape | 27 |
| `tests/unit/test_keyword_patterns.py` | Hybrid Layer 1 | 12 |
| `tests/unit/test_classification_cache.py` | LRU + normalisation | 6 |
| `tests/unit/test_llm_classifier.py` | Layer 2 + safe fallback | 7 |
| `tests/unit/test_hybrid_classifier.py` | Orchestrator decision tree | 8 |
| `tests/unit/test_hybrid_e2e.py` | Full pipeline edge cases | 6 |

`pytest -q tests/unit/` runs all of them in <1 s using the deterministic
mock LLM. Real-Gemini tests live in the integration suite and are
gated on `GEMINI_API_KEY`.
