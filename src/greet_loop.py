"""推荐页打招呼：按批队列 + 游标；打完未达日上限则刷新推荐继续；未读优先让出。"""

from __future__ import annotations

import re
import threading
import traceback
from datetime import date, datetime
from typing import Any

from admin.audit import audit
from admin.boss_runner import BOSS_LOCK, run_boss
from admin.config_io import ensure_daily_stats, load_env_into_os, read_policy, write_policy
from src.schedule import in_window, read_schedule, schedule_public
from src.send_timing import person_gap_range, sleep_after_person

# 推荐列表行：  - 1. 姓名｜... 或  - 1. 姓名 | 看过｜...
_ROW_RE = re.compile(
    r"^\s*-\s*(\d+)\.\s+(.+?)(?:｜|\||$)"
)
_OFF_RECOMMEND_RE = re.compile(r"不在推荐列表页|/web/chat/recommend")
_NOT_IN_LIST_RE = re.compile(r"未在推荐列表中找到")
_AGE_RE = re.compile(r"信息:(\d+)\s*岁")
_GENDER_RE = re.compile(r"性别\s*[:：]\s*([男女])")
_EXPECT_RE = re.compile(r"期望:([^｜|\n]+)")
_SALARY_RE = re.compile(r"薪资:([^｜|\n]+)")

# 本批打完后刷新推荐继续；连续空页过多则收手，避免死刷
_EMPTY_REFRESH_MAX = 8
_EMPTY_REFRESH_WAIT_SEC = 35.0
_BETWEEN_BATCH_WAIT_SEC = 6.0


def parse_recommend_list(raw: str) -> list[dict[str, Any]]:
    """从 boss recommend 文本解析候选人（含年龄/期望/优势等，供筛选）。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines = (raw or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _ROW_RE.match(line)
        if not m:
            i += 1
            continue
        idx = int(m.group(1))
        rest = m.group(2).strip()
        name = re.sub(r"\s*\|\s*看过\s*$", "", rest).strip()
        name = name.split("｜")[0].strip()
        if not name or name in seen:
            i += 1
            continue
        seen.add(name)
        can_greet = "可打招呼" in line and "已打招呼" not in line.split("可打招呼")[0]
        if "已打招呼" in line:
            can_greet = False
        elif "可打招呼" in line:
            can_greet = True

        advantage = ""
        if i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt.startswith("优势") or "优势:" in nxt[:6] or "优势：" in nxt[:6]:
                advantage = re.sub(r"^优势\s*[:：]\s*", "", nxt).strip()

        age: int | None = None
        am = _AGE_RE.search(line)
        if am:
            try:
                age = int(am.group(1))
            except ValueError:
                age = None
        expect = ""
        em = _EXPECT_RE.search(line)
        if em:
            expect = em.group(1).strip()
        salary = ""
        sm = _SALARY_RE.search(line)
        if sm:
            salary = sm.group(1).strip()

        gender: str | None = None
        gm = _GENDER_RE.search(line)
        if gm:
            gender = "female" if gm.group(1) == "女" else "male"
        else:
            gender = _infer_gender(line, advantage, name)

        rows.append(
            {
                "index": idx,
                "name": name,
                "can_greet": can_greet,
                "line": line[:280],
                "age": age,
                "expect": expect,
                "salary": salary,
                "advantage": advantage[:200],
                "gender": gender,
            }
        )
        i += 1
    return rows


def _infer_gender(line: str, advantage: str, name: str = "") -> str | None:
    """推荐主行通常无性别；从称呼/优势弱推断。无法判断返回 None。"""
    n = name or ""
    if re.search(r"(女士|小姐|女孩|女生)$", n) or "女士" in n or "小姐" in n:
        return "female"
    if re.search(r"(先生|帅哥|男生)$", n) or "先生" in n:
        return "male"
    blob = f"{line}\n{advantage}\n{n}"
    if re.search(r"性别\s*[:：]?\s*女|女性|／女\b|/女\b", blob):
        return "female"
    if re.search(r"性别\s*[:：]?\s*男|男性|／男\b|/男\b", blob):
        return "male"
    return None


def _parse_keyword_list(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    parts = re.split(r"[,，、;\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _norm_gender_filter(raw: Any) -> str:
    g = str(raw or "any").strip().lower()
    if g in ("female", "女", "f", "woman", "girl"):
        return "female"
    if g in ("male", "男", "m", "man", "boy"):
        return "male"
    return "any"


def candidate_matches_greet_filters(c: dict[str, Any], greet: dict[str, Any]) -> bool:
    """按年龄段 / 性别 / 关键词过滤推荐候选人。"""
    g = greet or {}
    age = c.get("age")
    age_min = g.get("age_min")
    age_max = g.get("age_max")
    try:
        amin = int(age_min) if age_min not in (None, "", False) else None
    except (TypeError, ValueError):
        amin = None
    try:
        amax = int(age_max) if age_max not in (None, "", False) else None
    except (TypeError, ValueError):
        amax = None
    if amin is not None or amax is not None:
        if age is None:
            return False
        if amin is not None and int(age) < amin:
            return False
        if amax is not None and int(age) > amax:
            return False

    want = _norm_gender_filter(g.get("gender"))
    if want != "any":
        got = c.get("gender")
        # 推荐页已能从头像角标读性别；筛性别时未知也排除，避免误打
        if got != want:
            return False

    blob = " ".join(
        str(x or "")
        for x in (c.get("expect"), c.get("advantage"), c.get("line"), c.get("salary"), c.get("name"))
    ).lower()
    include = _parse_keyword_list(g.get("keywords") or g.get("expect_keywords"))
    if include and not any(k.lower() in blob for k in include):
        return False
    exclude = _parse_keyword_list(g.get("keywords_exclude"))
    if exclude and any(k.lower() in blob for k in exclude):
        return False
    return True


def _filter_summary(greet: dict[str, Any]) -> str:
    g = greet or {}
    bits: list[str] = []
    amin = g.get("age_min")
    amax = g.get("age_max")
    if amin not in (None, "") or amax not in (None, ""):
        bits.append(f"年龄{amin or '?'}–{amax or '?'}")
    gender = _norm_gender_filter(g.get("gender"))
    if gender == "female":
        bits.append("女")
    elif gender == "male":
        bits.append("男")
    kws = _parse_keyword_list(g.get("keywords") or g.get("expect_keywords"))
    if kws:
        bits.append("含:" + "/".join(kws[:4]) + ("…" if len(kws) > 4 else ""))
    ex = _parse_keyword_list(g.get("keywords_exclude"))
    if ex:
        bits.append("排除:" + "/".join(ex[:3]) + ("…" if len(ex) > 3 else ""))
    return "、".join(bits)


def annotate_filter_match(candidates: list[dict[str, Any]], greet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in candidates:
        row = dict(c)
        row["filter_ok"] = candidate_matches_greet_filters(c, greet)
        out.append(row)
    return out


class GreetLoop:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.status: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "last_message": "未启动",
            "last_tick": None,
            "wait_remaining_sec": 0,
            "wait_label": "",
            "greeted": 0,
            "skipped": 0,
            "errors": 0,
            "risk_stop": False,
            "candidates": [],
            "queue": [],
            "cursor": 0,
            "queue_total": 0,
            "next_name": None,
            "yielding": False,
        }
        self._logs: list[str] = []
        self._lock = threading.Lock()

    def log(self, msg: str, *, replace_last: bool = False) -> None:
        now_ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            if replace_last and self._logs:
                prev = self._logs[-1]
                # 倒计时原地刷新：保留首次时间戳，避免每秒改钟点
                head = prev.split(" ", 1)[0] if prev else ""
                ts = head if len(head) == 8 and head[2] == ":" and head[5] == ":" else now_ts
                self._logs[-1] = f"{ts} {msg}"
            else:
                self._logs.append(f"{now_ts} {msg}")
                self._logs = self._logs[-200:]
            self.status["last_message"] = msg
            self.status["last_tick"] = datetime.now().isoformat(timespec="seconds")

    def _set_wait_countdown(self, remaining: int, label: str = "") -> None:
        """换人歇一歇倒计时（秒）；remaining=0 表示结束。"""
        with self._lock:
            if remaining and remaining > 0:
                self.status["wait_remaining_sec"] = int(remaining)
                self.status["wait_label"] = label or "换人歇一歇"
            else:
                self.status["wait_remaining_sec"] = 0
                self.status["wait_label"] = ""

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            p = read_policy()
            g = p.get("greet") or {}
            rate = p.get("rate_limit") or {}
            st, rolled = ensure_daily_stats(p)
            if rolled:
                write_policy(p)
            used = int(st.get("today_greet") or 0)
            lo, hi = _greet_interval(p)
            queue = list(self.status.get("queue") or [])
            cursor = int(self.status.get("cursor") or 0)
            next_name = None
            if 0 <= cursor < len(queue):
                next_name = queue[cursor]
            sch = schedule_public(p)
            return {
                **self.status,
                "next_name": next_name,
                "queue_total": len(queue),
                "logs": list(self._logs)[-50:],
                "enabled": g.get("enabled", True) is not False,
                "daily_max": int(g.get("daily_max") or 30),
                "today_greet": used,
                "job_keyword": g.get("job_keyword") or "",
                "only_can_greet": g.get("only_can_greet", True) is not False,
                "interval_min_sec": lo,
                "interval_max_sec": hi,
                "interval_source": "rate_limit",
                "quiet_start": rate.get("quiet_start") or "22:00",
                "quiet_end": rate.get("quiet_end") or "08:00",
                "window_start": sch["greet_start"],
                "window_end": sch["greet_end"],
                "schedule": sch,
                "unread_first": g.get("unread_first", False) is True,
                "pause_reply_during_greet": g.get("pause_reply_during_greet", False) is True,
                "exclusive_window": g.get("exclusive_window", False) is True,
                "age_min": g.get("age_min") if g.get("age_min") not in (None, "") else "",
                "age_max": g.get("age_max") if g.get("age_max") not in (None, "") else "",
                "gender": _norm_gender_filter(g.get("gender")),
                "keywords": g.get("keywords") or g.get("expect_keywords") or "",
                "keywords_exclude": g.get("keywords_exclude") or "",
                "candidates": annotate_filter_match(
                    list(self.status.get("candidates") or []), g
                ),
            }

    def fetch_recommend(self, job_keyword: str | None = None) -> dict[str, Any]:
        load_env_into_os()
        p = read_policy()
        g = p.get("greet") or {}
        kw = (job_keyword if job_keyword is not None else g.get("job_keyword") or "").strip()
        args = ["recommend"]
        if kw:
            args.append(kw)
        self.log(f"拉取推荐列表… {'岗位='+kw if kw else ''}")
        result = run_boss(args, timeout=180, confirmed=True)
        if result.get("risk"):
            self.status["risk_stop"] = True
            raise RuntimeError(result.get("risk_hint") or "风控")
        if result.get("exit_code") != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "recommend 失败")[:400])
        raw = result.get("stdout") or ""
        candidates = parse_recommend_list(raw)
        annotated = annotate_filter_match(candidates, g)
        self.status["candidates"] = annotated
        can = [c for c in annotated if c.get("can_greet")]
        matched = [c for c in can if c.get("filter_ok")]
        self.log(
            f"推荐 {len(annotated)} 人，可打招呼 {len(can)} 人，筛选后 {len(matched)} 人"
        )
        audit(
            "greet_recommend",
            {
                "total": len(annotated),
                "can_greet": len(can),
                "filter_ok": len(matched),
                "job": kw,
            },
        )
        return {
            "ok": True,
            "job_keyword": kw,
            "candidates": annotated,
            "can_greet_count": len(can),
            "filter_ok_count": len(matched),
            "raw": raw[:4000],
            "boss": {k: result.get(k) for k in ("exit_code", "elapsed_ms", "risk")},
        }

    def start(self, *, names: list[str] | None = None, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("打招呼会产生对外副作用，请二次确认")
        p = read_policy()
        if (p.get("greet") or {}).get("enabled") is False:
            raise RuntimeError("打招呼已在策略中关闭（greet.enabled=false）")
        if self._thread and self._thread.is_alive():
            return self.snapshot()
        self._stop.clear()
        self.status.update(
            {
                "running": True,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "greeted": 0,
                "skipped": 0,
                "errors": 0,
                "risk_stop": False,
                "queue": [],
                "cursor": 0,
                "queue_total": 0,
                "next_name": None,
                "yielding": False,
            }
        )
        target_names = list(names) if names else None
        self._thread = threading.Thread(
            target=self._run,
            kwargs={"target_names": target_names},
            name="boss-greet-loop",
            daemon=True,
        )
        self._thread.start()
        audit("greet_start", {"count": len(target_names) if target_names else "auto"})
        self.log("打招呼任务已启动")
        return self.snapshot()

    def stop(self, reason: str = "用户停止") -> dict[str, Any]:
        self._stop.set()
        self.status["running"] = False
        self.status["yielding"] = False
        self._set_wait_countdown(0)
        self.log(f"停止：{reason}")
        audit("greet_stop", {"reason": reason})
        return self.snapshot()

    def _run(self, target_names: list[str] | None) -> None:
        load_env_into_os()
        try:
            self._tick(target_names)
        except Exception as exc:
            self.status["errors"] = int(self.status.get("errors") or 0) + 1
            self.log(f"打招呼异常：{exc}")
            self.log(traceback.format_exc()[-400:])
        finally:
            self.status["running"] = False
            self.status["yielding"] = False
            self._set_wait_countdown(0)
            self.log("打招呼线程已退出")

    def _tick(self, target_names: list[str] | None) -> None:
        p = read_policy()
        g = dict(p.get("greet") or {})
        rate = p.get("rate_limit") or {}
        daily_max = int(g.get("daily_max") or 30)
        job_kw = (g.get("job_keyword") or "").strip()
        only_can = g.get("only_can_greet", True) is not False
        lo, hi = _greet_interval(p)
        done: set[str] = set()

        if rate.get("stop_on_captcha", True) and self.status.get("risk_stop"):
            self.log("已因风控停止，请处理后重试")
            return

        quiet_start = _norm_hm(rate.get("quiet_start") or "22:00")
        quiet_end = _norm_hm(rate.get("quiet_end") or "08:00")
        if _in_quiet_hours(quiet_start, quiet_end):
            self.log(f"静默时段（{quiet_start}–{quiet_end}），不打招呼")
            return

        sch = read_schedule(p)
        gs, ge = sch["greet_start"], sch["greet_end"]
        if gs and ge and gs != ge and not in_window(gs, ge):
            self.log(f"不在打招呼时段（{gs}–{ge}），等待进入时段…")
            while not self._stop.is_set():
                if in_window(gs, ge):
                    self.log(f"已进入打招呼时段（{gs}–{ge}）")
                    break
                if self._stop.wait(30):
                    return
            if self._stop.is_set():
                return

        used0 = _today_greet_count()
        if used0 >= daily_max:
            self.log(f"已达今日打招呼上限 {daily_max}（已用 {used0}）")
            return

        remain = daily_max - used0
        filt_tip = _filter_summary(g)
        self.log(
            f"开始打招呼 · 今日余量 {remain} · 间隔 {lo}–{hi}s"
            f" · 时段 {gs or '不限'}–{ge or '不限'}"
            f"{(' · 筛选 ' + filt_tip) if filt_tip else ''}"
            f"（本批打完且未达日上限会刷新推荐继续）"
        )

        # 首批可用勾选名单；之后刷新一律按筛选自动排
        pending_targets = list(target_names) if target_names else None
        batch_no = 0
        empty_streak = 0

        while not self._stop.is_set():
            p = read_policy()
            g = dict(p.get("greet") or {})
            rate = p.get("rate_limit") or {}
            daily_max = int(g.get("daily_max") or daily_max)
            job_kw = (g.get("job_keyword") or "").strip()
            only_can = g.get("only_can_greet", True) is not False
            lo, hi = _greet_interval(p)

            if rate.get("stop_on_captcha", True) and self.status.get("risk_stop"):
                self.log("检测到风控，停止打招呼")
                break
            if _in_quiet_hours(
                _norm_hm(rate.get("quiet_start") or "22:00"),
                _norm_hm(rate.get("quiet_end") or "08:00"),
            ):
                self.log("进入静默时段，停止打招呼")
                break

            sch = read_schedule(p)
            gs, ge = sch["greet_start"], sch["greet_end"]
            if gs and ge and gs != ge and not in_window(gs, ge):
                self.log(f"离开打招呼时段（{gs}–{ge}），暂停至下一段…")
                while not self._stop.is_set() and not in_window(gs, ge):
                    if self._stop.wait(30):
                        break
                if self._stop.is_set() or not in_window(gs, ge):
                    break
                self.log("已回到打招呼时段，刷新推荐后继续")
                continue

            used = _today_greet_count()
            if used >= daily_max:
                self.log(f"已达今日打招呼上限 {daily_max}，停止")
                break
            remain = daily_max - used

            # 第 2 批起 / 空页重试：硬刷新推荐页，避免同页死读旧名单
            if batch_no > 0 or empty_streak > 0:
                self._reload_recommend_page()

            try:
                with BOSS_LOCK:
                    candidates = self._recommend_locked(job_kw)
            except Exception as exc:
                self.status["errors"] = int(self.status.get("errors") or 0) + 1
                self.log(f"刷新推荐失败：{exc}")
                if self.status.get("risk_stop"):
                    break
                if self._stop.wait(_EMPTY_REFRESH_WAIT_SEC):
                    break
                continue

            annotated = annotate_filter_match(candidates, g)
            self.status["candidates"] = annotated
            use_targets = pending_targets
            pending_targets = None  # 仅首批吃勾选
            queue = _build_queue(
                annotated, target_names=use_targets, only_can=only_can, greet=g
            )
            queue = [n for n in queue if n not in done]
            # 余量截断，避免排很长却只打几个
            if len(queue) > remain:
                queue = queue[:remain]

            self.status["queue"] = list(queue)
            self.status["cursor"] = 0
            self.status["queue_total"] = len(queue)

            if not queue:
                empty_streak += 1
                if empty_streak >= _EMPTY_REFRESH_MAX:
                    self.log(
                        f"连续 {_EMPTY_REFRESH_MAX} 次刷新无新匹配"
                        f"（已打 {len(done)} 人，今日余量 {remain}），结束"
                    )
                    break
                self.log(
                    f"当前页无新匹配（已打 {len(done)} · 余量 {remain}），"
                    f"{int(_EMPTY_REFRESH_WAIT_SEC)}s 后刷新推荐继续"
                    f"（{empty_streak}/{_EMPTY_REFRESH_MAX}）…"
                )
                if self._stop.wait(_EMPTY_REFRESH_WAIT_SEC):
                    break
                continue

            empty_streak = 0
            batch_no += 1
            self.log(
                f"第 {batch_no} 批队列 {len(queue)} 人 · 今日余量 {remain}"
                f" · 间隔 {lo}–{hi}s"
            )
            if use_targets:
                missing = [n for n in use_targets if n not in queue and n not in done]
                filtered_out = [
                    n
                    for n in use_targets
                    if n in {c.get("name") for c in annotated}
                    and n not in queue
                    and n not in done
                    and n not in missing
                ]
                if missing:
                    self.log(f"勾选但不在当前推荐页，本批不会打到：{', '.join(missing)}")
                if filtered_out:
                    self.log(f"勾选但未通过筛选，已跳过：{', '.join(filtered_out)}")

            cursor = 0
            hit_stop = False
            while not self._stop.is_set() and cursor < len(queue):
                p = read_policy()
                rate = p.get("rate_limit") or {}
                if rate.get("stop_on_captcha", True) and self.status.get("risk_stop"):
                    self.log("检测到风控，停止打招呼")
                    hit_stop = True
                    break
                if _in_quiet_hours(
                    _norm_hm(rate.get("quiet_start") or "22:00"),
                    _norm_hm(rate.get("quiet_end") or "08:00"),
                ):
                    self.log("进入静默时段，停止打招呼")
                    hit_stop = True
                    break
                sch = read_schedule(p)
                gs, ge = sch["greet_start"], sch["greet_end"]
                if gs and ge and gs != ge and not in_window(gs, ge):
                    self.log(f"离开打招呼时段（{gs}–{ge}），暂停至下一段…")
                    while not self._stop.is_set() and not in_window(gs, ge):
                        if self._stop.wait(30):
                            break
                    if self._stop.is_set() or not in_window(gs, ge):
                        hit_stop = True
                        break
                    self.log("已回到打招呼时段，继续本批队列")
                    continue

                used = _today_greet_count()
                daily_max = int((p.get("greet") or {}).get("daily_max") or daily_max)
                if used >= daily_max:
                    self.log(f"已达今日打招呼上限 {daily_max}，停止")
                    hit_stop = True
                    break

                while cursor < len(queue) and queue[cursor] in done:
                    cursor += 1
                self.status["cursor"] = cursor
                if cursor >= len(queue):
                    break

                name = queue[cursor]
                self.status["next_name"] = name
                self.log(f"队列进度 {cursor + 1}/{len(queue)} 下一位:{name}")

                self._yield_for_unread()
                if self._stop.is_set():
                    break

                try:
                    outcome = self._greet_target(name, job_kw)
                except Exception as exc:
                    self.status["errors"] = int(self.status.get("errors") or 0) + 1
                    self.log(f"✗ {exc}")
                    if self.status.get("risk_stop"):
                        hit_stop = True
                        break
                    sleep_after_person(
                        "error",
                        read_policy(),
                        log=self.log,
                        stop_wait=self._stop.wait,
                        on_tick=self._set_wait_countdown,
                    )
                    continue

                done.add(name)
                cursor += 1
                if outcome == "skip":
                    pruned = self._prune_missing_from_cursor(queue, cursor, done)
                    if pruned:
                        cursor = int(self.status.get("cursor") or cursor)
                        self.log(f"批量跳过 {pruned} 人（刷新后已不在推荐列表）")
                self.status["cursor"] = cursor
                if outcome == "ok":
                    self.status["greeted"] = int(self.status.get("greeted") or 0) + 1
                    _bump_today_greet()
                    sleep_after_person(
                        "greet",
                        read_policy(),
                        log=self.log,
                        stop_wait=self._stop.wait,
                        on_tick=self._set_wait_countdown,
                    )
                else:
                    self.status["skipped"] = int(self.status.get("skipped") or 0) + 1
                    sleep_after_person(
                        "skip",
                        read_policy(),
                        log=self.log,
                        stop_wait=self._stop.wait,
                        on_tick=self._set_wait_countdown,
                    )

            self.status["cursor"] = cursor
            self.status["next_name"] = queue[cursor] if cursor < len(queue) else None

            if hit_stop or self._stop.is_set():
                break

            used = _today_greet_count()
            remain = daily_max - used
            if remain <= 0:
                self.log(f"已达今日打招呼上限 {daily_max}，停止")
                break

            self.log(
                f"第 {batch_no} 批已打完（本批 {len(queue)} · 累计成功 "
                f"{self.status.get('greeted') or 0}），今日余量 {remain}，"
                f"硬刷新推荐页继续…"
            )
            if self._stop.wait(_BETWEEN_BATCH_WAIT_SEC):
                break

        used_end = _today_greet_count()
        self.log(
            f"打招呼结束 · 本轮成功 {self.status.get('greeted') or 0}"
            f" · 跳过 {self.status.get('skipped') or 0}"
            f" · 今日已用 {used_end}/{daily_max}"
        )

    def _reload_recommend_page(self) -> None:
        """强制重新打开 /web/chat/recommend，让 Boss 换一批牛人。"""
        import json
        import shutil
        import subprocess
        from admin.paths import ROOT

        script = ROOT / "tools" / "boss_reload_recommend.mjs"
        if not script.is_file():
            self.log("硬刷新脚本缺失，改为直接 recommend")
            return
        node = shutil.which("node") or "node"
        self.log("硬刷新推荐页…")
        with BOSS_LOCK:
            try:
                completed = subprocess.run(
                    [node, str(script)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    check=False,
                    cwd=str(ROOT),
                )
            except Exception as exc:
                self.log(f"硬刷新失败（将继续 recommend）：{exc}")
                return
        payload: dict[str, Any] = {}
        for line in reversed((completed.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if completed.returncode == 0 and payload.get("ok"):
            self.log("推荐页已刷新")
        else:
            err = payload.get("error") or (completed.stderr or completed.stdout or "")[:160]
            self.log(f"硬刷新未成功（将继续 recommend）：{err}")

    def _yield_for_unread(self) -> None:
        """未读优先（可选）：自动回复正在处理时，打招呼在锁外等待。"""
        g = (read_policy().get("greet") or {})
        if g.get("unread_first", False) is not True:
            return
        from src.auto_loop import LOOP

        waited = False
        while not self._stop.is_set():
            if not LOOP.status.get("running"):
                break
            if not LOOP.status.get("browser_busy"):
                break
            if not waited:
                self.status["yielding"] = True
                self.log("未读优先让出：自动回复正在处理，等待…")
                waited = True
            if self._stop.wait(2.0):
                break
        if waited and not self._stop.is_set():
            self.log("未读优先：继续打招呼队列")
        self.status["yielding"] = False

    def _prune_missing_from_cursor(
        self, queue: list[str], cursor: int, done: set[str]
    ) -> int:
        """用最近一次 recommend 结果，把游标后已不在列表的人一次性标跳过。"""
        candidates = self.status.get("candidates") or []
        live = {(c.get("name") or "").strip() for c in candidates if (c.get("name") or "").strip()}
        if not live:
            return 0
        pruned = 0
        i = cursor
        while i < len(queue):
            name = queue[i]
            if name in done:
                i += 1
                continue
            if name in live:
                break
            done.add(name)
            pruned += 1
            i += 1
        if pruned:
            self.status["skipped"] = int(self.status.get("skipped") or 0) + pruned
            self.status["cursor"] = i
        return pruned

    def _recommend_locked(self, job_kw: str) -> list[dict[str, Any]]:
        """拉取并解析当前推荐列表（调用方持锁或经 run_boss 锁）。"""
        args = ["recommend"]
        if job_kw:
            args.append(job_kw)
        result = run_boss(args, timeout=180, confirmed=True)
        if result.get("risk"):
            self.status["risk_stop"] = True
            raise RuntimeError(result.get("risk_hint") or "风控/付费弹层")
        if result.get("exit_code") != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "recommend 失败")[:300])
        candidates = parse_recommend_list(result.get("stdout") or "")
        self.status["candidates"] = candidates
        return candidates

    def _greet_target(self, name: str, job_kw: str) -> str:
        """对队列中的指定人打招呼。返回 ok | skip。硬错误抛异常。"""
        with BOSS_LOCK:
            return self._greet_target_locked(name, job_kw)

    def _greet_target_locked(self, name: str, job_kw: str) -> str:
        args = ["greet", name]
        if job_kw:
            args.extend(["--job", job_kw])

        self.log(f"打招呼：{name}（队列指定）")
        result = run_boss(args, timeout=180, confirmed=True)
        if result.get("risk"):
            self.status["risk_stop"] = True
            raise RuntimeError(result.get("risk_hint") or "风控/付费弹层")
        err_text = (result.get("stderr") or "") + "\n" + (result.get("stdout") or "")

        need_refresh = result.get("exit_code") != 0 and (
            _OFF_RECOMMEND_RE.search(err_text) or _NOT_IN_LIST_RE.search(err_text)
        )
        if need_refresh:
            why = "不在推荐页" if _OFF_RECOMMEND_RE.search(err_text) else "未在列表中"
            self.log(f"{name}：{why}，刷新推荐后重试…")
            candidates = self._recommend_locked(job_kw)
            hit = next((c for c in candidates if (c.get("name") or "") == name), None)
            if not hit:
                self.log(f"跳过 {name}：刷新后已不在推荐列表")
                return "skip"
            if hit.get("can_greet") is False:
                self.log(f"跳过 {name}：刷新后显示已打招呼/不可打")
                return "skip"
            result = run_boss(args, timeout=180, confirmed=True)
            if result.get("risk"):
                self.status["risk_stop"] = True
                raise RuntimeError(result.get("risk_hint") or "风控/付费弹层")
            err_text = (result.get("stderr") or "") + "\n" + (result.get("stdout") or "")
            if result.get("exit_code") != 0:
                if _NOT_IN_LIST_RE.search(err_text) or _OFF_RECOMMEND_RE.search(err_text):
                    self.log(f"跳过 {name}：重试后仍无法打招呼")
                    return "skip"
                raise RuntimeError((result.get("stderr") or result.get("stdout") or "greet 失败")[:300])

        if result.get("exit_code") != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "greet 失败")[:300])

        out = (result.get("stdout") or "")[:200]
        self.log(f"✓ {name}：{out.replace(chr(10), ' ')[:120]}")
        audit("greet_one", {"name": name, "job": job_kw})
        return "ok"


def _build_queue(
    candidates: list[dict[str, Any]],
    *,
    target_names: list[str] | None,
    only_can: bool,
    greet: dict[str, Any] | None = None,
) -> list[str]:
    """开局生成有序待打名单；勾选时尽量保持勾选顺序；应用年龄/性别/关键词筛选。"""
    g = greet or {}
    by_name = {(c.get("name") or "").strip(): c for c in candidates if (c.get("name") or "").strip()}
    queue: list[str] = []
    seen: set[str] = set()

    def _ok(c: dict[str, Any]) -> bool:
        if only_can and not c.get("can_greet"):
            return False
        if "filter_ok" in c:
            return bool(c.get("filter_ok"))
        return candidate_matches_greet_filters(c, g)

    if target_names:
        for raw in target_names:
            name = (raw or "").strip()
            if not name or name in seen:
                continue
            c = by_name.get(name)
            if not c or not _ok(c):
                continue
            seen.add(name)
            queue.append(name)
        return queue

    for c in candidates:
        name = (c.get("name") or "").strip()
        if not name or name in seen:
            continue
        if not _ok(c):
            continue
        seen.add(name)
        queue.append(name)
    return queue


def _greet_interval(policy: dict[str, Any]) -> tuple[int, int]:
    """与自动回复同一套「换人歇一歇」区间（仅状态展示用）。"""
    lo, hi = person_gap_range(policy)
    g = policy.get("greet") or {}
    if not ((policy.get("rate_limit") or {}).get("send_interval_min_sec")):
        lo = float(g.get("interval_min_sec") or lo)
        hi = float(g.get("interval_max_sec") or hi)
        if hi < lo:
            hi = lo
    return int(lo), int(hi)


def _in_active_window(start_hm: str, end_hm: str) -> bool:
    """判断当前是否在 [start, end) 活跃窗口（支持跨午夜）。"""
    try:
        sh, sm = map(int, _norm_hm(start_hm).split(":"))
        eh, em = map(int, _norm_hm(end_hm).split(":"))
    except Exception:
        return True
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    s = sh * 60 + sm
    e = eh * 60 + em
    if s == e:
        return True
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


def _norm_hm(value: Any) -> str:
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
    now = datetime.now().strftime("%H:%M")
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _today_greet_count() -> int:
    p = read_policy()
    st, rolled = ensure_daily_stats(p)
    if rolled:
        write_policy(p)
    return int(st.get("today_greet") or 0)


def _bump_today_greet() -> None:
    p = read_policy()
    st, _ = ensure_daily_stats(p)
    st["today_greet"] = int(st.get("today_greet") or 0) + 1
    write_policy(p)


GREET = GreetLoop()
