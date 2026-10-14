"""从 boss positions 同步职位列表，保留本地 aliases / faq_path / enabled。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from admin.boss_runner import run_boss
from admin.config_io import read_policy, write_policy
from admin.paths import ROOT
from src.policy import (
    _status_key,
    default_faq_path_for_title,
    normalize_title,
    position_id_from_title,
    position_stats,
)

# 官方 CLI 行：`1. 标题｜状态:开放中｜…｜ID:1`
_NUMBERED_JOB_RE = re.compile(
    r"^\s*(?P<num>\d+)[\.\)、]\s*(?P<title>.+?)(?:\s*[｜|]\s*(?P<rest>.+))\s*$"
)
_LOOSE_JOB_RE = re.compile(
    r"^\s*(?P<title>.+?)(?:\s*[｜|]\s*(?P<rest>.+))\s*$"
)
# 顺序重要：待开放须先于「开放」
_STATUS_PATTERNS = (
    ("pending", re.compile(r"待开放|未开放|暂停")),
    ("closed", re.compile(r"已关闭|关闭|停招|下线")),
    ("open", re.compile(r"开放中|招聘中|在招|(?<!待)开放")),
)
_SKIP_LINE_RE = re.compile(
    r"^(来源页面|职位明细|状态统计|页面统计|已读取|共\s*\d+\s*个职位|职位|状态|title|status|---+)",
    re.I,
)
_ID_RE = re.compile(r"(?:^|[｜|\s])(?:id|job[_-]?id)\s*[=:：]\s*([^\s｜|]+)", re.I)
_STATUS_FIELD_RE = re.compile(r"状态\s*[=:：]\s*([^｜|]+)")


def parse_positions_output(raw: str) -> list[dict[str, Any]]:
    """解析 boss positions 文本输出为职位列表。"""
    text = (raw or "").strip()
    if not text:
        return []
    # JSON 数组兜底
    if text.startswith("[") or text.startswith("{"):
        try:
            import json

            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("positions") or data.get("items") or data.get("data") or []
            if isinstance(data, list):
                out: list[dict[str, Any]] = []
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    title = str(row.get("title") or row.get("name") or row.get("jobName") or "").strip()
                    if not title:
                        continue
                    status = _status_key(row.get("status") or row.get("state") or "open")
                    pid = str(row.get("id") or row.get("jobId") or "").strip() or position_id_from_title(title)
                    out.append({"id": pid, "title": title, "status": status})
                if out:
                    return out
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if _SKIP_LINE_RE.match(line):
            continue
        if "状态统计" in line or "页面统计" in line:
            continue
        if "://" in line or line.lower().startswith("http"):
            continue

        m = _NUMBERED_JOB_RE.match(line) or (
            _LOOSE_JOB_RE.match(line) if "状态" in line or "开放" in line or "关闭" in line else None
        )
        if not m:
            continue
        title = (m.group("title") or "").strip()
        rest = (m.groupdict().get("rest") or "").strip()
        title = re.sub(r"^(职位|岗位)\s*[：:]\s*", "", title).strip()
        if not title or len(title) > 120:
            continue
        if _SKIP_LINE_RE.match(title):
            continue
        if "://" in title:
            continue

        status = _extract_status(rest, title)
        # 从标题里剥掉误拼的状态词
        for key, pat in _STATUS_PATTERNS:
            if pat.search(title) and key == status:
                title = pat.sub("", title).strip(" ｜|-")
                break
        if not title:
            continue

        pid = ""
        id_m = _ID_RE.search(rest)
        if id_m:
            pid = id_m.group(1).strip()
        if not pid and "num" in m.groupdict() and m.group("num"):
            # 无显式 ID 时用序号，避免同名岗互相覆盖
            pid = str(m.group("num")).strip()
        if not pid:
            pid = position_id_from_title(title)

        # 同名可有多条（开放中 + 已关闭）；按 id 去重
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        items.append({"id": pid, "title": title, "status": status})
    return items


def _extract_status(rest: str, title: str) -> str:
    field = _STATUS_FIELD_RE.search(rest or "")
    if field:
        return _status_key(field.group(1).strip())
    blob = f"{rest} {title}"
    for key, pat in _STATUS_PATTERNS:
        if pat.search(blob):
            return key
    if rest:
        first = re.split(r"[｜|]", rest)[0].strip()
        return _status_key(first)
    return "open"


def sync_positions_from_boss(*, timeout: float = 120.0) -> dict[str, Any]:
    """调用 boss positions，upsert 到 policy.positions。"""
    result = run_boss(["positions"], timeout=timeout, confirmed=True)
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    raw = stdout if stdout.strip() else stderr
    exit_code = result.get("exit_code", result.get("returncode"))
    if exit_code not in (0, None) and not raw.strip():
        return {
            "ok": False,
            "error": f"boss positions 失败：exit_code={exit_code} {stderr[:400]}",
            "boss": result,
        }
    parsed = parse_positions_output(raw)
    if not parsed:
        parsed = parse_positions_output(f"{stdout}\n{stderr}")
    if not parsed:
        hint = ""
        if "未读取到职位" in raw or "未读取到职位" in stderr:
            hint = "（CLI 已进职位页但未刮到职位行，请确认 runtime 内 boss-cli 已同步最新 jd.js）"
        elif "共" in raw and "职位" in raw:
            hint = "（有页面统计但无明细行，多为列表 DOM 选择器过旧）"
        return {
            "ok": False,
            "error": f"未能解析职位列表，请检查 boss positions 输出{hint}",
            "raw": raw[:2000],
            "boss": {k: result.get(k) for k in ("exit_code", "argv", "elapsed_ms")},
        }

    # 丢掉历史误解析的噪声项（来源页面 URL 等）
    parsed = [p for p in parsed if "://" not in str(p.get("title") or "") and not str(p.get("title") or "").startswith("来源")]

    policy = read_policy()
    knowledge = policy.setdefault("knowledge", {})
    knowledge.setdefault("faq_path", "config/knowledge/faq.md")
    knowledge.setdefault("positions_dir", "config/knowledge/positions")

    old_block = policy.get("positions") or {}
    old_items = [
        it
        for it in list(old_block.get("items") or [])
        if "://" not in str(it.get("title") or "") and not str(it.get("title") or "").startswith("来源")
    ]
    by_id = {str(it.get("id") or ""): it for it in old_items if it.get("id")}
    # 仅当 id 对不上时，用「同名且同状态」兜底，避免开放中覆盖已关闭
    by_title_status = {
        (normalize_title(str(it.get("title") or "")), _status_key(it.get("status"))): it
        for it in old_items
    }

    merged: list[dict[str, Any]] = []
    for row in parsed:
        title = row["title"]
        status = _status_key(row["status"])
        nt = normalize_title(title)
        prev = by_id.get(str(row["id"])) or by_title_status.get((nt, status)) or {}
        faq_path = (prev.get("faq_path") or "").strip()
        if not faq_path and status == "open":
            faq_path = default_faq_path_for_title(policy, title)

        # 状态以 Boss 为准；非开放默认停用（可稍后手动再开）
        if status == "open":
            enabled = bool(prev["enabled"]) if "enabled" in prev else True
        else:
            enabled = False

        item = {
            "id": str(row["id"]),
            "title": title,
            "status": status,
            "aliases": list(prev.get("aliases") or _seed_aliases(title)),
            "faq_path": faq_path,
            "enabled": enabled,
        }
        merged.append(item)

    policy["positions"] = {
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "items": merged,
    }
    write_policy(policy)

    pos_dir = ROOT / str(knowledge.get("positions_dir") or "config/knowledge/positions")
    pos_dir.mkdir(parents=True, exist_ok=True)

    stats = position_stats(policy)
    return {
        "ok": True,
        "synced_at": policy["positions"]["synced_at"],
        "count": len(merged),
        "stats": stats,
        "items": merged,
        "raw_preview": raw[:800],
    }


def list_positions_view() -> dict[str, Any]:
    policy = read_policy()
    block = policy.get("positions") or {}
    items = list(block.get("items") or [])
    return {
        "synced_at": block.get("synced_at") or "",
        "stats": position_stats(policy),
        "items": items,
        "knowledge": {
            "faq_path": (policy.get("knowledge") or {}).get("faq_path") or "config/knowledge/faq.md",
            "positions_dir": (policy.get("knowledge") or {}).get("positions_dir")
            or "config/knowledge/positions",
        },
    }


def update_position(pos_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    policy = read_policy()
    block = policy.setdefault("positions", {})
    items = list(block.get("items") or [])
    found = None
    for it in items:
        if str(it.get("id") or "") == pos_id:
            found = it
            break
    if not found:
        raise KeyError(f"职位不存在：{pos_id}")
    if "aliases" in patch and isinstance(patch["aliases"], list):
        found["aliases"] = [str(a).strip() for a in patch["aliases"] if str(a).strip()]
    if "enabled" in patch:
        found["enabled"] = bool(patch["enabled"])
    if "faq_path" in patch:
        found["faq_path"] = str(patch.get("faq_path") or "").strip()
    if "title" in patch and str(patch.get("title") or "").strip():
        found["title"] = str(patch["title"]).strip()
    block["items"] = items
    write_policy(policy)
    return found


def _seed_aliases(title: str) -> list[str]:
    t = (title or "").strip()
    aliases: list[str] = []
    mapping = {
        "人事": ["人事", "hr", "招聘专员", "前台"],
        "主播": ["主播", "直播"],
        "歌手": ["歌手", "驻唱", "唱歌"],
        "前台": ["前台", "行政前台"],
    }
    for key, vals in mapping.items():
        if key in t:
            aliases.extend(vals)
    out: list[str] = []
    for a in aliases:
        if a not in out:
            out.append(a)
    return out
