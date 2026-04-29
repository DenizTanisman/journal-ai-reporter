"""Single shared `slowapi.Limiter` instance.

`main.py` registers it on `app.state` and the rate-limit handler; routes pull
it in to attach `@limiter.limit(...)` to specific endpoints. Centralizing
avoids two Limiters fighting over `app.state.limiter`.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import get_settings


def _default_limit() -> str:
    return f"{get_settings().rate_limit_per_minute}/minute"


limiter = Limiter(key_func=get_remote_address)
report_rate_limit = _default_limit()
