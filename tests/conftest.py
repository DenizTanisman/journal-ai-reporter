"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest

# Ensure tests don't accidentally read a real .env from the developer machine.
os.environ.setdefault("CORNELL_API_URL", "http://test-cornell.local")
os.environ.setdefault("CORNELL_API_KEY", "test-cornell-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("APP_ENV", "development")


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from src.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
