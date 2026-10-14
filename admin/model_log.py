"""大模型 / Jev 调用日志（脱敏，仅本机文件）。"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any

from admin.paths import MODEL_LOG

_LOCK = threading.Lock()
_MAX_FIELD = 6000
_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer\s+[a-z0-9\-_.]+|sk-[a-z0-9]+|ts_[a-z0-9]+)"
)


def log_model_call(
    *,
    kind: str,
    purpose: str,
    request: Any,
    response: Any = None,
    ok: bool = True,
    error: str | None = None,
    elapsed_ms: int | None = None,
    model: str | None = None,
    endpoint: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """追加一条模型调用记录。kind: llm | jev。"""
    row = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": kind,
        "purpose": purpose,
        "ok": ok,
        "model": model or "",
        "endpoint": _redact_text(endpoint or ""),
        "elapsed_ms": elapsed_ms,
        "request": _clip(_redact_obj(request)),
        "response": _clip(_redact_obj(response)) if response is not None else None,
        "error": _redact_text(error)[:800] if error else None,
        "meta": _redact_obj(meta or {}),
    }
    MODEL_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False)
    with _LOCK:
        with open(MODEL_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_model_logs(
    *,
    limit: int = 100,
    kind: str | None = None,
    purpose: str | None = None,
) -> list[dict[str, Any]]:
    if not MODEL_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in MODEL_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if kind and obj.get("kind") != kind:
            continue
        if purpose and purpose not in str(obj.get("purpose") or ""):
            continue
        rows.append(obj)
    rows.reverse()
    return rows[:limit]


def clear_model_logs() -> None:
    with _LOCK:
        MODEL_LOG.write_text("", encoding="utf-8")


def _clip(value: Any) -> Any:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    if len(text) <= _MAX_FIELD:
        return value if not isinstance(value, str) else text
    if isinstance(value, str):
        return text[:_MAX_FIELD] + f"…(truncated,{len(text)})"
    return {"_truncated": True, "_preview": text[:_MAX_FIELD], "_len": len(text)}


def _redact_text(text: str) -> str:
    if not text:
        return ""
    out = _KEY_RE.sub("***", text)
    # 长 token 形态
    out = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", lambda m: "***" if _looks_secret(m.group(0)) else m.group(0), out)
    return out


def _looks_secret(s: str) -> bool:
    low = s.lower()
    return low.startswith(("sk-", "sk_", "ts_", "bearer")) or (s.isalnum() and len(s) >= 40)


def _redact_obj(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, str):
        return _redact_text(obj)
    if isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_redact_obj(x) for x in obj[:50]]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in ("key", "secret", "token", "password", "authorization")):
                out[k] = "***"
            else:
                out[k] = _redact_obj(v)
        return out
    return _redact_text(str(obj))
