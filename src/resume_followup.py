"""收简历后的跟进队列：同意 → 确认话术 → 假装看简历 → 约面。"""

from __future__ import annotations

import json
import random
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from admin.paths import ROOT

FOLLOWUP_PATH = ROOT / "data" / "resume_followups.jsonl"
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now().astimezone()


def schedule_after_agree(
    candidate: str,
    *,
    policy: dict[str, Any],
    question: str = "",
) -> dict[str, Any]:
    """同意收简历后登记跟进：due_at = 现在 + 假装看简历随机时长。"""
    rr = policy.get("resume_review") or {}
    if rr.get("enabled") is False:
        return {}
    lo = int(rr.get("read_min_sec") or 180)
    hi = int(rr.get("read_max_sec") or 360)
    if hi < lo:
        hi = lo
    delay = random.randint(lo, hi)
    due = _now() + timedelta(seconds=delay)
    row = {
        "id": f"{candidate}-{int(_now().timestamp())}",
        "candidate": candidate,
        "question": (question or "")[:300],
        "created_at": _now().isoformat(timespec="seconds"),
        "due_at": due.isoformat(timespec="seconds"),
        "delay_sec": delay,
        "status": "pending",
        "kind": "invite_after_review",
    }
    _append(row)
    return row


def list_pending() -> list[dict[str, Any]]:
    with _lock:
        rows = _load_all()
    return [r for r in rows if r.get("status") == "pending"]


def pop_due(now: datetime | None = None) -> list[dict[str, Any]]:
    """取出已到期且仍 pending 的跟进，标记为 processing。"""
    now = now or _now()
    due_rows: list[dict[str, Any]] = []
    with _lock:
        rows = _load_all()
        changed = False
        for r in rows:
            if r.get("status") != "pending":
                continue
            try:
                due = datetime.fromisoformat(str(r.get("due_at")))
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc).astimezone()
            if due <= now:
                r["status"] = "processing"
                due_rows.append(dict(r))
                changed = True
        if changed:
            _save_all(rows)
    return due_rows


def mark_done(followup_id: str, *, ok: bool, note: str = "") -> None:
    with _lock:
        rows = _load_all()
        for r in rows:
            if r.get("id") == followup_id:
                r["status"] = "done" if ok else "failed"
                r["finished_at"] = _now().isoformat(timespec="seconds")
                r["note"] = note[:300]
                break
        _save_all(rows)


def cancel_for_candidate(candidate: str) -> int:
    n = 0
    with _lock:
        rows = _load_all()
        for r in rows:
            if r.get("candidate") == candidate and r.get("status") == "pending":
                r["status"] = "cancelled"
                n += 1
        if n:
            _save_all(rows)
    return n


def ack_template(policy: dict[str, Any]) -> str:
    rr = policy.get("resume_review") or {}
    text = (rr.get("ack_template") or "").strip()
    return text or "收到您的简历了，我先看看匹配度，稍后给您反馈～"


def invite_template(policy: dict[str, Any]) -> str:
    rr = policy.get("resume_review") or {}
    text = (rr.get("invite_template") or "").strip()
    return (
        text
        or "简历看过了，整体还不错。方便的话想邀您面试聊聊，您这周哪个时间段比较空？"
        "线上/线下都可以按您方便安排。"
    )


def looks_like_resume_card(text: str) -> bool:
    """候选人/系统侧附件简历确认卡类文案（需点同意/拒绝）。"""
    t = text or ""
    keys = (
        "附件简历",
        "请求您接收",
        "向您发送了附件",
        "发来一份附件简历",
        "简历确认",
        "同意接收后即可查看",
        "点击同意",
        "请查收我的简历",
        "已发送附件简历",
        "给您发了简历",
        "简历发给您了",
        "发送了简历",
        "发简历给您",
    )
    return any(k in t for k in keys)


def looks_like_resume_file(text: str) -> bool:
    """对方已直接发来的附件文件气泡（PDF 等，通常无需再点同意）。"""
    t = (text or "").strip()
    if not t:
        return False
    tl = t.lower()
    keys = (
        "点击预览附件",
        "预览附件简历",
        "点击预览附件简历",
        "附件简历.pdf",
    )
    if any(k in t for k in keys):
        return True
    if ".pdf" in tl and ("简历" in t or "resume" in tl or "预览" in t or "附件" in t):
        return True
    # 纯文件名：xxx简历.pdf / 璎璎简历.pdf
    if tl.endswith(".pdf") and ("简历" in t or "resume" in tl):
        return True
    return False


def looks_like_any_resume_inbound(text: str) -> bool:
    """确认卡或已送达附件文件。"""
    return looks_like_resume_card(text) or looks_like_resume_file(text)


def is_resume_ack_text(text: str) -> bool:
    """我方是否已发过「收到简历/先看看」类确认（含口语变体）。"""
    t = (text or "").strip()
    if not t:
        return False
    keys = (
        "收到你的简历",
        "收到您的简历",
        "收到简历",
        "先看看匹配度",
        "我先看看",
        "简历我看一下",
        "简历先收着",
        "先看一下你的简历",
        "先看下你的简历",
        "看一下你的简历",
        "看看你的简历",
        "先看看你的简历",
        "这边先看",
        "我先看下",
        "简历先看看",
    )
    if any(k in t for k in keys):
        return True
    # 口语：先看/看一下…简历；简历…先看/匹配
    if re.search(r"(收到|收着).{0,8}简历", t):
        return True
    if re.search(r"(先看|看一下|看看|看下).{0,12}简历", t):
        return True
    if re.search(r"简历.{0,12}(先看|看一下|看看|匹配|收)", t):
        return True
    return False


def looks_like_wechat_card(text: str) -> bool:
    """对方发起「交换微信」确认卡类文案（含同意/拒绝按钮）。"""
    from src.synonyms import looks_like_wechat_request, wechat_terms

    t = text or ""
    keys = (
        "交换微信",
        "我想要和您交换微信",
        "想和您交换微信",
        "是否同意",
        "请求交换微信",
        "换个微信",
        "换微信吗",
    )
    # 「是否同意」太宽，需同时带微信语义
    if "是否同意" in t and (
        "微信" in t or "wechat" in t.lower() or looks_like_wechat_request(t)
    ):
        return True
    if any(k in t for k in keys if k != "是否同意"):
        return True
    # 口语别名（绿泡泡等）出现在确认语境
    if looks_like_wechat_request(t) and any(
        x in t for x in ("同意", "请求", "交换", "想要", "吗", "？", "?")
    ):
        return True
    return False


def _append(row: dict[str, Any]) -> None:
    FOLLOWUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with FOLLOWUP_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_all() -> list[dict[str, Any]]:
    if not FOLLOWUP_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in FOLLOWUP_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _save_all(rows: list[dict[str, Any]]) -> None:
    FOLLOWUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 只保留最近 500 条，避免无限增长
    keep = rows[-500:]
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in keep)
    FOLLOWUP_PATH.write_text(text + ("\n" if keep else ""), encoding="utf-8")
