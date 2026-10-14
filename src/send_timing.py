"""发送节奏：两道闸，语义固定。

① 发出前停顿：只有真正要点「发送」时才等 —— 字越多停越久。
② 换人歇一歇：处理完一个人再接下一位。发过/动过用满额；跳过只短歇。

界面、日志、自动回复、打招呼都用上述说法，不要再拆成
「打字延迟 / 每人间隔 / skip 也睡」三套钟。
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Callable

# 跳过/失败时换人短歇（秒），不走满额间隔
_SKIP_GAP_SEC = 3.0

# 真正发过或动过平台时，用满额换人间隔
_FULL_GAP_OUTCOMES = frozenset(
    {"sent", "wechat", "action", "handoff", "draft", "invite", "greet"}
)

# 内置打字速度（秒/字），界面不再暴露「每字秒数」以免和换人间隔混淆
_SEC_PER_CHAR_LO = 0.07
_SEC_PER_CHAR_HI = 0.12


def _rate(policy: dict[str, Any] | None) -> dict[str, Any]:
    return (policy or {}).get("rate_limit") or {}


def think_before_send_sec(text: str, policy: dict[str, Any] | None = None) -> float:
    """算出「发出前停顿」秒数。关闭则 0。"""
    rate = _rate(policy)
    if rate.get("typing_delay_enabled", True) is False:
        return 0.0
    n = len((text or "").strip())
    lo = float(rate.get("typing_delay_min_sec") or 1.5)
    hi = float(rate.get("typing_delay_max_sec") or 12.0)
    if hi < lo:
        hi = lo
    if n <= 0:
        return lo
    # 兼容旧配置里的每字秒数；没有则用内置速度
    per_lo = float(rate.get("typing_sec_per_char_min") or _SEC_PER_CHAR_LO)
    per_hi = float(rate.get("typing_sec_per_char_max") or _SEC_PER_CHAR_HI)
    if per_hi < per_lo:
        per_hi = per_lo
    delay = n * random.uniform(per_lo, per_hi)
    return max(lo, min(hi, delay))


def sleep_before_send(
    text: str,
    policy: dict[str, Any] | None = None,
    *,
    log: Callable[..., None] | None = None,
) -> float:
    """闸①：boss send 前调用。返回实际等待秒数。"""
    sec = think_before_send_sec(text, policy)
    if sec <= 0:
        return 0.0
    if log:
        log(f"发出前停顿 {sec:.1f}s（{len((text or '').strip())} 字，像在打字）…")
    time.sleep(sec)
    return sec


# 旧名兼容
typing_delay_sec = think_before_send_sec


def person_gap_range(policy: dict[str, Any] | None = None) -> tuple[float, float]:
    """闸②满额换人间隔的 [min, max] 秒。"""
    rate = _rate(policy)
    lo = float(rate.get("send_interval_min_sec") or 15)
    hi = float(rate.get("send_interval_max_sec") or 30)
    if hi < lo:
        hi = lo
    return lo, hi


def person_gap_sec(
    outcome: str | None,
    policy: dict[str, Any] | None = None,
) -> float:
    """按本轮结果决定换人要歇多久。

    - 发过 / 点过动作 / 转人工：满额随机间隔
    - 跳过、出错等：只歇一小会，不白等满间隔
    """
    key = (outcome or "skip").strip().lower()
    if key in _FULL_GAP_OUTCOMES:
        lo, hi = person_gap_range(policy)
        return float(random.uniform(lo, hi))
    rate = _rate(policy)
    short = float(rate.get("skip_gap_sec") or _SKIP_GAP_SEC)
    return max(0.5, short)


def _emit_log(log: Callable[..., None] | None, msg: str, *, replace_last: bool = False) -> None:
    if log is None:
        return
    try:
        log(msg, replace_last=replace_last)
    except TypeError:
        if not replace_last:
            log(msg)


def sleep_with_countdown(
    sec: float,
    *,
    prefix: str,
    label: str = "",
    log: Callable[..., None] | None = None,
    stop_wait: Callable[[float], bool] | None = None,
    on_tick: Callable[[int, str], None] | None = None,
) -> float:
    """通用倒计时等待：日志末行原地刷新，并回调 on_tick(remaining, label)。"""
    if sec <= 0:
        return 0.0
    total_i = max(1, int(round(sec)))
    tag = label or ""
    _emit_log(log, f"{prefix} {total_i}s" + (f"（{tag}）" if tag else ""))
    if on_tick:
        on_tick(total_i, tag)

    deadline = time.monotonic() + sec
    last_rem: int | None = total_i
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            rem = max(1, int(math.ceil(left)))
            if rem != last_rem:
                last_rem = rem
                _emit_log(
                    log,
                    f"{prefix} {rem}s" + (f"（{tag}）" if tag else ""),
                    replace_last=True,
                )
                if on_tick:
                    on_tick(rem, tag)
            chunk = min(0.25, left)
            if stop_wait is not None:
                if stop_wait(chunk):
                    break
            else:
                time.sleep(chunk)
    finally:
        if on_tick:
            on_tick(0, tag)
    return sec


def sleep_after_person(
    outcome: str | None,
    policy: dict[str, Any] | None = None,
    *,
    log: Callable[..., None] | None = None,
    stop_wait: Callable[[float], bool] | None = None,
    on_tick: Callable[[int, str], None] | None = None,
) -> float:
    """闸②：处理完一个人后调用。

    stop_wait: 可选，签名 wait(sec)->bool，True 表示被外部停止（如 Event.wait）。
    on_tick: 可选，每秒回调 (remaining_sec, label)；结束时 remaining=0。
    未提供 stop_wait 则用 time.sleep。等待期间日志末行会倒计时 34→33→…
    """
    sec = person_gap_sec(outcome, policy)
    if sec <= 0:
        return 0.0
    key = (outcome or "skip").strip().lower()
    if key in _FULL_GAP_OUTCOMES:
        label = {
            "sent": "已发送",
            "wechat": "已换微信",
            "action": "已点动作",
            "handoff": "已转人工",
            "draft": "已出草稿",
            "invite": "已约面",
            "greet": "已打招呼",
        }.get(key, key)
        prefix = "换人歇一歇"
    else:
        label = "本轮未发送，不白等满间隔"
        prefix = "换人短歇"

    return sleep_with_countdown(
        sec,
        prefix=prefix,
        label=label,
        log=log,
        stop_wait=stop_wait,
        on_tick=on_tick,
    )
