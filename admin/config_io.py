"""原子读写 .env / policy.yaml；密钥只存服务端。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from admin.paths import ENV_PATH, POLICY_PATH, ROOT

_KEY_RE = re.compile(r"(?i)(api[_-]?key|secret|token|password)")


def mask_secret(value: str | None) -> dict[str, Any]:
    """返回已配置状态与末四位掩码，绝不回显完整密钥。"""
    raw = (value or "").strip()
    if not raw:
        return {"configured": False, "mask": ""}
    tail = raw[-4:] if len(raw) >= 4 else raw
    return {"configured": True, "mask": f"****{tail}"}


def read_env_map(path: Path | None = None) -> dict[str, str]:
    """解析 .env 为有序 dict（保留注释外的 KEY=VALUE）。"""
    target = path or ENV_PATH
    result: dict[str, str] = {}
    if not target.exists():
        example = ROOT / ".env.example"
        if example.exists():
            shutil.copy(example, target)
        else:
            target.write_text("", encoding="utf-8")
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        result[key] = value
    return result


def write_env_updates(updates: dict[str, str | None], path: Path | None = None) -> None:
    """就地更新 .env 中的键；None 表示清除该键值（保留键名）。不覆盖未提及的变量。"""
    target = path or ENV_PATH
    if not target.exists():
        target.write_text("", encoding="utf-8")
    _backup(target)
    lines = target.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.partition("=")[0].strip()
        if key in updates:
            val = updates[key]
            out.append(f"{key}=" if val is None else f"{key}={val}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key in seen:
            continue
        out.append(f"{key}=" if val is None else f"{key}={val}")
    _atomic_write_text(target, "\n".join(out) + ("\n" if out else ""))


def load_env_into_os() -> None:
    """把 .env 注入进程环境（不覆盖已有非空环境变量以外：以文件为准覆盖 LLM_/JEV_）。

    总控台演示时可由 `_apply_model_env_from_db` 设置 `BOSS_MODEL_ENV_LOCKED=1`，
    避免仓库 .env 盖掉总控 DB 下发的密钥。
    """
    locked = (os.environ.get("BOSS_MODEL_ENV_LOCKED") or "").strip() in {"1", "true", "yes", "on"}
    for key, value in read_env_map().items():
        if key.startswith(("LLM_", "JEV_", "TYPESAFE_", "OPENAI_", "BOSS_")):
            if locked:
                continue
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = value


def read_policy() -> dict[str, Any]:
    if not POLICY_PATH.exists():
        raise FileNotFoundError(f"缺少 {POLICY_PATH}")
    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("policy.yaml 根节点必须是 mapping")
    # 硬锁定：仅 not-fit 禁止自动；全自动发送（AI 当 HR）
    actions = data.setdefault("actions", {})
    actions["not-fit"] = False
    data["send_mode"] = "auto_send"
    return data


def write_policy(data: dict[str, Any]) -> None:
    actions = data.setdefault("actions", {})
    actions["not-fit"] = False
    data["send_mode"] = "auto_send"
    _backup(POLICY_PATH)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    _atomic_write_text(POLICY_PATH, text)


_DAILY_STAT_KEYS = ("today_auto_sent", "today_handoff", "today_greet")


def ensure_daily_stats(policy: dict[str, Any], *, today: str | None = None) -> tuple[dict[str, Any], bool]:
    """确保 stats.today_date 为当天；跨日则清零全部日计数（含打招呼）。

    返回 (stats, rolled)。会原地修改 policy，调用方负责 write_policy。
    """
    from datetime import date as _date

    day = today or _date.today().isoformat()
    st = policy.setdefault("stats", {})
    if not isinstance(st, dict):
        st = {}
        policy["stats"] = st
    if st.get("today_date") == day:
        return st, False
    st["today_date"] = day
    for key in _DAILY_STAT_KEYS:
        st[key] = 0
    return st, True


def bump_daily_stat(key: str, n: int = 1) -> dict[str, Any]:
    """跨日安全地 +n 某个日计数并落盘。"""
    policy = read_policy()
    st, _ = ensure_daily_stats(policy)
    st[key] = int(st.get(key) or 0) + int(n)
    write_policy(policy)
    return st


def llm_public_view(env: dict[str, str] | None = None) -> dict[str, Any]:
    e = env or read_env_map()
    return {
        "enabled": _truthy(e.get("LLM_ENABLED")),
        "provider": e.get("LLM_PROVIDER") or "deepseek",
        "base_url": e.get("LLM_BASE_URL") or "",
        "model": e.get("LLM_MODEL") or "",
        "timeout": _int(e.get("LLM_TIMEOUT"), 30),
        "max_tokens": _int(e.get("LLM_MAX_TOKENS"), 1600),
        "temperature": _float(e.get("LLM_TEMPERATURE"), 0.3),
        "api_key": mask_secret(e.get("LLM_API_KEY")),
    }


def jev_public_view(env: dict[str, str] | None = None) -> dict[str, Any]:
    e = env or read_env_map()
    return {
        "enabled": _truthy(e.get("JEV_ENABLED")),
        "provider": e.get("JEV_PROVIDER") or "typesafe",
        "base_url": e.get("JEV_BASE_URL") or "",
        "model": e.get("JEV_MODEL") or "jev-latest",
        "api_key": mask_secret(e.get("JEV_API_KEY") or e.get("TYPESAFE_API_KEY")),
    }


def apply_llm_save(payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, str | None] = {
        "LLM_ENABLED": "true" if payload.get("enabled") else "false",
        "LLM_PROVIDER": str(payload.get("provider") or "custom"),
        "LLM_BASE_URL": str(payload.get("base_url") or "").strip(),
        "LLM_MODEL": str(payload.get("model") or "").strip(),
        "LLM_TIMEOUT": str(int(payload.get("timeout") or 30)),
        "LLM_MAX_TOKENS": str(int(payload.get("max_tokens") or 1600)),
        "LLM_TEMPERATURE": str(float(payload.get("temperature") or 0.3)),
    }
    new_key = payload.get("api_key")
    if isinstance(new_key, str) and new_key.strip() and not new_key.startswith("****"):
        updates["LLM_API_KEY"] = new_key.strip()
        updates["OPENAI_API_KEY"] = new_key.strip()
    if updates["LLM_BASE_URL"]:
        updates["OPENAI_BASE_URL"] = updates["LLM_BASE_URL"]
    if updates["LLM_MODEL"]:
        updates["OPENAI_MODEL"] = updates["LLM_MODEL"]
    _validate_url(updates["LLM_BASE_URL"] or "")
    write_env_updates(updates)
    load_env_into_os()
    return llm_public_view()


def apply_jev_save(payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, str | None] = {
        "JEV_ENABLED": "true" if payload.get("enabled") else "false",
        "JEV_PROVIDER": str(payload.get("provider") or "typesafe"),
        "JEV_BASE_URL": str(payload.get("base_url") or "").strip(),
        "JEV_MODEL": str(payload.get("model") or "jev-latest").strip(),
    }
    new_key = payload.get("api_key")
    if isinstance(new_key, str) and new_key.strip() and not new_key.startswith("****"):
        updates["JEV_API_KEY"] = new_key.strip()
        updates["TYPESAFE_API_KEY"] = new_key.strip()
    _validate_url(updates["JEV_BASE_URL"] or "")
    write_env_updates(updates)
    load_env_into_os()
    return jev_public_view()


def clear_llm_key() -> None:
    write_env_updates({"LLM_API_KEY": "", "OPENAI_API_KEY": ""})
    load_env_into_os()


def clear_jev_key() -> None:
    write_env_updates({"JEV_API_KEY": "", "TYPESAFE_API_KEY": ""})
    load_env_into_os()


def _validate_url(url: str) -> None:
    if not url:
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("API 地址必须以 http:// 或 https:// 开头")


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v is not None and str(v).strip() != "" else default
    except ValueError:
        return default


def _float(v: str | None, default: float) -> float:
    try:
        return float(v) if v is not None and str(v).strip() != "" else default
    except ValueError:
        return default


def _backup(path: Path) -> None:
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
