"""Domain exception hierarchy.

API layer maps these to user-facing error codes/messages without leaking stack
traces. `code` is the contract; `message` is safe to surface to clients.
"""

from __future__ import annotations


class JournalReporterError(Exception):
    """Base for every domain error."""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail


# --- Converter ---
class ConverterError(JournalReporterError):
    code = "converter_error"
    http_status = 502


class CornellUnavailableError(ConverterError):
    code = "cornell_unavailable"
    http_status = 502


class CornellAuthError(ConverterError):
    code = "cornell_auth_error"
    http_status = 502


# --- Parser ---
class ParserError(JournalReporterError):
    code = "parser_error"
    http_status = 500


# --- Reporter ---
class ReporterError(JournalReporterError):
    code = "reporter_error"
    http_status = 500


class GeminiUnavailableError(ReporterError):
    code = "gemini_unavailable"
    http_status = 503


class GeminiRateLimitError(ReporterError):
    code = "gemini_rate_limit"
    http_status = 429


class InvalidAIResponseError(ReporterError):
    code = "invalid_ai_response"
    http_status = 502


# --- Tag / request validation ---
class InvalidTagError(JournalReporterError):
    code = "invalid_tag"
    http_status = 400


class DateNotInRangeError(JournalReporterError):
    code = "date_not_in_range"
    http_status = 404


class NoEntriesError(JournalReporterError):
    code = "no_entries"
    http_status = 404


# --- Auth ---
class UnauthorizedError(JournalReporterError):
    code = "unauthorized"
    http_status = 401
