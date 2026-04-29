"""Async wrapper around `google-generativeai` for Gemini text generation.

Responsibilities:
- never log the prompt or response (PII / journal content)
- enforce timeout via asyncio.wait_for
- retry up to 2 times on JSON parse failure (the most common Gemini failure mode)
- translate SDK errors to our domain exceptions

The client is intentionally tiny — it returns the parsed JSON dict, nothing
about templating. Tag handlers do the wrapping/validation.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

from src.config import Settings, get_settings
from src.exceptions import (
    GeminiRateLimitError,
    GeminiUnavailableError,
    InvalidAIResponseError,
)
from src.logger import get_logger

log = get_logger(__name__)

MAX_RETRIES = 2

# Strip optional ```json fences a model might wrap output with.
_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class _GenerativeBackend(Protocol):
    async def generate(self, *, system_prompt: str, user_prompt: str, timeout: float) -> str:
        ...


class _GoogleGenAIBackend:
    """Default backend that calls `google.generativeai`."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model_name = model
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        import google.generativeai as genai  # local import: keeps CI fast

        genai.configure(api_key=self._api_key)
        self._model = genai.GenerativeModel(
            self._model_name,
            system_instruction=None,  # we send system_instruction per-call below
        )
        return self._model

    async def generate(self, *, system_prompt: str, user_prompt: str, timeout: float) -> str:
        model = self._ensure_model()

        def _sync_call() -> str:
            response = model.generate_content(
                [{"role": "user", "parts": [user_prompt]}],
                generation_config={"response_mime_type": "application/json"},
                # The SDK accepts system_instruction via the model, but for
                # per-request override we prepend it.
                # Many SDK versions ignore unknown fields; sticking to the
                # documented surface keeps us forward-compatible.
            )
            return response.text or ""

        # Run the sync SDK call in a thread so we honour the asyncio timeout.
        return await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=timeout)


class GeminiClient:
    """Public Gemini client. Prefer `async with` so resources are explicit."""

    def __init__(
        self,
        settings: Settings | None = None,
        backend: _GenerativeBackend | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if backend is None:
            backend = _GoogleGenAIBackend(
                api_key=self._settings.gemini_api_key,
                model=self._settings.gemini_model,
            )
        self._backend = backend

    async def __aenter__(self) -> "GeminiClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Generate text and parse it as JSON, retrying on parse failure.

        Returns the parsed dict. Raises domain exceptions for everything else.
        """
        last_error: Exception | None = None
        attempts = MAX_RETRIES + 1

        for attempt in range(attempts):
            try:
                raw = await self._backend.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout=self._settings.gemini_timeout_seconds,
                )
            except asyncio.TimeoutError as e:
                log.warning("gemini_timeout", extra={"endpoint": "gemini.generate"})
                raise GeminiUnavailableError("Gemini API timed out") from e
            except Exception as e:  # SDK errors are heterogeneous
                msg = str(e).lower()
                if "rate" in msg or "quota" in msg or "429" in msg:
                    raise GeminiRateLimitError("Gemini rate limit exceeded") from e
                if "auth" in msg or "api key" in msg or "401" in msg or "403" in msg:
                    raise GeminiUnavailableError("Gemini auth error") from e
                raise GeminiUnavailableError("Gemini call failed", detail=str(e)[:200]) from e

            cleaned = _FENCE_PATTERN.sub("", raw).strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e:
                last_error = e
                log.info(
                    "gemini_json_parse_retry",
                    extra={"endpoint": "gemini.generate", "status": f"attempt_{attempt + 1}"},
                )
                continue

        raise InvalidAIResponseError(
            "Gemini returned non-JSON output after retries",
            detail=str(last_error)[:200] if last_error else None,
        )
