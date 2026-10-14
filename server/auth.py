"""管理员 Cookie 会话。"""

from __future__ import annotations

import secrets
import time
from typing import Any

_SESSIONS: dict[str, dict[str, Any]] = {}
COOKIE_NAME = "zlxm_license_admin"
TTL_SEC = 12 * 3600


def create_session() -> str:
    token = secrets.token_urlsafe(24)
    _SESSIONS[token] = {"created": time.time()}
    _purge()
    return token


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    row = _SESSIONS.get(token)
    if not row:
        return False
    if time.time() - float(row["created"]) > TTL_SEC:
        _SESSIONS.pop(token, None)
        return False
    return True


def destroy_session(token: str | None) -> None:
    if token:
        _SESSIONS.pop(token, None)


def _purge() -> None:
    now = time.time()
    dead = [k for k, v in _SESSIONS.items() if now - float(v["created"]) > TTL_SEC]
    for k in dead:
        _SESSIONS.pop(k, None)
