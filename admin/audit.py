"""操作审计日志（本地文件）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from admin.paths import AUDIT_LOG


def audit(action: str, detail: dict[str, Any] | None = None, actor: str = "local") -> None:
    """追加一条操作记录。detail 不得包含完整 API Key。"""
    row = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "actor": actor,
        "action": action,
        "detail": _redact(detail or {}),
    }
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_audit(limit: int = 200) -> list[dict[str, Any]]:
    if not AUDIT_LOG.exists():
        return []
    lines = AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for line in reversed(lines[-limit:]):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _redact(detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in detail.items():
        lk = key.lower()
        if any(s in lk for s in ("key", "secret", "token", "password")):
            out[key] = "***"
        else:
            out[key] = value
    return out
