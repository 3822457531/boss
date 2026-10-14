"""模拟聊天会话：走真实 Jev / 生成 / 动作判断，不调用 boss-cli。"""

from __future__ import annotations

import random
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from admin.config_io import load_env_into_os, read_policy
from src.judge import Judge, action_label
from src.policy import get_job
from src.reply import generate_reply

# 与 auto_loop 对齐的模板 / 平台动作映射
_TEXT_ACTION_TEMPLATES: dict[str, str] = {
    "invite_interview": "方便的话想邀您面试聊聊，您这周哪个时间段比较空？线上/线下都可以按您方便安排。",
    "ask_phone": "方便的话可以留个手机号吗？方便后续约面或同步进度～",
}

_PLATFORM_ACTIONS: dict[str, tuple[str, str] | None] = {
    "wechat": ("wechat", "wechat"),
    "request_resume": ("request-attachment-resume", "request-attachment-resume"),
    "agree_resume": ("agree-resume", "agree-resume"),
    "invite_interview": None,
    "ask_phone": None,
    "continue": None,
}

_PLATFORM_LABELS = {
    "wechat": "已向对方发起换微信",
    "request_resume": "已点击「求简历」",
    "agree_resume": "已点击「同意接收附件简历」",
}

_NAME_POOL = (
    "罗晶龄",
    "覃丹丹",
    "金雅凤",
    "向惠",
    "周敏",
    "杨扬",
    "小安",
    "陈思雨",
    "刘子涵",
    "王晓彤",
)


class SimChatStore:
    """进程内单会话（本机调试够用）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if not self._session:
                return {"session": None}
            return {"session": dict(self._session)}

    def set_options(self, *, force_reply: bool | None = None) -> dict[str, Any]:
        with self._lock:
            if not self._session:
                raise RuntimeError("请先新建/换人会话")
            if force_reply is not None:
                self._session["force_reply"] = bool(force_reply)
            return {"session": dict(self._session)}

    def reset(
        self,
        *,
        candidate: str | None = None,
        job_title: str | None = None,
        force_reply: bool = False,
    ) -> dict[str, Any]:
        load_env_into_os()
        policy = read_policy()
        job = get_job(policy, None)
        name = (candidate or "").strip() or random.choice(_NAME_POOL)
        title = (job_title or "").strip() or (job.get("title") or "前台 人事")
        now = _now()
        session = {
            "id": uuid.uuid4().hex[:12],
            "candidate": name,
            "job_title": title,
            "force_reply": bool(force_reply),
            "created_at": now,
            "messages": [
                {
                    "id": uuid.uuid4().hex[:10],
                    "role": "system",
                    "text": f"{_today_label()} 沟通的职位-{title}",
                    "ts": now,
                }
            ],
            "last_judgment": None,
            "last_outcome": None,
            "stats": {"turns": 0, "handoff": 0, "actions": 0, "replies": 0},
            "pending_reply": False,
        }
        with self._lock:
            self._session = session
            return {"session": dict(session)}

    def inject_recruiter(self, text: str) -> dict[str, Any]:
        """插入一条 HR 消息，不触发自动回复（用于模拟人工确认话术）。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("消息不能为空")
        with self._lock:
            if not self._session:
                raise RuntimeError("请先新建/换人会话")
            session = self._session
            msg = {
                "id": uuid.uuid4().hex[:10],
                "role": "recruiter",
                "text": text,
                "ts": _now(),
                "meta": {"reason": "sim_inject_hr"},
            }
            session["messages"].append(msg)
            session["messages"].append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "role": "system",
                    "text": "✍️ 已插入 HR 消息（不触发自动回复，用于复现人工确认等场景）",
                    "ts": _now(),
                }
            )
            return {"session": dict(session), "injected": msg}

    def send(self, text: str, *, force_reply: bool | None = None) -> dict[str, Any]:
        load_env_into_os()
        text = (text or "").strip()
        if not text:
            raise ValueError("消息不能为空")

        with self._lock:
            if not self._session:
                raise RuntimeError("请先新建/换人会话")
            session = self._session
            auto = bool(session.get("force_reply", False) if force_reply is None else force_reply)

        cand_msg = {
            "id": uuid.uuid4().hex[:10],
            "role": "candidate",
            "text": text,
            "ts": _now(),
        }
        with self._lock:
            session["messages"].append(cand_msg)
            session["stats"]["turns"] = int(session["stats"].get("turns") or 0) + 1
            pending_n = _pending_candidate_count(session.get("messages") or [])
            session["pending_reply"] = pending_n > 0
            if not auto:
                # 只入列，不触发 HR（模拟 Boss 连发多条）
                return {
                    "session": dict(session),
                    "turn": {
                        "candidate_message": cand_msg,
                        "outcome": "queued",
                        "reason": "pending_manual_reply",
                        "draft": "",
                        "pending_count": pending_n,
                    },
                }

        return self._run_reply(trigger_text=text)

    def reply_now(self) -> dict[str, Any]:
        """对「上次 HR 之后」积压的牛人消息统一回复（取最新一条为主，全文进上下文）。"""
        load_env_into_os()
        with self._lock:
            if not self._session:
                raise RuntimeError("请先新建/换人会话")
            session = self._session
            msgs = list(session.get("messages") or [])
        pending = _pending_candidate_texts(msgs)
        if not pending:
            raise ValueError("没有待回复的牛人消息")
        # 与真实循环一致：以最后一条为主问题，历史含全部连发
        return self._run_reply(trigger_text=pending[-1], pending_batch=pending)

    def _run_reply(
        self,
        *,
        trigger_text: str,
        pending_batch: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session
            assert session is not None
            prior = list(session.get("messages") or [])

        policy = read_policy()
        job = get_job(policy, None)
        job = {**job, "title": session.get("job_title") or job.get("title")}
        batch = pending_batch or _pending_candidate_texts(prior)
        judge = Judge()
        try:
            result = _process_turn(
                trigger_text,
                policy=policy,
                job=job,
                force_reply=True,
                judge=judge,
                prior_messages=prior,
                candidate_name=str(session.get("candidate") or "模拟会话"),
                chat_position=str(session.get("job_title") or "") or None,
            )
        finally:
            judge.close()

        with self._lock:
            if batch and len(batch) > 1:
                session["messages"].append(
                    _sys(
                        f"📬 连发 {len(batch)} 条一并处理（主句：「{trigger_text[:28]}」）"
                    )
                )
            for m in result.get("new_messages") or []:
                session["messages"].append(m)
            session["last_judgment"] = result.get("judgment")
            session["last_outcome"] = {
                "outcome": result.get("outcome"),
                "reason": result.get("reason"),
                "draft_quality": result.get("draft_quality"),
                "action": result.get("action"),
                "send_mode": result.get("send_mode"),
                "elapsed_ms": result.get("elapsed_ms"),
                "effective_question": result.get("effective_question"),
                "faq_title": result.get("faq_title"),
                "faq_confidence": result.get("faq_confidence"),
                "dialog": result.get("dialog"),
                "gate": result.get("gate"),
                "pending_batch": batch,
            }
            session["pending_reply"] = _pending_candidate_count(session.get("messages") or []) > 0
            if result.get("outcome") == "handoff":
                session["stats"]["handoff"] = int(session["stats"].get("handoff") or 0) + 1
            if result.get("action"):
                session["stats"]["actions"] = int(session["stats"].get("actions") or 0) + 1
            if result.get("outcome") in ("sent", "draft"):
                session["stats"]["replies"] = int(session["stats"].get("replies") or 0) + 1
            return {
                "session": dict(session),
                "turn": {
                    "candidate_message": {"text": trigger_text, "batch": batch},
                    **{k: result[k] for k in result if k != "new_messages"},
                },
            }

    def auto_play(
        self,
        *,
        turns: int = 5,
        persona: str = "active",
        opener: str | None = None,
        reset_first: bool = True,
        job_title: str | None = None,
        candidate: str | None = None,
        candidate_mode: str = "auto",
    ) -> dict[str, Any]:
        """AI 牛人 × 真实 HR 引擎多轮对打（带完整上下文）。

        candidate_mode:
          - auto: 优先 LLM 扮牛人，失败回落脚本
          - llm: 只用 LLM
          - script: 只用脚本人格（演示快、不依赖牛人侧 LLM）
        """
        turns = max(1, min(12, int(turns or 5)))
        persona = (persona or "active").strip() or "active"
        if persona not in PERSONAS:
            persona = "active"
        mode = (candidate_mode or "auto").strip().lower()
        if mode not in {"auto", "llm", "script"}:
            mode = "auto"
        if reset_first or not self._session:
            self.reset(candidate=candidate, job_title=job_title, force_reply=True)

        with self._lock:
            session = self._session
            assert session is not None
            session["auto_persona"] = persona
            session["auto_candidate_mode"] = mode
            job_title_s = str(session.get("job_title") or "")
            name = str(session.get("candidate") or "牛人")

        transcript: list[dict[str, Any]] = []
        first = (opener or "").strip() or PERSONAS[persona]["opener"].format(
            title=job_title_s or "这个岗位"
        )

        for i in range(turns):
            if i == 0:
                cand_text = first
                cand_src = "opener"
            else:
                with self._lock:
                    hist = list((self._session or {}).get("messages") or [])
                cand_text, cand_src = generate_candidate_line(
                    hist,
                    persona=persona,
                    job_title=job_title_s,
                    candidate_name=name,
                    mode=mode,
                )
            if not cand_text:
                break
            turn_data = self.send(cand_text)
            with self._lock:
                sess = self._session or {}
                msgs = list(sess.get("messages") or [])
                # 本轮新增的 HR 气泡（避免 skip 时沿用上一轮 HR）
                last_hr = ""
                for m in reversed(msgs):
                    if m.get("role") == "system":
                        continue
                    if m.get("role") == "recruiter" and (m.get("text") or "").strip():
                        # 仅当在本轮候选人消息之后
                        break_on_cand = False
                        # 找到本轮 cand 后再取其后的 recruiter —— 简化：看 turn 结果
                        last_hr = str(m.get("text") or "")
                        break
                    if m.get("role") == "candidate":
                        break
                outcome = (turn_data.get("turn") or {}).get("outcome")
                dialog = (sess.get("last_outcome") or {}).get("dialog")
                gate = (sess.get("last_outcome") or {}).get("gate")
                draft = (turn_data.get("turn") or {}).get("draft") or ""
                if outcome not in ("sent", "draft", "wechat") and not draft:
                    last_hr = ""
                elif outcome in ("sent", "draft", "wechat") and draft:
                    last_hr = str(draft)
            transcript.append(
                {
                    "n": i + 1,
                    "candidate": cand_text,
                    "candidate_source": cand_src,
                    "hr": last_hr,
                    "outcome": outcome,
                    "dialog": dialog,
                    "gate": gate,
                }
            )
            if outcome == "handoff":
                break
            time.sleep(0.05)

        with self._lock:
            session = self._session
            assert session is not None
            session["messages"].append(
                _sys(
                    f"🤖 多轮模拟结束：人格={PERSONAS[persona]['label']} · "
                    f"牛人={'脚本' if mode == 'script' else ('LLM' if mode == 'llm' else '自动')} · "
                    f"完成 {len(transcript)}/{turns} 轮 · 可继续手动发消息"
                )
            )
            session["last_auto"] = {
                "persona": persona,
                "candidate_mode": mode,
                "turns_requested": turns,
                "turns_done": len(transcript),
                "transcript": transcript,
            }
            return {"session": dict(session), "transcript": transcript, "persona": persona}


# ---------- AI 牛人（多轮模拟） ----------

PERSONAS: dict[str, dict[str, str]] = {
    "active": {
        "label": "积极求职",
        "opener": "你好，{title}还在招吗？",
        "style": "积极配合、愿意自我介绍、会问一两个关键条件，语气自然短句。",
    },
    "reask_hiring": {
        "label": "反复确认还招",
        "opener": "在吗，还招人吗",
        "style": "爱反复确认是否还在招；中间夹杂「好的」「嗯」；偶尔再问一遍还招吗。专门用来测复读。",
    },
    "salary": {
        "label": "紧盯薪资待遇",
        "opener": "您好，想问下薪资和上班时间怎么算",
        "style": "优先问薪资/五险/休息；回答简短；被追问经历时只给一两句。",
    },
    "brief": {
        "label": "惜字如金",
        "opener": "在吗",
        "style": "每条尽量 ≤8 字：好的/在的/嗯/还招吗/薪资呢；极少主动展开。",
    },
    "offtopic": {
        "label": "爱跑题",
        "opener": "你好呀还招吗",
        "style": "偶尔聊天气/周末/吐槽找工作，再被拉回招聘话题；测分支外承接。",
    },
}


def generate_candidate_line(
    history: list[dict[str, Any]],
    *,
    persona: str,
    job_title: str,
    candidate_name: str,
    mode: str = "auto",
) -> tuple[str, str]:
    """生成下一条牛人消息。返回 (text, source)。"""
    persona = persona if persona in PERSONAS else "active"
    scripted = _scripted_candidate_line(history, persona=persona, job_title=job_title)
    if mode == "script":
        return scripted, "script"

    try:
        from src.reply import _llm_chat

        hist_lines = []
        for m in history[-16:]:
            role = m.get("role")
            t = (m.get("text") or "").strip()
            if not t or role == "system":
                continue
            who = "牛人(我)" if role == "candidate" else "HR" if role == "recruiter" else role
            hist_lines.append(f"{who}：{t}")
        hist_block = "\n".join(hist_lines) or "（尚无对话）"
        style = PERSONAS[persona]["style"]
        system = (
            "你在 Boss 直聘上扮演求职牛人，与 HR 文字聊天。"
            "只输出下一条要发送的短消息正文（8～40 字为主），不要解释、不要引号、不要角色前缀。"
            "必须承接 HR 上一句；不要突然跳过话题；不要替 HR 说话。"
            "不要连续两轮说意思几乎相同的话。"
        )
        user = (
            f"你的昵称：{candidate_name}\n"
            f"沟通职位：{job_title or '未知'}\n"
            f"扮演人格：{PERSONAS[persona]['label']} — {style}\n\n"
            f"对话记录：\n{hist_block}\n\n"
            "请写出牛人下一条回复："
        )
        text = _llm_chat(
            user,
            purpose="sim_candidate_next",
            system=system,
            temperature=0.85,
        ).strip()
        text = _clean_candidate_text(text)
        if text:
            return text, "llm"
    except Exception:
        if mode == "llm":
            raise
    return scripted, "script"


def _clean_candidate_text(text: str) -> str:
    t = (text or "").strip().strip("「」『』\"'")
    for prefix in ("牛人：", "牛人:", "我：", "我:", "候选人：", "候选人:"):
        if t.startswith(prefix):
            t = t[len(prefix) :].strip()
    # 只取第一行，避免模型输出多段
    t = t.splitlines()[0].strip() if t else ""
    return t[:80]


def _scripted_candidate_line(
    history: list[dict[str, Any]],
    *,
    persona: str,
    job_title: str,
) -> str:
    """无 LLM 时的规则牛人：按轮次与 HR 末句推进，保证可演示多轮。"""
    cand_n = sum(1 for m in history if m.get("role") == "candidate")
    last_hr = ""
    for m in reversed(history):
        if m.get("role") == "recruiter" and (m.get("text") or "").strip():
            last_hr = str(m.get("text") or "")
            break
    hr_l = last_hr.lower()

    def pick(options: list[str]) -> str:
        return options[min(cand_n, len(options) - 1)]

    if persona == "reask_hiring":
        return pick(["好的", "还招吗", "嗯嗯", "那还在招是吧", "好"])
    if persona == "salary":
        if any(k in last_hr for k in ("薪", "待遇", "休", "班")):
            return pick(["还有五险吗", "双休吗", "了解了，方便说下要求吗", "好的"])
        return pick(["薪资大概多少", "上班时间呢", "有五险一金吗", "好的"])
    if persona == "brief":
        return pick(["好的", "嗯", "薪资呢", "还招吗", "好"])
    if persona == "offtopic":
        return pick(
            [
                "哈哈找工作好累",
                "你们周末双休吗",
                "今天好热呀…对了岗位还招吗",
                "好吧那我介绍下，做过前台",
            ]
        )
    # active
    if any(k in last_hr for k in ("介绍", "近况", "经历", "在职")):
        return pick(
            [
                "我目前在职，做过一年前台",
                "离职看机会，有相关经验",
                "方便的话我发份简历给你",
            ]
        )
    if "简历" in last_hr:
        return "可以，我发附件简历过来"
    if any(k in last_hr for k in ("薪", "时间", "地点")):
        return pick(["好的了解了", "那我可以来面试吗", "还有别的要求吗"])
    if cand_n <= 1:
        return "好的"
    return pick(["方便了解下薪资吗", "我可以发简历吗", "这周能面试吗", "好的"])


def _process_turn(
    question: str,
    *,
    policy: dict[str, Any],
    job: dict[str, Any],
    force_reply: bool,
    judge: Judge,
    prior_messages: list[dict[str, Any]] | None = None,
    candidate_name: str = "模拟会话",
    chat_position: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    new_messages: list[dict[str, Any]] = []
    from src import resume_followup as rf
    from src.auto_loop import _pending_resume_inbound

    # 与真实循环对齐：若历史里仍有「未确认」的附件简历，用简历文案覆盖最新短句（如「好」）
    msgs = list(prior_messages or [])
    resume_got = any(
        rf.looks_like_resume_file(str(m.get("text") or ""))
        or str(m.get("text") or "").startswith("[对方已发送附件简历]")
        for m in msgs
    )
    pending_resume = _pending_resume_inbound(msgs, resume_got=resume_got)
    effective = (pending_resume or question or "").strip()
    if pending_resume and pending_resume.strip() != (question or "").strip():
        new_messages.append(
            _sys(
                f"📎 待确认简历覆盖最新句「{(question or '')[:24]}」"
                f"→ 按「{pending_resume[:36]}」判断（与真实 auto_loop 一致）"
            )
        )

    judgment = judge.evaluate_incoming(
        effective, policy=policy, job=job, history=msgs
    )
    next_action = str(judgment.get("next_action") or "continue")
    actions = policy.get("actions") or {}
    action_done: str | None = None
    recruiter_said = any(
        m.get("role") == "recruiter" and (m.get("text") or "").strip()
        for m in msgs
    )
    # 沟通职位：显式传入 > 历史系统句 > 会话 job_title
    pos = (chat_position or "").strip()
    if not pos:
        from src.reply import _infer_position_from_history

        pos = _infer_position_from_history(prior_messages, "")
    if not pos:
        pos = str(job.get("title") or "").strip()
        if pos in {"默认岗位", "该岗位", "本岗位"}:
            pos = ""

    # 不必回复
    if judgment.get("need_reply") is False and next_action == "continue":
        note = judgment.get("reason") or "不必回复"
        new_messages.append(_sys(f"⏭ 不必回复：{note}"))
        return {
            "outcome": "skip",
            "reason": note,
            "judgment": judgment,
            "action": None,
            "draft": "",
            "draft_quality": None,
            "send_mode": policy.get("send_mode"),
            "new_messages": new_messages,
            "effective_question": effective,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # 与真实循环一致：附件/确认卡忽略红线急停
    from src.auto_loop import _resume_inbound_skip_handoff

    if judgment.get("handoff") and _resume_inbound_skip_handoff(
        effective, pending_resume=pending_resume
    ):
        new_messages.append(
            _sys(
                f"📎 附件简历入站，已忽略红线/转人工急停"
                f"（redline={judgment.get('redline_prob'):.2f}）"
            )
        )
        judgment = {
            **judgment,
            "handoff": False,
            "actionable": True,
            "reason": (
                (judgment.get("reason") or "") + "；附件入站已忽略红线急停"
            ).strip("；"),
        }
        if rf.looks_like_resume_card(effective) and not rf.looks_like_resume_file(effective):
            judgment = {**judgment, "next_action": "agree_resume", "actionable": True}
            next_action = "agree_resume"

    if judgment.get("handoff"):
        note = judgment.get("reason") or "转人工"
        new_messages.append(_sys(f"⚠️ 系统判定转人工：{note}"))
        return {
            "outcome": "handoff",
            "reason": note,
            "judgment": judgment,
            "action": None,
            "draft": "",
            "draft_quality": None,
            "send_mode": policy.get("send_mode"),
            "new_messages": new_messages,
            "effective_question": effective,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # 同意收简历：同意 → 确认话术 → 登记假装看简历后约面
    if (
        (judgment.get("actionable") and next_action == "agree_resume" and actions.get("agree-resume"))
        or rf.looks_like_resume_card(effective)
        or rf.looks_like_resume_file(effective)
    ):
        if rf.looks_like_resume_card(effective) and not rf.looks_like_resume_file(effective):
            new_messages.append(
                _sys(f"🔘 模拟执行：{_PLATFORM_LABELS.get('agree_resume', '同意收简历')}")
            )
        else:
            new_messages.append(_sys("📎 识别到对方已发送附件简历（无需点同意）"))
        ack = rf.ack_template(policy)
        new_messages.append(
            {
                "id": uuid.uuid4().hex[:10],
                "role": "recruiter",
                "text": ack,
                "ts": _now(),
                "meta": {"reason": "resume_ack"},
            }
        )
        rr = dict(policy.get("resume_review") or {})
        sim_policy = {
            **policy,
            "resume_review": {
                **rr,
                "enabled": True,
                "read_min_sec": min(15, int(rr.get("read_min_sec") or 180)),
                "read_max_sec": min(30, int(rr.get("read_max_sec") or 360)),
            },
        }
        item = rf.schedule_after_agree(
            candidate_name or "模拟会话",
            policy=sim_policy,
            question=effective,
        )
        delay = (item or {}).get("delay_sec") or 20
        new_messages.append(
            _sys(f"⏳ 假装看简历约 {delay}s，到期后由自动循环发约面（真实默认 3～6 分钟）")
        )
        return {
            "outcome": "sent",
            "reason": "agree_resume+schedule_invite",
            "judgment": {**judgment, "next_action": "agree_resume", "actionable": True},
            "action": "agree_resume",
            "draft": ack,
            "send_mode": "auto_send",
            "new_messages": new_messages,
            "effective_question": effective,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # 交换微信卡 / 明确要求换微信：执行 boss action wechat + 确认话术
    # 「有意可加微信看作品」是对方留联系方式，不走平台换微信
    from src.synonyms import looks_like_candidate_offers_wechat

    if looks_like_candidate_offers_wechat(effective):
        if next_action == "wechat":
            next_action = "continue"
            judgment = {**judgment, "next_action": "continue", "actionable": False}
    elif (
        (judgment.get("actionable") and next_action == "wechat" and actions.get("wechat"))
        or rf.looks_like_wechat_card(effective)
    ) and actions.get("wechat") is not False:
        new_messages.append(
            _sys(f"🔘 模拟执行：{_PLATFORM_LABELS.get('wechat', '换微信')}（boss action wechat）")
        )
        draft = "好的，微信这边通过后咱们细聊哈～"
        new_messages.append(
            {
                "id": uuid.uuid4().hex[:10],
                "role": "recruiter",
                "text": draft,
                "ts": _now(),
                "meta": {"reason": "wechat_confirm"},
            }
        )
        return {
            "outcome": "wechat",
            "reason": "wechat_card_or_action",
            "judgment": {**judgment, "next_action": "wechat", "actionable": True},
            "action": "wechat",
            "draft": draft,
            "send_mode": "auto_send",
            "new_messages": new_messages,
            "effective_question": effective,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # 求简历且尚无我方消息：先发文字再模拟求简历（对齐 Boss 互发门槛）
    if (
        judgment.get("actionable")
        and next_action == "request_resume"
        and actions.get("request-attachment-resume")
        and not recruiter_said
    ):
        gen = generate_reply(
            effective,
            job_id=job.get("id"),
            intent=str(judgment.get("intent") or "other"),
            history=list(prior_messages or []) + [{"role": "candidate", "text": effective}],
            chat_position=pos or None,
            ambiguous=bool(judgment.get("ambiguous")),
        )
        warmup = (gen.get("draft") or "").strip()
        dlg = gen.get("dialog") or {}
        if not warmup or (
            str(gen.get("reason") or "").startswith("greeting")
            and dlg.get("allow_full_greeting", True)
        ):
            from src.dialog_v2 import build_dialog_state, progress_template

            st = build_dialog_state(
                list(prior_messages or []) + [{"role": "candidate", "text": effective}],
                effective,
                intent=str(judgment.get("intent") or "other"),
                policy=policy,
            )
            if st.allow_full_greeting:
                warmup = "在的 还在招 方便的话发份附件简历过来 我先看看匹配度哈"
            else:
                warmup = progress_template(st, job_title=str(job.get("title") or ""), question=effective)
        new_messages.append(
            {
                "id": uuid.uuid4().hex[:10],
                "role": "recruiter",
                "text": warmup,
                "ts": _now(),
                "meta": {"reason": "warmup_before_request_resume", "dialog": dlg or gen.get("dialog")},
            }
        )
        new_messages.append(
            _sys(f"🔘 模拟执行：{_PLATFORM_LABELS.get('request_resume', '求简历')}（已先发文字解锁）")
        )
        return {
            "outcome": "action",
            "reason": "warmup+request_resume",
            "judgment": judgment,
            "action": "request_resume",
            "draft": warmup,
            "draft_quality": 0.95,
            "send_mode": policy.get("send_mode"),
            "new_messages": new_messages,
            "effective_question": effective,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # 平台按钮（模拟，不调 CLI）
    if judgment.get("actionable") and next_action in _PLATFORM_ACTIONS:
        mapping = _PLATFORM_ACTIONS[next_action]
        if mapping is not None:
            policy_key, _cli = mapping
            if actions.get(policy_key):
                action_done = next_action
                new_messages.append(
                    _sys(f"🔘 模拟执行：{_PLATFORM_LABELS.get(next_action, action_label(next_action))}")
                )
                if next_action == "request_resume":
                    return {
                        "outcome": "action",
                        "reason": f"已模拟{action_label(next_action)}",
                        "judgment": judgment,
                        "action": action_done,
                        "draft": "",
                        "send_mode": "auto_send",
                        "new_messages": new_messages,
                        "effective_question": effective,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    }
            else:
                judgment = {
                    **judgment,
                    "actionable": False,
                    "whitelist_blocked": policy_key,
                }
                new_messages.append(
                    _sys(f"白名单未开「{policy_key}」，跳过平台动作，改走文字")
                )

    draft = ""
    gen_reason = ""
    gen: dict = {}
    if action_done == "wechat":
        # 换微信后用短确认，不要再走寒暄模板
        draft = "好的，微信这边通过后咱们细聊哈～"
        gen_reason = "wechat_confirm"
    elif judgment.get("actionable") and next_action in _TEXT_ACTION_TEMPLATES:
        draft = _TEXT_ACTION_TEMPLATES[next_action]
        gen_reason = f"action_template:{next_action}"
    else:
        gen = generate_reply(
            effective,
            job_id=job.get("id"),
            intent=str(judgment.get("intent") or "other"),
            history=list(prior_messages or []) + [{"role": "candidate", "text": effective}],
            chat_position=pos or None,
        )
        draft = (gen.get("draft") or "").strip()
        gen_reason = str(gen.get("reason") or "")

    if not draft:
        if action_done == "wechat":
            return {
                "outcome": "action",
                "reason": "已模拟换微信（无补充话术）",
                "judgment": judgment,
                "action": action_done,
                "draft": "",
                "send_mode": "auto_send",
                "new_messages": new_messages,
                "effective_question": effective,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        new_messages.append(_sys(f"⚠️ 空回复，已跳过：{gen_reason or '无内容'}"))
        return {
            "outcome": "skip",
            "reason": gen_reason or "空回复",
            "judgment": judgment,
            "action": action_done,
            "draft": "",
            "send_mode": "auto_send",
            "new_messages": new_messages,
            "effective_question": effective,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    from src.judge import _is_useless_draft

    if _is_useless_draft(draft):
        new_messages.append(_sys(f"⚠️ 废稿已拦截：{draft[:40]}"))
        return {
            "outcome": "skip",
            "reason": "useless_draft",
            "judgment": judgment,
            "action": action_done or next_action,
            "draft": draft,
            "send_mode": "auto_send",
            "new_messages": new_messages,
            "effective_question": effective,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # 全自动：直接作为 HR 回复发出（模拟）
    new_messages.append(
        {
            "id": uuid.uuid4().hex[:10],
            "role": "recruiter",
            "text": draft,
            "ts": _now(),
            "meta": {
                "reason": gen_reason,
                "action": action_done,
                "dialog": gen.get("dialog"),
                "gate": gen.get("gate"),
            },
        }
    )
    return {
        "outcome": "sent",
        "reason": gen_reason,
        "judgment": judgment,
        "action": action_done or (next_action if judgment.get("actionable") else None),
        "draft": draft,
        "send_mode": "auto_send",
        "new_messages": new_messages,
        "effective_question": effective,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "faq_title": gen.get("faq_title"),
        "faq_confidence": gen.get("faq_confidence"),
        "dialog": gen.get("dialog"),
        "gate": gen.get("gate"),
    }


def _pending_candidate_texts(messages: list[dict[str, Any]] | None) -> list[str]:
    """上次 HR 回复之后积压的牛人正文（按时间顺序）。"""
    pending: list[str] = []
    for m in messages or []:
        role = m.get("role")
        if role == "recruiter":
            pending = []
        elif role == "candidate":
            t = (m.get("text") or "").strip()
            if t:
                pending.append(t)
    return pending


def _pending_candidate_count(messages: list[dict[str, Any]] | None) -> int:
    return len(_pending_candidate_texts(messages))


def _sys(text: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10],
        "role": "system",
        "text": text,
        "ts": _now(),
    }


def _draft_bubble(draft: str, *, pending: bool) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10],
        "role": "draft",
        "text": draft,
        "ts": _now(),
        "meta": {"pending": pending},
    }


def _now() -> str:
    return datetime.now().strftime("%H:%M")


def _today_label() -> str:
    return datetime.now().strftime("%m月%d日").lstrip("0").replace("月0", "月")


STORE = SimChatStore()
