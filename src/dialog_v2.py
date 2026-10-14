"""直聊小蜜 2.0 对话引擎：会话阶段 · 已说清单 · 本轮目标 · 近句去重门禁。

设计原则：
- Jev/意图只做辅诊；总调度是「阶段 + 已说 + 本轮目标」。
- 同一事实（如还在招）只主动完整说一次；之后只允许短确认或推进。
- 近句高度相似则强制换成推进句，禁止同人复读。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# ---------- 阶段 / 目标 ----------

STAGES = ("open", "qualify", "inform", "advance", "stall", "close")

GOALS = {
    "confirm_hiring": "简短确认还在招，并邀请对方用一两句介绍近况",
    "ask_background": "承接对方最新一句，追问近况/经历/是否在职（不要再说还在招开场白）",
    "answer_and_ask": "只回答对方刚问到的点，再补一个轻量追问推进沟通",
    "ask_one_thing": "承接一句后只问一个具体问题，把对话往前推",
    "short_hiring_ack": "对方再次确认是否在招：半句点头即可，立刻追问下一步",
    "advance_resume": "礼貌推进要简历或确认附件",
    "polite_close": "对方已表示不感兴趣：礼貌收尾致谢，不要再推销岗位或追问经历",
    "handoff": "礼貌说明稍后人工跟进，不展开长聊",
}

_HIRING_RE = re.compile(
    r"(还在招|还招|在招|招人中|岗位还在|职位还在)",
    re.I,
)
_SALARY_RE = re.compile(r"(薪|待遇|工资|k\b|K\b|\d+\s*-\s*\d+|元/)", re.I)
_LOC_RE = re.compile(r"(地点|地址|在哪|哪儿|通勤)", re.I)
_INTRO_ASK_RE = re.compile(r"(介绍|近况|经历|在职|目前做|方便说)", re.I)
_RESUME_ASK_RE = re.compile(r"(简历|附件)", re.I)
_WECHAT_RE = re.compile(r"(微信|vx|v信)", re.I)
_INTERVIEW_RE = re.compile(r"(面试|约面|到面)", re.I)
_CAND_HIRING_ASK_RE = re.compile(r"(还招|还在招|招人吗|招人么|在招吗)", re.I)


@dataclass
class SaidFacts:
    told_hiring: bool = False
    told_salary: bool = False
    told_location: bool = False
    asked_intro: bool = False
    asked_resume: bool = False
    asked_wechat: bool = False
    asked_interview: bool = False

    def as_list(self) -> list[str]:
        labels = []
        if self.told_hiring:
            labels.append("已告知还在招")
        if self.told_salary:
            labels.append("已谈及薪资口径")
        if self.told_location:
            labels.append("已谈及地点")
        if self.asked_intro:
            labels.append("已请对方介绍近况")
        if self.asked_resume:
            labels.append("已提及要简历")
        if self.asked_wechat:
            labels.append("已提及微信")
        if self.asked_interview:
            labels.append("已提及约面")
        return labels


@dataclass
class DialogState:
    stage: str = "open"
    goal: str = "confirm_hiring"
    goal_hint: str = ""
    said: SaidFacts = field(default_factory=SaidFacts)
    allow_full_greeting: bool = True
    candidate_reask_hiring: bool = False
    recent_recruiter: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_public(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "goal": self.goal,
            "goal_hint": self.goal_hint,
            "said": self.said.as_list(),
            "allow_full_greeting": self.allow_full_greeting,
            "candidate_reask_hiring": self.candidate_reask_hiring,
        }


def dialog_v2_enabled(policy: dict[str, Any] | None) -> bool:
    cfg = (policy or {}).get("dialog_v2") or {}
    if cfg.get("enabled") is False:
        return False
    return True


def _norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[，。！？、,.!?;；:：~～…·\-—_]", "", t)
    return t


def text_similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(shorter) >= 6 and shorter in longer:
            return max(0.82, len(shorter) / max(len(longer), 1))
    return SequenceMatcher(None, na, nb).ratio()


def _recruiter_texts(history: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for m in history or []:
        if (m.get("role") or "") != "recruiter":
            continue
        t = (m.get("text") or "").strip()
        if t:
            out.append(t)
    return out


def extract_said_facts(history: list[dict[str, Any]] | None) -> SaidFacts:
    said = SaidFacts()
    for t in _recruiter_texts(history):
        if _HIRING_RE.search(t):
            said.told_hiring = True
        if _SALARY_RE.search(t):
            said.told_salary = True
        if _LOC_RE.search(t):
            said.told_location = True
        if _INTRO_ASK_RE.search(t):
            said.asked_intro = True
        if _RESUME_ASK_RE.search(t):
            said.asked_resume = True
        if _WECHAT_RE.search(t):
            said.asked_wechat = True
        if _INTERVIEW_RE.search(t):
            said.asked_interview = True
    return said


def infer_stage(
    history: list[dict[str, Any]] | None,
    *,
    said: SaidFacts | None = None,
    intent: str = "other",
) -> str:
    said = said or extract_said_facts(history)
    msgs = [m for m in (history or []) if (m.get("text") or "").strip()]
    rec_n = sum(1 for m in msgs if m.get("role") == "recruiter")
    cand_n = sum(1 for m in msgs if m.get("role") == "candidate")

    if intent == "not_interested":
        return "close"
    if said.asked_interview or said.asked_wechat or (said.asked_resume and rec_n >= 2):
        return "advance"
    if intent in {"salary", "schedule", "location", "offer"} or said.told_salary or said.told_location:
        if rec_n >= 1:
            return "inform"
    if said.told_hiring or said.asked_intro or rec_n >= 1:
        if cand_n >= 2 or (cand_n >= 1 and rec_n >= 1):
            return "qualify"
        return "qualify" if said.told_hiring else "open"
    if rec_n == 0:
        return "open"
    return "qualify"


def pick_goal(
    *,
    stage: str,
    said: SaidFacts,
    intent: str,
    question: str,
    candidate_reask_hiring: bool,
) -> str:
    q = (question or "").strip()
    if intent == "not_interested" or stage == "close":
        return "polite_close"
    if intent in {"salary", "schedule", "location", "offer", "interview", "resume"}:
        if intent == "resume":
            return "advance_resume"
        return "answer_and_ask"
    if candidate_reask_hiring and said.told_hiring:
        return "short_hiring_ack"
    if stage == "open" and not said.told_hiring:
        return "confirm_hiring"
    if stage in {"qualify", "stall"} or intent in {"greeting", "other"}:
        if not said.asked_intro:
            return "ask_background"
        if not said.asked_resume and len(q) <= 12:
            return "ask_one_thing"
        return "ask_one_thing"
    if stage == "inform":
        return "answer_and_ask"
    if stage == "advance":
        return "advance_resume" if not said.asked_resume else "ask_one_thing"
    return "ask_one_thing"


def build_dialog_state(
    history: list[dict[str, Any]] | None,
    question: str,
    *,
    intent: str = "other",
    policy: dict[str, Any] | None = None,
) -> DialogState:
    enabled = dialog_v2_enabled(policy)
    said = extract_said_facts(history)
    stage = infer_stage(history, said=said, intent=intent)
    cand_reask = bool(_CAND_HIRING_ASK_RE.search(question or ""))
    allow_full = (not said.told_hiring) and stage == "open"
    goal = pick_goal(
        stage=stage,
        said=said,
        intent=intent,
        question=question,
        candidate_reask_hiring=cand_reask,
    )
    # 未启用时仍返回状态，但不强制门禁
    st = DialogState(
        stage=stage,
        goal=goal,
        goal_hint=GOALS.get(goal, GOALS["ask_one_thing"]),
        said=said,
        allow_full_greeting=allow_full,
        candidate_reask_hiring=cand_reask,
        recent_recruiter=_recruiter_texts(history)[-5:],
        enabled=enabled,
    )
    return st


def memory_prompt_block(dlg: DialogState) -> str:
    if not dlg.enabled:
        return ""
    said = "、".join(dlg.said.as_list()) or "尚无"
    return (
        f"【2.0会话记忆】当前阶段={dlg.stage}；本轮目标={dlg.goal}（{dlg.goal_hint}）；"
        f"已说清单：{said}。"
        "硬性要求：必须承接候选人最新一句；完成「本轮目标」；每条最多一个事实+一个问题；"
        "禁止把已说清单里的内容再当开场白完整复述；禁止与自己近期回复意思几乎相同。\n"
    )


def progress_template(
    dlg: DialogState,
    *,
    job_title: str = "",
    question: str = "",
) -> str:
    """无 LLM / 门禁拦截时的推进短句。"""
    title = (job_title or "").strip()
    if title in {"默认岗位", "该岗位", "本岗位"}:
        title = ""
    if dlg.goal == "polite_close":
        return "好的 感谢关注 祝顺利哈"
    if dlg.goal == "confirm_hiring":
        if title:
            return f"在的 {title}还在招 您方便简单介绍下近况吗"
        return "在的 还在招 您方便简单介绍下近况吗"
    if dlg.goal == "short_hiring_ack":
        return "在的哈 您方便说下目前在职情况和到岗时间吗"
    if dlg.goal == "advance_resume":
        return "可以的 您直接发附件简历过来就行 我看完再跟您聊"
    if dlg.goal == "answer_and_ask":
        return "好的 我按您刚问的说～您方便也简单介绍下近况吗"
    # ask_background / ask_one_thing
    if dlg.said.asked_intro:
        return "收到 您更想先了解上班时间还是岗位内容呀"
    return "好的 方便的话简单说下您目前在职情况和相关经历呗"


def draft_repeats_hiring(draft: str) -> bool:
    return bool(_HIRING_RE.search(draft or ""))


def is_near_duplicate(draft: str, recent: list[str], *, threshold: float = 0.78) -> tuple[bool, float]:
    best = 0.0
    for prev in recent or []:
        s = text_similarity(draft, prev)
        if s > best:
            best = s
        if s >= threshold:
            return True, best
    return False, best


def apply_outbound_gate(
    draft: str,
    dlg: DialogState,
    *,
    job_title: str = "",
    question: str = "",
    threshold: float | None = None,
) -> tuple[str, str | None]:
    """发送前门禁。返回 (draft, rewrite_reason|None)。"""
    text = (draft or "").strip()
    if not text or not dlg.enabled:
        return text, None
    thr = 0.78 if threshold is None else float(threshold)

    dup, score = is_near_duplicate(text, dlg.recent_recruiter, threshold=thr)
    if dup:
        alt = progress_template(dlg, job_title=job_title, question=question)
        # 推进句若仍撞车，再换一条更短的
        dup2, _ = is_near_duplicate(alt, dlg.recent_recruiter, threshold=thr)
        if dup2:
            alt = "收到 您方便再具体说下想了解哪方面吗"
        return alt, f"dedup:{score:.2f}"

    # 已说过还在招，且本轮不是「短确认」目标，却又生成完整还招句 → 改写
    if (
        dlg.said.told_hiring
        and dlg.goal != "short_hiring_ack"
        and dlg.goal != "confirm_hiring"
        and draft_repeats_hiring(text)
        and not dlg.candidate_reask_hiring
    ):
        alt = progress_template(dlg, job_title=job_title, question=question)
        if draft_repeats_hiring(alt) and dlg.goal != "confirm_hiring":
            alt = "好的 方便简单介绍下您的近况吗"
        return alt, "said_hiring_lock"

    # 再次确认还招：不允许长开场，压成短确认+推进
    if dlg.goal == "short_hiring_ack" and draft_repeats_hiring(text):
        # 允许「在的/还招」半句；超过约 18 字或含完整开场腔则改写
        if len(_norm(text)) > 18 or "还在招的" in text or "人事还在招" in text:
            return progress_template(dlg, job_title=job_title, question=question), "short_hiring_ack"

    return text, None
