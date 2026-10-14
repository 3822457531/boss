"""全自动循环：list → chat → Jev 判断 → 生成 → 草稿/发送。"""

from __future__ import annotations

import random
import sys
import threading
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from admin.audit import audit
from admin.boss_runner import get_page_hint, leave_chat_index, run_boss
from admin.config_io import ensure_daily_stats, load_env_into_os, read_policy, write_policy
from admin.drafts import append_draft
from src.judge import Judge, action_label
from src.policy import get_job, resolve_position
from src.reply import generate_reply
from src import resume_followup as rf
from src.send_timing import sleep_after_person, sleep_before_send, sleep_with_countdown
from src.synonyms import looks_like_wechat_request
from tools.collect_history import (
    is_bogus_full_page_unread,
    parse_chat_messages,
    parse_chat_meta,
    parse_list_header_count,
    parse_list_names,
)

LogFn = Callable[[str], None]

# 无 CLI 按钮的动作 → 站内话术模板
_TEXT_ACTION_TEMPLATES: dict[str, str] = {
    "invite_interview": "方便的话想邀您面试聊聊，您这周哪个时间段比较空？线上/线下都可以按您方便安排。",
    "ask_phone": "方便的话可以留个手机号吗？方便后续约面或同步进度～",
}

# next_action → (policy actions 键, boss action 子命令)；无 CLI 的为 None
_PLATFORM_ACTIONS: dict[str, tuple[str, str] | None] = {
    "wechat": ("wechat", "wechat"),
    "request_resume": ("request-attachment-resume", "request-attachment-resume"),
    "agree_resume": ("agree-resume", "agree-resume"),
    "invite_interview": None,
    "ask_phone": None,
    "continue": None,
}


class AutoLoop:
    """后台线程循环。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.status: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "last_tick": None,
            "last_message": "未启动",
            "last_skip_reason": "",
            "wait_remaining_sec": 0,
            "wait_label": "",
            "processed": 0,
            "sent": 0,
            "handoff": 0,
            "drafts": 0,
            "errors": 0,
            "risk_stop": False,
            "browser_busy": False,
        }
        self._logs: list[str] = []
        self._lock = threading.Lock()
        # 刚发过/动过的会话：停在聊天页时对方秒回会被标已读，list --unread 永不再出
        # 记下来下一轮强制再打开检查是否有待回消息
        self._recheck: list[dict[str, Any]] = []

    def log(self, msg: str, *, replace_last: bool = False) -> None:
        now_ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            if replace_last and self._logs:
                prev = self._logs[-1]
                head = prev.split(" ", 1)[0] if prev else ""
                ts = head if len(head) == 8 and head[2] == ":" and head[5] == ":" else now_ts
                self._logs[-1] = f"{ts} {msg}"
            else:
                self._logs.append(f"{now_ts} {msg}")
                self._logs = self._logs[-200:]
            self.status["last_message"] = msg
            self.status["last_tick"] = datetime.now().isoformat(timespec="seconds")

    def _set_wait_countdown(self, remaining: int, label: str = "") -> None:
        with self._lock:
            if remaining and remaining > 0:
                self.status["wait_remaining_sec"] = int(remaining)
                self.status["wait_label"] = label or "换人歇一歇"
            else:
                self.status["wait_remaining_sec"] = 0
                self.status["wait_label"] = ""

    def logs(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def start(self) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return self.snapshot()
        self._stop.clear()
        self.status.update(
            {
                "running": True,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "risk_stop": False,
                "processed": 0,
                "sent": 0,
                "handoff": 0,
                "drafts": 0,
                "errors": 0,
                "browser_busy": False,
            }
        )
        self._thread = threading.Thread(target=self._run, name="boss-auto-loop", daemon=True)
        self._thread.start()
        audit("auto_loop_start", {})
        self.log("自动循环已启动")
        return self.snapshot()

    def stop(self, reason: str = "用户停止") -> dict[str, Any]:
        self._stop.set()
        self.status["running"] = False
        self.status["browser_busy"] = False
        self._set_wait_countdown(0)
        self.log(f"停止：{reason}")
        audit("auto_loop_stop", {"reason": reason})
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self.status, "logs": list(self._logs)[-40:]}

    def run_once(self) -> dict[str, Any]:
        """只处理当前未读一轮（不进入常驻循环）。"""
        if self._thread and self._thread.is_alive():
            raise RuntimeError("常驻循环正在运行，请先停止后再点「只跑一轮」")
        self._stop.clear()
        self.status["risk_stop"] = False
        self.log("开始：只跑一轮未读")
        audit("auto_loop_once", {})
        try:
            self._tick(once=True)
        except Exception as exc:
            self.status["errors"] = int(self.status.get("errors") or 0) + 1
            self.log(f"单轮异常：{exc}")
            raise
        self.log("单轮结束")
        return self.snapshot()

    def _run(self) -> None:
        load_env_into_os()
        while not self._stop.is_set():
            try:
                if not self._tick(once=False):
                    break
            except Exception as exc:
                self.status["errors"] = int(self.status.get("errors") or 0) + 1
                self.log(f"循环异常：{exc}")
                self.log(traceback.format_exc()[-400:])
                if self._stop.wait(15):
                    break
        self.status["running"] = False
        self.status["browser_busy"] = False
        self._set_wait_countdown(0)
        self.log("循环线程已退出")

    def _tick(self, *, once: bool = False) -> bool:
        load_env_into_os()
        policy = read_policy()
        rate = policy.get("rate_limit") or {}
        if rate.get("stop_on_captcha", True) and self.status.get("risk_stop"):
            self.stop("风控停止")
            return False

        quiet_start = _norm_hm(rate.get("quiet_start") or "22:00")
        quiet_end = _norm_hm(rate.get("quiet_end") or "08:00")

        # 假装看简历到期约面：优先处理，不受日上限/静默挡住（已承诺的跟进）
        try:
            self.status["browser_busy"] = True
            n_fu = self._process_due_followups()
            if n_fu:
                self.log(f"已处理 {n_fu} 条简历跟进（约面）")
        except Exception as exc:
            self.log(f"简历跟进异常：{exc}")
        finally:
            self.status["browser_busy"] = False

        gcfg = policy.get("greet") or {}
        # 打招呼整批占用浏览器时，可选择暂缓未读（约面跟进已在上面处理）
        if gcfg.get("pause_reply_during_greet"):
            from src.greet_loop import GREET

            if GREET.status.get("running"):
                self.log("打招呼进行中，暂缓拉取未读（约面跟进仍会处理）")
                if once:
                    return True
                return not self._stop.wait(20)

        # 回消息工作时段（与打招呼互斥）；未配置则不限制
        from src.schedule import in_window, read_schedule

        sch = read_schedule(policy)
        if sch["reply_start"] and sch["reply_end"] and sch["reply_start"] != sch["reply_end"]:
            if not in_window(sch["reply_start"], sch["reply_end"]):
                self.log(
                    f"非回消息时段（{sch['reply_start']}–{sch['reply_end']}），休眠 60s"
                    f"（打招呼请用 {sch['greet_start'] or '—'}–{sch['greet_end'] or '—'}）"
                )
                if once:
                    return True
                return not self._stop.wait(60)

        # 兼容旧「打招呼专属」开关
        win_s = (gcfg.get("window_start") or sch["greet_start"] or "").strip()
        win_e = (gcfg.get("window_end") or sch["greet_end"] or "").strip()
        if gcfg.get("exclusive_window") and win_s and win_e and win_s != win_e:
            from src.schedule import in_window as _in_w

            if _in_w(win_s, win_e):
                self.log(f"打招呼专属时段（{win_s}–{win_e}），暂缓未读回复")
                if once:
                    return True
                return not self._stop.wait(30)

        if _in_quiet_hours(quiet_start, quiet_end):
            self.log(f"静默时段（{quiet_start}–{quiet_end}），休眠 60s")
            if once:
                return True
            return not self._stop.wait(60)

        # 日上限（跨日清零 today_auto_sent / today_handoff / today_greet）
        today = date.today().isoformat()
        _, rolled = ensure_daily_stats(policy, today=today)
        if rolled:
            write_policy(policy)
        stats = policy.get("stats") or {}

        daily_max = int(rate.get("daily_max") or 80)
        if int(stats.get("today_auto_sent") or 0) >= daily_max:
            self.log(f"已达每日上限 {daily_max}，{'结束本轮' if once else '休眠'}")
            if once:
                return True
            return not self._stop.wait(120)

        self.status["browser_busy"] = True
        try:
            self.log("拉取未读列表…")
            names, listing = self._fetch_unread_names(rate)
            if listing.get("risk"):
                self.status["risk_stop"] = True
                self.log(listing.get("risk_hint") or "检测到风控")
                self.status["browser_busy"] = False
                self.stop("验证码/风控")
                return False
            if listing.get("exit_code") != 0:
                self.log(f"list 失败：{(listing.get('stderr') or listing.get('stdout') or '')[:200]}")
                self.status["browser_busy"] = False
                if once:
                    return True
                return not self._stop.wait(30)

            # 未读刚好顶满一页（常见恰好 40）= 筛选失效扫全表，禁止处理
            if listing.get("bogus_unread_cap"):
                cap = int(listing.get("unread_page_cap") or rate.get("unread_list_cap") or 40)
                header_n = listing.get("list_header_count")
                n = len(names)
                self.log(
                    f"未读名单异常：恰好 {cap} 人（抬头={header_n if header_n is not None else '?'}，"
                    f"解析={n}），疑似未读筛选失效/满页误报，本轮不处理，等待后重试…"
                )
                self.status["last_skip_reason"] = f"未读假满页(={cap})"
                self.status["last_message"] = self.status["last_skip_reason"]
                self.status["browser_busy"] = False
                # 再离开一次，避免停在脏列表态
                try:
                    self._bounce_chat_page(rate, "假满页后离开会话页", force=True)
                except Exception:
                    pass
                if once:
                    return True
                return not self._wait_idle(rate, why="本轮跳过假满页")

            # 未读为空时仍可能要复查「刚聊过、秒回已读」的人
            unread_n = len(names)
            names = self._merge_recheck_names(names, rate)
            if not names:
                self.status["browser_busy"] = False
                if once:
                    self.log("无未读会话，本轮结束")
                    return True
                return not self._wait_idle(rate, why="无未读会话")

            self.status["last_skip_reason"] = ""
            extra = len(names) - unread_n
            if extra > 0:
                self.log(f"未读 {unread_n} 人 + 秒回复查 {extra} 人，开始处理")
            else:
                self.log(f"未读 {len(names)} 人，开始处理")
            # 有名单则留在沟通页处理，方便监控；下轮拉未读前再按需离开
            # 每轮重新读 policy，保证发送模式热更新
            policy = read_policy()
            judge = Judge()
            job = get_job(policy, None)
            hourly_max = int(rate.get("hourly_max") or 20)
            sent_this_hour = 0

            for name in names:
                if self._stop.is_set():
                    break
                if sent_this_hour >= hourly_max:
                    self.log("达到每小时上限，本轮结束")
                    break
                # 热加载：每条消息前刷新 send_mode / 阈值
                policy = read_policy()
                outcome = "skip"
                try:
                    outcome = self._handle_one(name, policy, judge, job)
                    self.status["processed"] = int(self.status.get("processed") or 0) + 1
                    if outcome == "sent":
                        self.status["sent"] = int(self.status.get("sent") or 0) + 1
                        sent_this_hour += 1 + int(getattr(self, "_same_chat_extra_sends", 0) or 0)
                        p2 = read_policy()
                        st, _ = ensure_daily_stats(p2)
                        st["today_auto_sent"] = int(st.get("today_auto_sent") or 0) + 1
                        st["draft_adopted"] = int(st.get("draft_adopted") or 0) + 1
                        st["draft_total"] = int(st.get("draft_total") or 0) + 1
                        write_policy(p2)
                    elif outcome == "handoff":
                        self.status["handoff"] = int(self.status.get("handoff") or 0) + 1
                        p2 = read_policy()
                        st, _ = ensure_daily_stats(p2)
                        st["today_handoff"] = int(st.get("today_handoff") or 0) + 1
                        write_policy(p2)
                    elif outcome == "draft":
                        self.status["drafts"] = int(self.status.get("drafts") or 0) + 1
                        p2 = read_policy()
                        st = p2.setdefault("stats", {})
                        st["draft_total"] = int(st.get("draft_total") or 0) + 1
                        write_policy(p2)
                    elif outcome in ("wechat", "action"):
                        self.status["sent"] = int(self.status.get("sent") or 0) + 1
                        sent_this_hour += 1 + int(getattr(self, "_same_chat_extra_sends", 0) or 0)
                        p2 = read_policy()
                        st, _ = ensure_daily_stats(p2)
                        st["today_auto_sent"] = int(st.get("today_auto_sent") or 0) + 1
                        write_policy(p2)
                    self._same_chat_extra_sends = 0
                except Exception as exc:
                    self.status["errors"] = int(self.status.get("errors") or 0) + 1
                    self.log(f"处理 {name} 失败：{exc}")
                    outcome = "error"

                sleep_after_person(
                    outcome,
                    policy,
                    log=self.log,
                    stop_wait=self._stop.wait,
                    on_tick=self._set_wait_countdown,
                )
                if self._stop.is_set():
                    break

            judge.close()
            self.status["browser_busy"] = False
            if once:
                return True
            return not self._stop.wait(5)
        finally:
            self.status["browser_busy"] = False

    def _idle_poll_sec(self, rate: dict[str, Any]) -> float:
        """无未读 / 假满页跳过后，多久再拉一次 list（最短～最长随机）。"""
        lo = float(rate.get("idle_poll_min_sec") or 0)
        hi = float(rate.get("idle_poll_max_sec") or 0)
        # 兼容旧配置 idle_poll_sec
        if lo <= 0 and hi <= 0:
            fixed = float(rate.get("idle_poll_sec") or 45)
            lo = hi = fixed
        elif lo <= 0:
            lo = hi
        elif hi <= 0:
            hi = lo
        if hi < lo:
            hi = lo
        lo = max(5.0, lo)
        hi = max(lo, hi)
        return float(random.uniform(lo, hi))

    def _wait_idle(self, rate: dict[str, Any], *, why: str) -> bool:
        """空闲等待。返回 True 表示被 stop 打断。"""
        sec = self._idle_poll_sec(rate)
        sleep_with_countdown(
            sec,
            prefix="空闲再看",
            label=why,
            log=self.log,
            stop_wait=self._stop.wait,
            on_tick=self._set_wait_countdown,
        )
        return self._stop.is_set()

    def _bounce_mode(self, rate: dict[str, Any]) -> str:
        """smart=少跳转（默认）；aggressive=旧版狂跳；off=关闭。"""
        raw = str(rate.get("bounce_mode") or "").strip().lower()
        if raw in {"smart", "aggressive", "off"}:
            return raw
        if rate.get("bounce_chat_page") is False:
            return "off"
        return "smart"

    def _bounce_chat_page(self, rate: dict[str, Any], reason: str, *, force: bool = False) -> bool:
        """离开会话页。smart 下若已在互动/搜索等页则跳过。"""
        mode = self._bounce_mode(rate)
        if mode == "off":
            return False
        if mode == "smart" and not force:
            if get_page_hint() == "away":
                self.log(f"{reason}：已在其它页，跳过跳转")
                return False
        prefer = "search"
        try:
            result = leave_chat_index(prefer=prefer)
            via = result.get("left_via") or "?"
            if result.get("risk"):
                self.status["risk_stop"] = True
                self.log(f"{reason}：跳转时疑似风控")
                return False
            if result.get("exit_code") == 0:
                self.log(f"{reason}（via {via}）")
                return True
            self.log(f"{reason}失败：{(result.get('stderr') or result.get('stdout') or '')[:120]}")
            return False
        except Exception as exc:
            self.log(f"{reason}异常：{exc}")
            return False

    def _annotate_unread_listing(
        self,
        listing: dict[str, Any],
        names: list[str],
        rate: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        """给 list 结果打上抬头人数 / 假满页标记。"""
        raw = listing.get("stdout") or ""
        cap = int(rate.get("unread_list_cap") or 40)
        header_n = parse_list_header_count(raw)
        bogus = is_bogus_full_page_unread(raw, names, page_cap=cap)
        out = {
            **listing,
            "list_header_count": header_n,
            "unread_page_cap": cap,
            "bogus_unread_cap": bogus,
        }
        return names, out

    def _fetch_unread_names(self, rate: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        """智能刷新：卡在会话页才先离开；假空再强制离开重试。假满页（恰好 40）原样返回并由 _tick 跳过。"""
        mode = self._bounce_mode(rate)
        if mode == "aggressive":
            self._bounce_chat_page(rate, "拉未读前离开会话页", force=True)
        elif mode == "smart":
            # 打招呼后常在推荐页 → list 自然进沟通页，不必先跳
            # 刚回完消息停在 chat/index → 必须先离开
            self._bounce_chat_page(rate, "拉未读前按需离开会话页", force=False)

        listing = run_boss(["list", "--unread"], timeout=180, confirmed=True)
        if listing.get("risk") or listing.get("exit_code") != 0:
            return [], listing

        names = parse_list_names(listing.get("stdout") or "")
        names, listing = self._annotate_unread_listing(listing, names, rate)
        if listing.get("bogus_unread_cap"):
            return names, listing
        if names or mode == "off":
            return names, listing

        self.log("未读为空，强制离开会话页后重试 list…")
        self._bounce_chat_page(rate, "空列表强制刷新", force=True)
        listing2 = run_boss(["list", "--unread"], timeout=180, confirmed=True)
        if listing2.get("risk") or listing2.get("exit_code") != 0:
            return [], listing2
        names2 = parse_list_names(listing2.get("stdout") or "")
        names2, listing2 = self._annotate_unread_listing(listing2, names2, rate)
        if names2 and not listing2.get("bogus_unread_cap"):
            self.log(f"刷新后读到未读 {len(names2)} 人")
        return names2, listing2

    def _note_recheck(self, name: str, policy: dict[str, Any] | None = None) -> None:
        """登记刚处理过的人，供下一轮在未读列表之外强制复查。"""
        rate = (policy or {}).get("rate_limit") or {}
        ttl = float(rate.get("same_chat_recheck_ttl_sec") or 180)
        if ttl <= 0:
            return
        name = (name or "").strip()
        if not name:
            return
        until = time.time() + ttl
        with self._lock:
            self._recheck = [x for x in self._recheck if x.get("name") != name and float(x.get("until") or 0) > time.time()]
            self._recheck.append({"name": name, "until": until})
            self._recheck = self._recheck[-30:]

    def _clear_recheck(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        with self._lock:
            self._recheck = [x for x in self._recheck if x.get("name") != name]

    def _merge_recheck_names(self, unread: list[str], rate: dict[str, Any]) -> list[str]:
        """刚聊过的人优先：未读里提到最前；不在未读的仍插队复查（保险）。"""
        if rate.get("same_chat_recheck", True) is False:
            return list(unread)
        now = time.time()
        with self._lock:
            alive = [x for x in self._recheck if float(x.get("until") or 0) > now]
            self._recheck = alive
            pending = [str(x.get("name") or "") for x in alive if x.get("name")]
        if not pending:
            return list(unread)
        unread_list = list(unread)
        unread_set = set(unread_list)
        # 未读 ∩ 刚聊过 → 最优先（秒回进未读后立刻再回）
        in_unread = [n for n in pending if n in unread_set]
        # 不在未读 → 强制复查（已读漏网保险）
        extra = [n for n in pending if n and n not in unread_set]
        rest = [n for n in unread_list if n not in set(in_unread)]
        return in_unread + extra + rest

    def _leave_after_send(self, rate: dict[str, Any], *, who: str) -> None:
        """发完立刻离开会话去搜索页，避免对方秒回被标已读。"""
        try:
            result = leave_chat_index(prefer="search-only")
            via = result.get("left_via") or "?"
            if result.get("risk"):
                self.status["risk_stop"] = True
                self.log(f"{who}：发完离开时疑似风控")
                return
            if result.get("exit_code") == 0:
                self.log(f"{who}：发完离开会话（via {via}），秒回将进未读")
            else:
                self.log(
                    f"{who}：发完离开失败：{(result.get('stderr') or result.get('stdout') or '')[:120]}"
                )
        except Exception as exc:
            self.log(f"{who}：发完离开异常：{exc}")

    def _bump_sent_stats(self) -> None:
        """同会话连续秒回多发时，补计日/状态（首轮仍由 _tick 计数）。"""
        self.status["sent"] = int(self.status.get("sent") or 0) + 1
        p2 = read_policy()
        st, _ = ensure_daily_stats(p2)
        st["today_auto_sent"] = int(st.get("today_auto_sent") or 0) + 1
        st["draft_adopted"] = int(st.get("draft_adopted") or 0) + 1
        st["draft_total"] = int(st.get("draft_total") or 0) + 1
        write_policy(p2)

    def _chat_needs_reply(self, messages: list[dict[str, Any]]) -> bool:
        """最后一条不是我方，且存在可处理的入站内容。"""
        if not messages:
            return False
        if messages[-1].get("role") == "recruiter":
            return False
        return _pick_last_incoming(messages) is not None

    def _dwell_for_quick_reply(self, name: str, policy: dict[str, Any]) -> bool:
        """发完后仍停在本会话，短时轮询是否有秒回。

        默认关闭：停在会话会把秒回标已读。仅 policy 显式 same_chat_watch=true 时启用。
        """
        rate = policy.get("rate_limit") or {}
        if rate.get("same_chat_watch") is not True:
            return False
        watch = float(rate.get("same_chat_watch_sec") or 12)
        poll = float(rate.get("same_chat_watch_poll_sec") or 2.5)
        if watch <= 0:
            return False
        poll = max(0.8, poll)
        deadline = time.time() + watch
        self.log(f"{name}：同会话守候秒回 {watch:.0f}s…")
        while time.time() < deadline and not self._stop.is_set():
            remain = deadline - time.time()
            if remain <= 0:
                break
            if self._stop.wait(min(poll, remain)):
                return False
            try:
                chat = run_boss(["chat", name], timeout=120, confirmed=True)
            except Exception as exc:
                self.log(f"{name}：守候刷新失败：{exc}")
                continue
            if chat.get("risk"):
                self.status["risk_stop"] = True
                raise RuntimeError(chat.get("risk_hint") or "风控")
            if chat.get("exit_code") != 0:
                continue
            messages = parse_chat_messages(chat.get("stdout") or "")
            if self._chat_needs_reply(messages):
                self.log(f"{name}：检测到秒回，继续回复")
                return True
        return False

    def _handle_one(self, name: str, policy: dict[str, Any], judge: Judge, job: dict[str, Any]) -> str:
        """处理一人。默认：发完立刻去搜索页（秒回进未读），不在会话里守候。"""
        rate = policy.get("rate_limit") or {}
        watch_on = rate.get("same_chat_watch") is True
        self._same_chat_extra_sends = 0

        if not watch_on:
            outcome = self._handle_one_inner(name, policy, judge, job)
            if outcome in ("sent", "wechat", "action"):
                self._note_recheck(name, policy)
                self._leave_after_send(rate, who=name)
            return outcome

        # 兼容旧「同会话守候」：仅显式开启时走多轮（会占会话页，易已读）
        max_rounds = int(rate.get("same_chat_watch_max_rounds") or 4)
        max_rounds = max(1, min(8, max_rounds))
        outcome = "skip"
        last_ok: str | None = None
        sent_rounds = 0
        for round_i in range(max_rounds):
            outcome = self._handle_one_inner(name, policy, judge, job)
            if outcome not in ("sent", "wechat", "action"):
                break
            last_ok = outcome
            sent_rounds += 1
            if sent_rounds > 1:
                self._bump_sent_stats()
                self._same_chat_extra_sends = sent_rounds - 1
            self._note_recheck(name, policy)
            if round_i + 1 >= max_rounds:
                break
            if not self._dwell_for_quick_reply(name, policy):
                break
            policy = read_policy()
        if last_ok in ("sent", "wechat", "action"):
            self._leave_after_send(rate, who=name)
        return last_ok or outcome

    def _handle_one_inner(self, name: str, policy: dict[str, Any], judge: Judge, job: dict[str, Any]) -> str:
        self.log(f"打开会话：{name}")
        chat = run_boss(["chat", name], timeout=180, confirmed=True)
        if chat.get("risk"):
            self.status["risk_stop"] = True
            raise RuntimeError(chat.get("risk_hint") or "风控")
        if chat.get("exit_code") != 0:
            raise RuntimeError((chat.get("stderr") or chat.get("stdout") or "")[:240])

        messages = parse_chat_messages(chat.get("stdout") or "")
        chat_meta = parse_chat_meta(chat.get("stdout") or "")
        chat_position = (chat_meta.get("communication_position") or "").strip()
        if chat_position:
            self.log(f"{name}：沟通职位={chat_position}")
        matched = resolve_position(policy, chat_position)
        if matched:
            job = get_job(policy, str(matched.get("id") or "") or None)
            self.log(
                f"{name}：匹配职位={matched.get('title')} status={matched.get('status')} "
                f"enabled={matched.get('enabled')}"
            )
        resume_got = (chat_meta.get("resume_status") or "").strip() == "已获取"
        pending_resume = _pending_resume_inbound(messages, resume_got=resume_got)
        last_cand = _pick_last_incoming(messages)
        if pending_resume:
            # 对方已发附件 PDF（非「求简历」）：即使最后一条仍是我方「请发简历」，也要走收简历确认
            last_cand = pending_resume
            self.log(f"{name}：识别到对方附件简历（待确认）")
        if not last_cand:
            self.log(f"{name}：无候选人消息，跳过")
            self._clear_recheck(name)
            return "skip"

        # 若最后一条已是我方，视为已回复（附件简历待确认除外）
        if (
            not pending_resume
            and messages
            and messages[-1].get("role") == "recruiter"
        ):
            self.log(f"{name}：最后一条已是我方回复，跳过")
            self._clear_recheck(name)
            return "skip"

        judgment = judge.evaluate_incoming(
            last_cand, policy=policy, job=job, history=messages
        )
        next_action = str(judgment.get("next_action") or "continue")
        self.log(
            f"{name}：意图={judgment.get('intent')} conf={judgment.get('intent_confidence'):.2f} "
            f"need_reply={judgment.get('need_reply')} "
            f"handoff={judgment.get('handoff_prob'):.2f} "
            f"动作={action_label(next_action)}({judgment.get('action_confidence'):.2f}) "
            f"engine={judgment.get('engine')}"
        )

        # 不必回复：无平台动作时直接跳过
        if judgment.get("need_reply") is False and next_action == "continue":
            reason = judgment.get("reason") or "不必回复"
            self.log(f"{name}：跳过 — {reason}")
            self.status["last_skip_reason"] = f"不必回复:{reason}"
            self._clear_recheck(name)
            return "skip"

        # 附件/确认卡：系统文案易被 Jev 抬红线，忽略急停，走收简历确认
        if judgment.get("handoff") and _resume_inbound_skip_handoff(
            last_cand, pending_resume=pending_resume
        ):
            self.log(
                f"{name}：附件简历入站，忽略红线/转人工急停"
                f"（redline={judgment.get('redline_prob'):.2f} handoff={judgment.get('handoff_prob'):.2f}）"
            )
            judgment = {
                **judgment,
                "handoff": False,
                "actionable": True
                if judgment.get("next_action") == "agree_resume"
                or rf.looks_like_resume_card(last_cand)
                or bool(pending_resume)
                else judgment.get("actionable"),
                "reason": (
                    (judgment.get("reason") or "")
                    + "；附件入站已忽略红线急停"
                ).strip("；"),
            }
            if pending_resume or rf.looks_like_any_resume_inbound(last_cand):
                if not judgment.get("next_action") or judgment.get("next_action") == "continue":
                    if rf.looks_like_resume_card(last_cand) and not rf.looks_like_resume_file(
                        last_cand
                    ):
                        judgment = {
                            **judgment,
                            "next_action": "agree_resume",
                            "actionable": True,
                        }
                next_action = str(judgment.get("next_action") or "continue")

        if judgment.get("handoff"):
            append_draft(
                {
                    "candidate": name,
                    "question": last_cand,
                    "draft": "",
                    "status": "handoff",
                    "reason": judgment.get("reason") or "转人工",
                    "judgment": judgment,
                }
            )
            self.log(f"{name}：转人工 — {judgment.get('reason')}")
            return "handoff"

        actions = policy.get("actions") or {}
        platform_done: str | None = None
        pending_request_resume = False
        pending_agree_followup = False
        recruiter_said = any(
            m.get("role") == "recruiter" and (m.get("text") or "").strip() for m in messages
        )

        # 附件简历：确认卡 → 点同意；已送达 PDF 文件气泡 → 无需点同意，直接确认+假装看简历
        resume_card = _should_agree_resume(messages, last_cand, judgment)
        is_file_bubble = bool(pending_resume) and (
            rf.looks_like_resume_file(pending_resume)
            or pending_resume.startswith("[对方已发送附件简历]")
        )
        if pending_resume and actions.get("agree-resume") is not False:
            if resume_card and not is_file_bubble:
                next_action = "agree_resume"
                judgment = {**judgment, "next_action": "agree_resume", "actionable": True}
            else:
                pending_agree_followup = True
                next_action = "continue"
                judgment = {**judgment, "next_action": "continue", "actionable": False}
                self.log(f"{name}：对方已发附件简历，将发确认并安排假装看简历后约面")
        elif resume_card and actions.get("agree-resume"):
            next_action = "agree_resume"
            judgment = {**judgment, "next_action": "agree_resume", "actionable": True}

        # 交换微信卡 / 对方要微信：强制 boss action wechat（主动发起或同意对方均可）
        if _should_exchange_wechat(messages, last_cand, judgment) and actions.get("wechat"):
            next_action = "wechat"
            judgment = {**judgment, "next_action": "wechat", "actionable": True}
            self.log(f"{name}：识别到换微信（卡/文案），将执行 boss action wechat")

        # 平台按钮类：白名单 + Jev actionable → 自动执行
        if judgment.get("actionable") and next_action in _PLATFORM_ACTIONS:
            mapping = _PLATFORM_ACTIONS[next_action]
            if mapping is not None:
                policy_key, cli_name = mapping
                if actions.get(policy_key):
                    # Boss 规则：双方各至少一条消息才能「求简历」
                    if next_action == "request_resume" and not recruiter_said:
                        pending_request_resume = True
                        self.log(f"{name}：会话尚无我方消息，先发一条再求简历")
                    elif next_action == "agree_resume":
                        platform_done = self._run_platform_action(
                            name, last_cand, judgment, next_action, cli_name
                        )
                        if platform_done == "handoff":
                            # 确认卡点同意失败时：若已能看到附件，仍走确认+假装看简历
                            if pending_resume or resume_got:
                                self.log(f"{name}：点同意失败，但已检测到附件，改为直接确认话术")
                                pending_agree_followup = True
                                platform_done = None
                            else:
                                return "handoff"
                        if platform_done == "agree_resume":
                            pending_agree_followup = True
                            self.log(f"{name}：已同意收简历，将发确认并安排假装看简历后约面")
                    else:
                        platform_done = self._run_platform_action(
                            name, last_cand, judgment, next_action, cli_name
                        )
                        if platform_done == "need_message":
                            pending_request_resume = True
                            platform_done = None
                            self.log(f"{name}：求简历需先互发消息，改为先发文字再求简历")
                        elif platform_done == "handoff":
                            return "handoff"
                        elif next_action == "request_resume":
                            return "action"
                else:
                    self.log(
                        f"{name}：建议「{action_label(next_action)}」但白名单未开（{policy_key}），改走文字沟通"
                    )
                    judgment = {**judgment, "actionable": False, "whitelist_blocked": policy_key}

        # 约面 / 要电话 / 换微信确认 / 收简历确认：优先固定话术
        draft = ""
        gen_reason = ""
        gen: dict = {}
        if platform_done == "wechat":
            draft = "好的，微信这边通过后咱们细聊哈～"
            gen_reason = "wechat_confirm"
            self.log(f"{name}：换微信确认话术")
        elif pending_agree_followup:
            draft = rf.ack_template(policy)
            gen_reason = "resume_ack"
            self.log(f"{name}：收简历确认话术")
        elif pending_request_resume:
            # 先发解锁求简历的话术（候选人主动打招呼/要发简历时）
            gen = generate_reply(
                last_cand,
                job_id=job.get("id"),
                intent=str(judgment.get("intent") or "other"),
                history=messages,
                chat_position=chat_position or None,
                ambiguous=bool(judgment.get("ambiguous")),
            )
            draft = (gen.get("draft") or "").strip()
            gen_reason = str(gen.get("reason") or "") or "warmup_before_request_resume"
            dlg = gen.get("dialog") or {}
            if dlg:
                self.log(
                    f"{name}：对话2.0 stage={dlg.get('stage')} goal={dlg.get('goal')} "
                    f"said={','.join(dlg.get('said') or []) or '—'}"
                )
            if not draft or (
                gen_reason.startswith("greeting")
                and not (dlg and not dlg.get("allow_full_greeting"))
            ):
                # 寒暄偏空泛且仍处开场时，改用更贴求简历的开场；已过开场则用推进句
                from src.dialog_v2 import build_dialog_state, progress_template

                st = build_dialog_state(
                    messages, last_cand, intent=str(judgment.get("intent") or "other"), policy=policy
                )
                if st.allow_full_greeting:
                    title = (job.get("title") or "").strip()
                    if not title or title == "默认岗位":
                        draft = "在的 还在招 方便的话发份附件简历过来 我先看看匹配度哈"
                    else:
                        draft = f"在的 {title}还在招 方便发份附件简历过来 我先看看匹配度哈"
                    gen_reason = "warmup_before_request_resume"
                else:
                    draft = progress_template(st, job_title=str(job.get("title") or ""), question=last_cand)
                    gen_reason = "warmup_progress_before_request_resume"
            self.log(f"{name}：先发解锁话术，再点求简历")
        elif judgment.get("actionable") and next_action in _TEXT_ACTION_TEMPLATES:
            draft = _TEXT_ACTION_TEMPLATES[next_action]
            gen_reason = f"action_template:{next_action}"
            self.log(f"{name}：使用「{action_label(next_action)}」话术模板")
        else:
            gen = generate_reply(
                last_cand,
                job_id=job.get("id"),
                intent=str(judgment.get("intent") or "other"),
                history=messages,
                chat_position=chat_position or None,
                ambiguous=bool(judgment.get("ambiguous")),
            )
            draft = (gen.get("draft") or "").strip()
            gen_reason = str(gen.get("reason") or "")
            pos_hint = gen.get("chat_position") or chat_position or ""
            dlg = gen.get("dialog") or {}
            gate = gen.get("gate") or ""
            self.log(
                f"{name}：生成回复 context={len(messages)}条"
                f"{' 职位='+pos_hint if pos_hint else ''} reason={gen_reason}"
                f"{' gate='+gate if gate else ''}"
            )
            if dlg:
                self.log(
                    f"{name}：对话2.0 stage={dlg.get('stage')} goal={dlg.get('goal')} "
                    f"said={','.join(dlg.get('said') or []) or '—'}"
                )

        if not draft:
            if platform_done == "wechat":
                return "wechat"
            self.log(f"{name}：空回复，跳过（{gen_reason or '无内容'}）")
            append_draft(
                {
                    "candidate": name,
                    "question": last_cand,
                    "draft": "",
                    "status": "skipped",
                    "reason": gen_reason or "空回复",
                    "judgment": judgment,
                    "action": platform_done,
                }
            )
            return "skip"

        # 仅硬拦截废稿（单字「无」等）；不再做 Jev 草稿质检
        from src.judge import _is_useless_draft

        if _is_useless_draft(draft):
            self.log(f"{name}：废稿已拦截，跳过：{draft[:40]}")
            append_draft(
                {
                    "candidate": name,
                    "question": last_cand,
                    "draft": draft,
                    "status": "skipped",
                    "reason": "useless_draft",
                    "judgment": judgment,
                    "action": platform_done or next_action,
                }
            )
            return "skip"

        action_tag = platform_done or (
            "request_resume"
            if pending_request_resume
            else (next_action if judgment.get("actionable") else None)
        )
        row = {
            "candidate": name,
            "question": last_cand,
            "draft": draft,
            "status": "pending",
            "reason": gen_reason
            or ("已换微信，待发确认话术" if platform_done == "wechat" else ""),
            "judgment": judgment,
            "send_mode": "auto_send",
            "action": action_tag,
            "dialog": gen.get("dialog"),
            "gate": gen.get("gate"),
        }

        self.log(f"{name}：自动发送…")
        sleep_before_send(draft, policy, log=self.log)
        sent = run_boss(["send", "--text", draft], timeout=120, confirmed=True)
        if sent.get("risk"):
            self.status["risk_stop"] = True
            row["status"] = "handoff"
            row["reason"] = sent.get("risk_hint") or "风控"
            append_draft(row)
            raise RuntimeError(row["reason"])
        if sent.get("exit_code") != 0:
            row["status"] = "handoff"
            row["reason"] = (sent.get("stderr") or "发送失败")[:200]
            append_draft(row)
            raise RuntimeError(row["reason"])
        row["status"] = "sent"
        append_draft(row)
        suffix = ""
        if platform_done == "wechat":
            suffix = "（含自动换微信）"
        elif action_tag and action_tag in _TEXT_ACTION_TEMPLATES:
            suffix = f"（{action_label(str(action_tag))}）"
        self.log(f"{name}：已发送{suffix}")

        # 发完文字后再点求简历（满足 Boss 互发门槛）
        if pending_request_resume and (actions.get("request-attachment-resume")):
            time.sleep(1.2)
            again = self._run_platform_action(
                name,
                last_cand,
                judgment,
                "request_resume",
                "request-attachment-resume",
            )
            if again == "request_resume":
                self.log(f"{name}：发送后已自动求简历")
                return "action"
            if again == "need_message":
                self.log(f"{name}：发送后求简历仍不可用，本轮仅完成文字回复")
            elif again == "handoff":
                self.log(f"{name}：发送成功，但求简历失败已转人工")
                return "handoff"

        # 同意收简历后：登记「假装看简历」到期再约面
        if pending_agree_followup:
            item = rf.schedule_after_agree(name, policy=policy, question=last_cand)
            if item:
                self.log(
                    f"{name}：假装看简历 {item.get('delay_sec')}s，"
                    f"约面将于 {str(item.get('due_at') or '')[11:19]} 发出"
                )
            return "sent"
        return "sent"

    def _process_due_followups(self) -> int:
        """处理已到期的「看完简历 → 约面」任务。"""
        policy = read_policy()
        rr = policy.get("resume_review") or {}
        if rr.get("enabled") is False:
            return 0
        due = rf.pop_due()
        if not due:
            return 0
        done = 0
        for item in due:
            if self._stop.is_set():
                break
            name = str(item.get("candidate") or "")
            if not name:
                rf.mark_done(str(item.get("id")), ok=False, note="无候选人名")
                continue
            try:
                self.log(f"{name}：假装看简历结束，发送约面…")
                chat = run_boss(["chat", name], timeout=180, confirmed=True)
                if chat.get("risk"):
                    self.status["risk_stop"] = True
                    rf.mark_done(str(item.get("id")), ok=False, note="风控")
                    raise RuntimeError(chat.get("risk_hint") or "风控")
                if chat.get("exit_code") != 0:
                    raise RuntimeError((chat.get("stderr") or chat.get("stdout") or "")[:200])
                text = rf.invite_template(policy)
                sleep_before_send(text, policy, log=self.log)
                sent = run_boss(["send", "--text", text], timeout=120, confirmed=True)
                if sent.get("risk"):
                    self.status["risk_stop"] = True
                    rf.mark_done(str(item.get("id")), ok=False, note="发送风控")
                    raise RuntimeError(sent.get("risk_hint") or "风控")
                if sent.get("exit_code") != 0:
                    raise RuntimeError((sent.get("stderr") or "约面发送失败")[:200])
                append_draft(
                    {
                        "candidate": name,
                        "question": item.get("question") or "",
                        "draft": text,
                        "status": "sent",
                        "reason": "resume_review_invite",
                        "action": "invite_interview",
                        "followup_id": item.get("id"),
                    }
                )
                rf.mark_done(str(item.get("id")), ok=True, note="已约面")
                self.status["sent"] = int(self.status.get("sent") or 0) + 1
                done += 1
                self.log(f"{name}：约面话术已发送")
                # 发完离开，避免约面后对方秒回被已读
                self._note_recheck(name, policy)
                self._leave_after_send(policy.get("rate_limit") or {}, who=name)
                sleep_after_person(
                    "invite",
                    policy,
                    log=self.log,
                    stop_wait=self._stop.wait,
                    on_tick=self._set_wait_countdown,
                )
                if self._stop.is_set():
                    break
            except Exception as exc:
                rf.mark_done(str(item.get("id")), ok=False, note=str(exc)[:200])
                self.status["errors"] = int(self.status.get("errors") or 0) + 1
                self.log(f"{name}：约面跟进失败：{exc}")
        return done

    def _run_platform_action(
        self,
        name: str,
        last_cand: str,
        judgment: dict[str, Any],
        next_action: str,
        cli_name: str,
    ) -> str:
        """执行 boss action；成功返回 next_action 名，失败返回 handoff。"""
        label = action_label(next_action)
        conf = float(judgment.get("action_confidence") or 0)
        self.log(f"{name}：Jev 建议{label}（{conf:.2f}），自动执行…")
        result = run_boss(["action", cli_name], timeout=120, confirmed=True)
        if result.get("risk"):
            self.status["risk_stop"] = True
            append_draft(
                {
                    "candidate": name,
                    "question": last_cand,
                    "draft": "",
                    "status": "handoff",
                    "reason": result.get("risk_hint") or f"{label}触发风控",
                    "judgment": judgment,
                    "action": next_action,
                }
            )
            raise RuntimeError(result.get("risk_hint") or "风控")
        if result.get("exit_code") != 0:
            err = (result.get("stderr") or result.get("stdout") or "")[:300]
            self.log(f"{name}：{label}失败：{err[:160]}")
            # Boss：未互发消息时求简历不可用 → 交给上层先发文字再重试
            if next_action == "request_resume" and _is_resume_need_message_error(err):
                return "need_message"
            append_draft(
                {
                    "candidate": name,
                    "question": last_cand,
                    "draft": "",
                    "status": "handoff",
                    "reason": f"自动{label}失败，请人工处理",
                    "judgment": judgment,
                    "action": next_action,
                    "boss_out": err,
                }
            )
            return "handoff"
        append_draft(
            {
                "candidate": name,
                "question": last_cand,
                "draft": "",
                "status": f"{next_action}_sent",
                "reason": f"系统已自动{label}",
                "judgment": judgment,
                "action": next_action,
            }
        )
        self.log(f"{name}：已自动{label}")
        return next_action


def _is_resume_need_message_error(text: str) -> bool:
    t = text or ""
    keys = ("各至少发送一条", "互发消息", "求简历」不可用", "求简历不可用", "请先与对方互发")
    return any(k in t for k in keys)


def _pick_last_incoming(messages: list[dict[str, Any]]) -> str | None:
    """取最新一条待处理入站消息：候选人正文，或系统/候选人侧的简历卡、换微信卡、附件文件。"""
    for m in reversed(messages):
        role = m.get("role")
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if role == "candidate":
            return text
        if role == "system" and (
            rf.looks_like_any_resume_inbound(text) or rf.looks_like_wechat_card(text)
        ):
            return text
    return None


def _pending_resume_inbound(
    messages: list[dict[str, Any]], *, resume_got: bool
) -> str | None:
    """若对方已发附件/确认卡且我方尚未确认话术，返回待处理文案。

    简历之后若已有我方任意回复，且候选人又发了非简历新消息（如「好」），
    则不再用旧 PDF 覆盖最新句。
    """
    last_att: str | None = None
    last_idx = -1
    for i, m in enumerate(messages):
        if m.get("role") == "recruiter":
            continue
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if rf.looks_like_any_resume_inbound(text):
            last_att = text
            last_idx = i
    if last_idx < 0 and resume_got:
        last_att = "[对方已发送附件简历]"
        last_idx = len(messages) - 1
    if last_idx < 0 or not last_att:
        return None

    after = messages[last_idx + 1 :]
    if any(
        m.get("role") == "recruiter" and rf.is_resume_ack_text(m.get("text") or "")
        for m in after
    ):
        return None

    has_recruiter_after = any(
        m.get("role") == "recruiter" and (m.get("text") or "").strip() for m in after
    )
    newer_non_resume = any(
        m.get("role") == "candidate"
        and (m.get("text") or "").strip()
        and not rf.looks_like_any_resume_inbound(m.get("text") or "")
        for m in after
    )
    # HR 已在简历后说过话，且对方又跟了普通短句 → 回最新句，勿反复拿 PDF 转人工
    if has_recruiter_after and newer_non_resume:
        return None

    return last_att


def _resume_inbound_skip_handoff(text: str, *, pending_resume: str | None = None) -> bool:
    """附件/确认卡入站不应走红线急停（系统文案易被 Jev 误判）。"""
    t = (text or "").strip()
    if pending_resume and rf.looks_like_any_resume_inbound(pending_resume):
        return True
    if pending_resume and pending_resume.startswith("[对方已发送附件简历]"):
        return True
    return rf.looks_like_any_resume_inbound(t)


def _should_agree_resume(
    messages: list[dict[str, Any]], last_cand: str, judgment: dict[str, Any]
) -> bool:
    if judgment.get("next_action") == "agree_resume" and judgment.get("actionable"):
        return True
    if rf.looks_like_resume_card(last_cand):
        return True
    for m in reversed(messages[-8:]):
        if m.get("role") == "recruiter":
            continue
        if rf.looks_like_resume_card(m.get("text") or ""):
            return True
    return False


def _should_exchange_wechat(
    messages: list[dict[str, Any]], last_cand: str, judgment: dict[str, Any]
) -> bool:
    """对齐简历卡：识别对方换微信卡/要微信文案，强制走 boss action wechat。"""
    from src.synonyms import looks_like_candidate_offers_wechat

    if looks_like_candidate_offers_wechat(last_cand):
        return False
    if judgment.get("next_action") == "wechat" and judgment.get("actionable"):
        return True
    if rf.looks_like_wechat_card(last_cand) or looks_like_wechat_request(last_cand):
        return True
    for m in reversed(messages[-8:]):
        if m.get("role") == "recruiter":
            continue
        text = m.get("text") or ""
        if looks_like_candidate_offers_wechat(text):
            continue
        if rf.looks_like_wechat_card(text) or looks_like_wechat_request(text):
            return True
    return False


def _norm_hm(value: Any) -> str:
    """把 YAML 可能解析成 int/奇怪值的时间规范成 HH:MM。"""
    if isinstance(value, int):
        return f"{value:02d}:00"
    if isinstance(value, float):
        return f"{int(value):02d}:00"
    text = str(value or "").strip().strip("'\"")
    if not text:
        return "00:00"
    if ":" not in text and text.isdigit():
        return f"{int(text):02d}:00"
    parts = text.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return f"{h:02d}:{m:02d}"
    except ValueError:
        return "00:00"


def _in_quiet_hours(start: str, end: str) -> bool:
    """支持跨零点，如 22:00–08:00。"""
    now = datetime.now().strftime("%H:%M")
    if start == end:
        return False
    if start < end:
        return start <= now < end
    # 跨零点
    return now >= start or now < end


LOOP = AutoLoop()
