"""ReporterService — orchestrates ParsedCollection → tag → AI → ReportResponse.

Stays free of HTTP concerns; the API layer wraps this. Constructor accepts a
GeminiClient so tests can inject a fake without touching the SDK.
"""

from __future__ import annotations

from src.config import Settings, get_settings
from src.logger import get_logger
from src.modules.parser.schemas import ParsedCollection
from src.modules.reporter.ai_client import GeminiClient
from src.modules.reporter.prompts import SYSTEM_PROMPT, build_user_prompt
from src.modules.reporter.schemas import ReportResponse
from src.modules.reporter.tag_handlers import prepare

log = get_logger(__name__)


class ReporterService:
    def __init__(
        self,
        settings: Settings | None = None,
        ai_client: GeminiClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._ai_client = ai_client or GeminiClient(settings=self._settings)

    async def generate(self, parsed: ParsedCollection, tag: str) -> ReportResponse:
        handler = prepare(parsed, tag)

        user_prompt = build_user_prompt(handler.template_key, handler.payload_json)

        async with self._ai_client as client:
            content = await client.generate_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

        if not isinstance(content, dict):
            from src.exceptions import InvalidAIResponseError

            raise InvalidAIResponseError("Gemini returned non-object JSON")

        markdown = handler.markdown_renderer(content)

        log.info(
            "reporter_generated",
            extra={
                "endpoint": "reporter.generate",
                "tag": tag,
                "status": "ok",
            },
        )

        return ReportResponse(
            tag=tag,
            date_range=handler.date_range,
            entry_count=handler.entry_count,
            content=content,
            raw_markdown=markdown,
        )
