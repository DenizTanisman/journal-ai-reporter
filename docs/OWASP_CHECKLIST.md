# OWASP API Security Top 10 (2023) — Coverage Checklist

| #     | Risk                                          | Status | Where addressed                                                                                  |
| ----- | --------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------ |
| API1  | Broken Object Level Authorization             | ✅     | Single-tenant deployment; the `/report` body has no object IDs to authorize. Multi-user is roadmap. |
| API2  | Broken Authentication                         | ✅     | Bearer auth on every Bridge route except `/health`; X-API-Key on the sidecar; fail-closed on unset key. |
| API3  | Broken Object Property Level Authorization    | ✅     | Pydantic `model_config={"extra": "forbid"}` on every request DTO; tag whitelist + regex.         |
| API4  | Unrestricted Resource Consumption             | ✅     | slowapi `20/min` on `/report`, 60s Gemini timeout, 30s Cornell timeout, 5MB upload cap, retry budget = 2. |
| API5  | Broken Function Level Authorization           | ✅     | `verify_internal_api_key` guards all sensitive endpoints via APIRouter dependency; `/health` deliberately open. |
| API6  | Unrestricted Access to Sensitive Business Flows | ✅   | The only business flow is `/report`. Bearer + rate limit + same fail-closed posture as API2.     |
| API7  | Server-Side Request Forgery                   | ✅     | The Bridge calls one fixed URL: `CORNELL_API_URL` from env (validated as the sidecar). No user-supplied URL ever reaches httpx. |
| API8  | Security Misconfiguration                     | ✅     | CORS whitelist (never `*`), `APP_DEBUG=false` in prod, `.env` in `.gitignore`, exception handler returns sanitized envelope, request-id propagated for trace correlation. |
| API9  | Improper Inventory Management                 | ✅     | OpenAPI auto-generated at `/docs`; one Bridge process, one sidecar process, README enumerates every route. |
| API10 | Unsafe Consumption of APIs                    | ✅     | Cornell responses validated by Pydantic (`RawEntry.model_validate`); Gemini output JSON-validated + retried; httpx wraps exceptions into domain types so the caller never sees raw upstream errors. |

## Notes on items marked ✅

- **API3:** the parser produces a strict tree (`FieldsTree`) with
  `extra="forbid"` semantics; the Reporter slices it by tag and never
  exposes mass-assignment.
- **API4:** rate-limit storage is in-memory by default. For a multi-pod
  deployment, swap in `slowapi.RedisStorage` — no code change to routes.
- **API7:** `pydantic-settings` strips trailing slashes on
  `CORNELL_API_URL` but does NOT validate the URL scheme. A defense-in-depth
  TODO is to assert `http(s)://` and a known host pattern at startup.
- **API10:** Gemini's `response_mime_type=application/json` plus our
  Pydantic re-validation means a malformed model output is detected
  before it can corrupt the response payload.

## Verification matrix

| Mitigation             | Test                                                                             |
| ---------------------- | -------------------------------------------------------------------------------- |
| Bearer auth enforced   | `tests/integration/test_api.py::test_report_missing_auth`                        |
| Wrong token rejected   | `tests/integration/test_api.py::test_report_wrong_token`                         |
| Tag whitelist + regex  | `tests/unit/test_reporter.py::test_is_valid_tag_rejects_others`                  |
| Rate limit             | `tests/integration/test_api.py::test_report_local_rate_limit_kicks_in`           |
| Cornell down → 502     | `tests/integration/test_api.py::test_report_cornell_down_returns_502`            |
| Gemini 429 → 429       | `tests/integration/test_api.py::test_report_gemini_rate_limit_propagates_429`    |
| Prompt injection       | `tests/unit/test_reporter.py::test_injection_attempt_does_not_break_wrapper`    |
| Sidecar read-only      | `cornell_journal_api/tests/test_endpoint.py::test_readonly_mode_rejects_writes` |
| Sidecar auth           | `cornell_journal_api/tests/test_endpoint.py::test_entries_requires_api_key`     |
| Full pipeline mapping  | `tests/integration/test_full_pipeline.py::test_full_pipeline_sidecar_through_bridge` |

## Out of scope / accepted risks

- **A multi-user deployment** would need API1 work (per-user scoping in
  the sidecar's WHERE clause; per-user bearer tokens).
- **Auditing of mutation operations** is N/A — there are none. The
  sidecar only reads.
- **Web UI XSS** is N/A here — there is no first-party UI in this repo.
  Jarvis renders the markdown; XSS hardening is its responsibility.
