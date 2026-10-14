"""TypeSafe Jev / System One 客户端。

正确请求体（官方）：
  POST {base}/v1/systemone
  {"model": "jev-latest", "state": "...", "questions": {"id": {"type":"noul","instructions":"..."}}}

响应：
  {"answers": {"id": {"type":"noul","noul": 0.99}}, "model": "...", "usage": {...}}
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any


class JevError(RuntimeError):
    """Jev 调用失败。"""


# 对端掐连接 / 超时 / 网关抖动：可重试
_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
_TRANSIENT_MARKERS = (
    "remote end closed",
    "connection reset",
    "connection aborted",
    "broken pipe",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection refused",
    "eof occurred",
    "incomplete read",
    "reset by peer",
)


def jev_configured() -> bool:
    env_on = (os.environ.get("JEV_ENABLED") or "true").strip().lower()
    if env_on in {"0", "false", "no", "off"}:
        return False
    key = (os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or "").strip()
    return bool(key)


def system_one(
    state: str | dict[str, Any] | list[Any],
    questions: dict[str, dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float = 45.0,
    purpose: str = "system_one",
    retries: int | None = None,
) -> dict[str, Any]:
    """调用 System One，返回完整响应 dict（含 answers）。

    对端断连、超时、5xx/429 会自动重试；最终仍失败则抛 JevError（供上层降级规则）。
    """
    if not questions:
        raise JevError("questions 不能为空")
    key = (os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if not key:
        raise JevError("未配置 JEV_API_KEY / TYPESAFE_API_KEY")

    provider = (os.environ.get("JEV_PROVIDER") or "typesafe").strip().lower()
    base = (os.environ.get("JEV_BASE_URL") or os.environ.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
    model_name = (model or os.environ.get("JEV_MODEL") or os.environ.get("TYPESAFE_MODEL") or "jev-latest").strip()
    url = _endpoint(provider, base)
    max_attempts = max(1, retries if retries is not None else _default_retries())

    payload = {
        "model": model_name,
        "state": state,
        "questions": questions,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_err: str | None = None
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "Connection": "close",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                err = f"响应不是 JSON：{raw[:400]}"
                _log_jev(purpose, payload, url, model_name, None, False, err, elapsed_ms, attempt=attempt)
                raise JevError(err) from exc
            if not isinstance(data, dict):
                err = f"响应根节点不是对象：{type(data)}"
                _log_jev(purpose, payload, url, model_name, data, False, err, elapsed_ms, attempt=attempt)
                raise JevError(err)
            if "answers" not in data:
                err = (
                    "该地址返回的不是 System One 格式（缺少 answers）。"
                    "请检查是否误填了大模型 chat/completions 地址。"
                    f" raw={raw[:400]}"
                )
                _log_jev(purpose, payload, url, model_name, data, False, err, elapsed_ms, attempt=attempt)
                raise JevError(err)
            _log_jev(purpose, payload, url, model_name, data, True, None, elapsed_ms, attempt=attempt)
            return data
        except JevError as exc:
            # 业务/格式错误不重试
            if not _is_transient_message(str(exc)) or attempt >= max_attempts:
                raise
            last_err = str(exc)
        except urllib.error.HTTPError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            raw = exc.read().decode("utf-8", errors="replace")
            err = f"HTTP {exc.code}: {raw[:800]}"
            retryable = exc.code in _RETRYABLE_HTTP
            _log_jev(
                purpose, payload, url, model_name, None, False, err, elapsed_ms,
                attempt=attempt, retryable=retryable,
            )
            if not retryable or attempt >= max_attempts:
                raise JevError(err) from exc
            last_err = err
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            err = _format_network_err(exc)
            retryable = _is_transient_exc(exc)
            _log_jev(
                purpose, payload, url, model_name, None, False, err, elapsed_ms,
                attempt=attempt, retryable=retryable,
            )
            if not retryable or attempt >= max_attempts:
                raise JevError(err) from exc
            last_err = err

        # 指数退避：1s、2s、4s…（对端断连后稍等再连）
        wait = min(8.0, float(2 ** (attempt - 1)))
        time.sleep(wait)

    raise JevError(last_err or "Jev 调用失败")


def _default_retries() -> int:
    raw = (os.environ.get("JEV_RETRIES") or "3").strip()
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 3


def _format_network_err(exc: BaseException) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    # URLError 常包一层 reason
    reason = getattr(exc, "reason", None)
    if reason is not None and str(reason) not in msg:
        msg = f"{msg} ({reason})"
    return f"网络错误：{msg}"


def _is_transient_exc(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            TimeoutError,
            socket.timeout,
            ConnectionError,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            urllib.error.URLError,
        ),
    ):
        return True
    # HTTPError 已在上层按状态码判断
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_HTTP
    return _is_transient_message(str(exc))


def _is_transient_message(msg: str) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in _TRANSIENT_MARKERS)


def _log_jev(
    purpose: str,
    payload: dict[str, Any],
    url: str,
    model_name: str,
    response: Any,
    ok: bool,
    error: str | None,
    elapsed_ms: int,
    *,
    attempt: int = 1,
    retryable: bool | None = None,
) -> None:
    try:
        from admin.model_log import log_model_call

        note = error
        if not ok and attempt > 1:
            note = f"[attempt {attempt}] {error or ''}".strip()
        elif not ok and retryable:
            note = f"[retryable] {error or ''}".strip()
        log_model_call(
            kind="jev",
            purpose=purpose,
            request=payload,
            response=response,
            ok=ok,
            error=note,
            elapsed_ms=elapsed_ms,
            model=model_name,
            endpoint=url,
        )
    except Exception:
        pass


def noul(instructions: str, *, criteria: dict[str, str] | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: str, criteria: dict[str, str | None]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> dict[str, Any]:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def get_noul(answers: dict[str, Any], key: str, default: float = 0.5) -> float:
    root = answers.get("answers") if isinstance(answers.get("answers"), dict) else answers
    if not isinstance(root, dict):
        return default
    node = root.get(key)
    if isinstance(node, dict) and "noul" in node:
        try:
            return float(node["noul"])
        except (TypeError, ValueError):
            return default
    return default


def get_choice(answers: dict[str, Any], key: str, default: str = "other") -> tuple[str, float]:
    root = answers.get("answers") if isinstance(answers.get("answers"), dict) else answers
    node = root.get(key) if isinstance(root, dict) else None
    if not isinstance(node, dict):
        return default, 0.0
    ch = str(node.get("choice") or default)
    conf = node.get("confidence")
    try:
        c = float(conf) if conf is not None else float((node.get("probabilities") or {}).get(ch) or 0.0)
    except (TypeError, ValueError):
        c = 0.0
    return ch, c


def get_score(answers: dict[str, Any], key: str, default: float = 0.0) -> tuple[float, float]:
    root = answers.get("answers") if isinstance(answers.get("answers"), dict) else answers
    node = root.get(key) if isinstance(root, dict) else None
    if not isinstance(node, dict):
        return default, 0.0
    try:
        s = float(node.get("score"))
    except (TypeError, ValueError):
        s = default
    try:
        conf = float(node.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return s, conf


def _endpoint(provider: str, base: str) -> str:
    if "systemone" in base:
        return base
    if provider in {"typesafe", "custom", ""}:
        return base.rstrip("/") + "/v1/systemone"
    if provider == "openrouter":
        # OpenRouter 上 TypeSafe 仍走 systemone 形态网关时用此路径；否则用户可把 base 写成完整 URL
        if base.rstrip("/").endswith("/api"):
            return base.rstrip("/") + "/v1/systemone"
        return base.rstrip("/") + "/v1/systemone"
    if provider in {"vercel", "vercel_ai_gateway", "ai-gateway"}:
        return base.rstrip("/") + "/v1/systemone"
    return base.rstrip("/") + "/v1/systemone"
