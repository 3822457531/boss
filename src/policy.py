"""策略与环境加载；多职位匹配与 FAQ 合并。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

_ROOT = Path(__file__).resolve().parents[1]


def load_env(path: str | Path = ".env") -> None:
    """轻量加载 .env（KEY=VALUE），LLM_/JEV_ 以文件为准写入进程环境。"""
    env_path = Path(path)
    if not env_path.is_absolute():
        root_env = _ROOT / ".env"
        if root_env.exists():
            env_path = root_env
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if key.startswith(("LLM_", "JEV_", "TYPESAFE_", "OPENAI_", "BOSS_")):
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = value


def load_policy(path: str | Path) -> dict[str, Any]:
    """读取 policy.yaml。"""
    policy_path = Path(path)
    if not policy_path.is_absolute():
        cand = _ROOT / policy_path
        if cand.exists():
            policy_path = cand
    if not policy_path.exists():
        raise FileNotFoundError(f"policy 不存在：{policy_path}")
    text = policy_path.read_text(encoding="utf-8")
    if yaml is None:
        raise RuntimeError("需要 PyYAML：pip install pyyaml")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("policy 根节点必须是 mapping")
    return data


def get_job(policy: dict[str, Any], job_id: str | None) -> dict[str, Any]:
    """按 id 取岗位配置（兼容旧 jobs[]；优先用 positions 推导）。"""
    # 新架构：用 positions 合成假 job，保留 answers 等字段兼容
    pos_block = policy.get("positions") or {}
    items = list(pos_block.get("items") or [])
    if items:
        if job_id:
            for it in items:
                if str(it.get("id") or "") == job_id:
                    return _position_as_job(it, policy)
        # 无 id：返回第一个 enabled open，否则第一项
        for it in items:
            if it.get("enabled") and _status_key(it.get("status")) == "open":
                return _position_as_job(it, policy)
        return _position_as_job(items[0], policy)

    jobs = policy.get("jobs") or []
    if not jobs:
        return {
            "id": "default",
            "title": "默认岗位",
            "city": "",
            "salary": "",
            "requirements": "",
            "answers": {},
        }
    if job_id is None:
        return jobs[0]
    for job in jobs:
        if job.get("id") == job_id:
            return job
    raise KeyError(f"找不到岗位 id={job_id!r}")


def _position_as_job(pos: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    legacy = (policy.get("jobs") or [{}])[0] if policy.get("jobs") else {}
    answers = {}
    if isinstance(legacy, dict) and isinstance(legacy.get("answers"), dict):
        answers = dict(legacy["answers"])
    return {
        "id": str(pos.get("id") or "default"),
        "title": str(pos.get("title") or "默认岗位"),
        "city": str(pos.get("city") or legacy.get("city") or ""),
        "salary": str(pos.get("salary") or legacy.get("salary") or ""),
        "requirements": str(pos.get("requirements") or legacy.get("requirements") or ""),
        "answers": answers,
        "position": pos,
    }


def _status_key(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in {"open", "开放", "开放中", "招聘中", "在招"}:
        return "open"
    if s in {"pending", "待开放", "未开放", "暂停"}:
        return "pending"
    if s in {"closed", "已关闭", "关闭", "停招", "下线"}:
        return "closed"
    # 中文原文
    t = str(raw or "")
    if "开放中" in t or t == "开放":
        return "open"
    if "待开放" in t:
        return "pending"
    if "关闭" in t:
        return "closed"
    return s or "open"


def position_items(policy: dict[str, Any]) -> list[dict[str, Any]]:
    block = policy.get("positions") or {}
    items = block.get("items") or []
    return list(items) if isinstance(items, list) else []


def position_stats(policy: dict[str, Any]) -> dict[str, int]:
    items = position_items(policy)
    stats = {"total": len(items), "open": 0, "pending": 0, "closed": 0, "other": 0}
    for it in items:
        k = _status_key(it.get("status"))
        if k in stats:
            stats[k] += 1
        else:
            stats["other"] += 1
    return stats


def normalize_title(title: str) -> str:
    t = (title or "").strip().lower()
    t = re.sub(r"[\s\-_/｜|·•]+", "", t)
    return t


def position_id_from_title(title: str) -> str:
    n = normalize_title(title) or "position"
    digest = hashlib.sha1(n.encode("utf-8")).hexdigest()[:10]
    return f"boss-{digest}"


def match_position(
    policy: dict[str, Any],
    chat_position: str | None,
    *,
    require_enabled: bool = False,
) -> dict[str, Any] | None:
    """用沟通职位字符串匹配 positions.items。"""
    needle = (chat_position or "").strip()
    if not needle:
        return None
    nn = normalize_title(needle)
    items = position_items(policy)
    # 精确 title
    for it in items:
        if require_enabled and not it.get("enabled", True):
            continue
        title = str(it.get("title") or "").strip()
        if title and (title == needle or normalize_title(title) == nn):
            return it
    # aliases
    for it in items:
        if require_enabled and not it.get("enabled", True):
            continue
        aliases = it.get("aliases") or []
        for a in aliases:
            a = str(a or "").strip()
            if not a:
                continue
            if a == needle or normalize_title(a) == nn or a in needle or needle in a:
                return it
    # 模糊包含
    for it in items:
        if require_enabled and not it.get("enabled", True):
            continue
        title = str(it.get("title") or "").strip()
        if title and (title in needle or needle in title):
            return it
    return None


def resolve_position(
    policy: dict[str, Any],
    chat_position: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any] | None:
    if job_id:
        for it in position_items(policy):
            if str(it.get("id") or "") == job_id:
                return it
    return match_position(policy, chat_position)


def faq_path_common(policy: dict[str, Any]) -> Path:
    knowledge = policy.get("knowledge") or {}
    rel = knowledge.get("faq_path") or "config/knowledge/faq.md"
    p = Path(rel)
    return p if p.is_absolute() else _ROOT / p


def positions_dir(policy: dict[str, Any]) -> Path:
    knowledge = policy.get("knowledge") or {}
    rel = knowledge.get("positions_dir") or "config/knowledge/positions"
    p = Path(rel)
    return p if p.is_absolute() else _ROOT / p


def default_faq_path_for_title(policy: dict[str, Any], title: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "_", (title or "position").strip()) or "position"
    rel_dir = (policy.get("knowledge") or {}).get("positions_dir") or "config/knowledge/positions"
    return f"{rel_dir.rstrip('/')}/{safe}.md"


def parse_faq_sections(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for block in re.split(r"\n##\s+", text or ""):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            block = block[3:]
        title, _, body = block.partition("\n")
        title = title.strip().lstrip("#").strip()
        body = body.strip()
        if title and body:
            items.append({"title": title, "body": body})
    return items


def faq_sections_to_markdown(sections: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for it in sections:
        q = (it.get("title") or it.get("question") or "").strip()
        a = (it.get("body") or it.get("answer") or "").strip()
        if not q:
            continue
        parts.append(f"## {q}\n{a}\n")
    return "\n".join(parts).rstrip() + ("\n" if parts else "")


def load_faq_text(path: str | Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = _ROOT / p
    return p.read_text(encoding="utf-8") if p.exists() else ""


def merge_faq_sections(
    common: list[dict[str, str]],
    override: list[dict[str, str]],
) -> list[dict[str, str]]:
    """同 title 时职位专属覆盖公共；新 title 追加。"""
    by_title: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for sec in common:
        t = (sec.get("title") or "").strip()
        if not t:
            continue
        by_title[t] = {"title": t, "body": (sec.get("body") or "").strip()}
        order.append(t)
    for sec in override:
        t = (sec.get("title") or "").strip()
        if not t:
            continue
        if t not in by_title:
            order.append(t)
        by_title[t] = {"title": t, "body": (sec.get("body") or "").strip()}
    return [by_title[t] for t in order if t in by_title]


def load_merged_faq(
    policy: dict[str, Any],
    position: dict[str, Any] | None = None,
    chat_position: str | None = None,
) -> tuple[str, list[dict[str, str]], dict[str, Any] | None]:
    """返回 (merged_markdown, sections, matched_position)。"""
    pos = position or match_position(policy, chat_position)
    common_path = faq_path_common(policy)
    common_text = load_faq_text(common_path)
    common_secs = parse_faq_sections(common_text)
    override_secs: list[dict[str, str]] = []
    if pos:
        rel = (pos.get("faq_path") or "").strip()
        if rel:
            override_secs = parse_faq_sections(load_faq_text(rel))
    merged = merge_faq_sections(common_secs, override_secs)
    return faq_sections_to_markdown(merged), merged, pos
