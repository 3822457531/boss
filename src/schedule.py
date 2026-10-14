"""工作日程：打招呼时段 vs 回消息时段（互斥）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def norm_hm(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:02d}:00"
    if isinstance(value, float):
        return f"{int(value):02d}:00"
    text = str(value or "").strip().strip("'\"")
    if not text:
        return ""
    if ":" not in text and text.isdigit():
        return f"{int(text):02d}:00"
    parts = text.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return ""
        return f"{h:02d}:{m:02d}"
    except ValueError:
        return ""


def hm_to_min(hm: str) -> int | None:
    hm = norm_hm(hm)
    if not hm:
        return None
    h, m = map(int, hm.split(":"))
    return h * 60 + m


def window_segments(start_hm: str, end_hm: str) -> list[tuple[int, int]]:
    """把 [start, end) 拆成不跨午夜的分钟段；空窗返回 []。"""
    s = hm_to_min(start_hm)
    e = hm_to_min(end_hm)
    if s is None or e is None:
        return []
    if s == e:
        # 等长视为未配置（避免「全天」与另一段永远冲突说不清）
        return []
    if s < e:
        return [(s, e)]
    return [(s, 24 * 60), (0, e)]


def windows_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    segs_a = window_segments(a_start, a_end)
    segs_b = window_segments(b_start, b_end)
    if not segs_a or not segs_b:
        return False
    for a0, a1 in segs_a:
        for b0, b1 in segs_b:
            if a0 < b1 and b0 < a1:
                return True
    return False


def in_window(start_hm: str, end_hm: str, *, now: datetime | None = None) -> bool:
    """当前是否在 [start, end)。未配置（空或起止相同）视为全天可用。"""
    segs = window_segments(start_hm, end_hm)
    if not segs:
        return True
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    return any(a <= cur < b for a, b in segs)


def read_schedule(policy: dict[str, Any] | None) -> dict[str, str]:
    """统一读日程；兼容旧 greet.window_*。"""
    p = policy or {}
    sch = dict(p.get("schedule") or {})
    g = p.get("greet") or {}
    greet_start = norm_hm(sch.get("greet_start") or g.get("window_start") or "")
    greet_end = norm_hm(sch.get("greet_end") or g.get("window_end") or "")
    reply_start = norm_hm(sch.get("reply_start") or "")
    reply_end = norm_hm(sch.get("reply_end") or "")
    return {
        "greet_start": greet_start,
        "greet_end": greet_end,
        "reply_start": reply_start,
        "reply_end": reply_end,
    }


def validate_schedule(sch: dict[str, Any]) -> str | None:
    """有重叠则返回错误文案；通过返回 None。"""
    gs = norm_hm(sch.get("greet_start"))
    ge = norm_hm(sch.get("greet_end"))
    rs = norm_hm(sch.get("reply_start"))
    re_ = norm_hm(sch.get("reply_end"))
    greet_set = bool(gs and ge and gs != ge)
    reply_set = bool(rs and re_ and rs != re_)
    if greet_set and reply_set and windows_overlap(gs, ge, rs, re_):
        return f"打招呼时段（{gs}–{ge}）与回消息时段（{rs}–{re_}）有重叠，请改成互不交叉"
    if greet_set and (not gs or not ge):
        return "打招呼时段请成对填写开始与结束"
    if reply_set and (not rs or not re_):
        return "回消息时段请成对填写开始与结束"
    return None


def schedule_public(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    sch = read_schedule(policy)
    now = datetime.now()
    return {
        **sch,
        "greet_active_now": in_window(sch["greet_start"], sch["greet_end"], now=now),
        "reply_active_now": in_window(sch["reply_start"], sch["reply_end"], now=now),
        "overlap": windows_overlap(
            sch["greet_start"], sch["greet_end"], sch["reply_start"], sch["reply_end"]
        ),
    }
