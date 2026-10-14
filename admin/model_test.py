"""大模型 / Jev 连接测试（密钥不出服务端）。"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from admin.config_io import load_env_into_os, read_env_map


def test_llm_connection() -> dict[str, Any]:
    env = read_env_map()
    base = (env.get("LLM_BASE_URL") or "").rstrip("/")
    key = (env.get("LLM_API_KEY") or "").strip()
    model = (env.get("LLM_MODEL") or "").strip()
    timeout = int(env.get("LLM_TIMEOUT") or 30)
    if not base or not key or not model:
        return {"ok": False, "error": "请先保存 base_url、API Key 与模型名", "human": "配置不完整"}
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "回复一个字：好"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    started = time.perf_counter()
    try:
        data, raw = _post_json(url, payload, key, timeout)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        content = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        try:
            from admin.model_log import log_model_call

            log_model_call(
                kind="llm",
                purpose="connection_test",
                request=payload,
                response=data,
                ok=True,
                elapsed_ms=elapsed_ms,
                model=model,
                endpoint=url,
            )
        except Exception:
            pass
        return {
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "preview": str(content).strip()[:80],
            "raw_snippet": raw[:500],
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            from admin.model_log import log_model_call

            log_model_call(
                kind="llm",
                purpose="connection_test",
                request=payload,
                response=None,
                ok=False,
                error=str(exc),
                elapsed_ms=elapsed_ms,
                model=model,
                endpoint=url,
            )
        except Exception:
            pass
        return _fail(exc, started)


def test_jev_connection() -> dict[str, Any]:
    """发已知是非题，校验 System One（answers.*.noul）。"""
    load_env_into_os()
    env = read_env_map()
    key = (env.get("JEV_API_KEY") or env.get("TYPESAFE_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "error": "请先保存 Jev 的 API Key", "human": "配置不完整"}

    # 同步到进程，供 src.jev 读取
    os.environ["JEV_API_KEY"] = key
    os.environ["JEV_BASE_URL"] = (env.get("JEV_BASE_URL") or "https://api.typesafe.ai").strip()
    os.environ["JEV_MODEL"] = (env.get("JEV_MODEL") or "jev-latest").strip()
    os.environ["JEV_PROVIDER"] = (env.get("JEV_PROVIDER") or "typesafe").strip()
    os.environ["JEV_ENABLED"] = env.get("JEV_ENABLED") or "true"

    from src.jev import JevError, get_noul, noul, system_one

    started = time.perf_counter()
    try:
        data = system_one(
            state="我今天很开心，一切都很顺利。",
            questions={
                "positive_mood": noul(
                    "这句话表达的是正面情绪吗？",
                    criteria={"true": "明显正面或积极", "false": "负面、中性或无法判断"},
                )
            },
            timeout=45,
            purpose="connection_test",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        noul_val = get_noul(data, "positive_mood", default=-1.0)
        if noul_val < 0:
            return {
                "ok": False,
                "human": "该地址返回的不是 System One 格式，请检查是否误填了大模型地址",
                "error": "missing answers.positive_mood.noul",
                "raw_snippet": json.dumps(data, ensure_ascii=False)[:800],
                "elapsed_ms": elapsed_ms,
            }
        direction_ok = noul_val >= 0.5
        return {
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "noul": noul_val,
            "direction_ok": direction_ok,
            "preview": f"noul={noul_val:.3f}",
            "model": data.get("model"),
            "raw_snippet": json.dumps(data, ensure_ascii=False)[:500],
            "warn": None if direction_ok else "格式正确但方向偏差，请人工确认模型是否为 Jev",
        }
    except JevError as exc:
        return _fail(exc, started)
    except Exception as exc:
        return _fail(exc, started)


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> tuple[dict[str, Any], str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw[:800]}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {"_raw": data}
    return data, raw


def _fail(exc: Exception, started: float) -> dict[str, Any]:
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    msg = str(exc)
    human = "连接失败"
    low = msg.lower()
    if "401" in low or "unauthorized" in low:
        human = "密钥无效或被拒绝"
    elif "400" in low or "api_usage_error" in low or "invalid request" in low:
        human = "请求格式无效（Jev 需要 state + questions，不是 chat messages）"
    elif "404" in low:
        human = "接口地址不存在（404）"
    elif "余额" in msg or "quota" in low or "insufficient" in low:
        human = "余额不足或配额耗尽"
    elif "timed out" in low or "timeout" in low:
        human = "请求超时"
    elif "不是 system one" in low or "answers" in low:
        human = "返回不是 System One 格式，请检查是否误填了大模型地址"
    return {"ok": False, "human": human, "error": msg[:800], "elapsed_ms": elapsed_ms}
