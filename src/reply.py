"""生成层：Jev 选题 → FAQ 事实口径（可按自由度改写）→ LLM。"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from src.judge import _is_useless_draft, is_simple_opener
from src.policy import get_job, load_env, load_merged_faq, load_policy, resolve_position

# FAQ 标题 → 给 Jev 的判定说明（含口语同义，如「待遇」→ 薪资）
_FAQ_CRITERIA_HINTS: dict[str, str] = {
    "薪资范围": "询问薪资、待遇、工资、底薪、提成、月薪、钱多少等报酬",
    "五险一金": "询问五险、一金、社保、公积金、保险缴纳",
    "上班时间": "询问上下班时间、几点上班、工作时段、班次",
    "工作内容": "询问做什么、岗位职责、工作内容、干什么活",
    "是否有加班": "询问加班、加班费、是否要加班",
    "休假": "询问休假、休息、调休、双休、假期、请假、每周休几天",
    "工作地点": "询问地址、地点、在哪、位置、通勤",
    "年龄要求限制": "询问年龄限制、多大可以、超龄、招聘要求、门槛、条件、有什么要求",
    "岗位": "询问招什么岗、什么岗位、有哪些岗位、岗位名称",
    "打招呼": "简单寒暄、问好、在吗、还招人吗等开场",
    "表示不感兴趣": "明确表示不感兴趣、不考虑、算了",
    "闲聊,跟招聘和求职无关的话题": "与招聘求职无关的闲聊、天气、娱乐、私人问题等",
    "学历要求": "询问学历、文凭、要不要大专本科",
    "招聘工具": "询问用什么招聘工具/平台、是否用boss/直聘、boss嘛、是不是boss等（含确认问）",
    "用什么工具负责招聘": "同招聘工具：用什么平台招人、是否用boss",
}

_TOPIC_HEURISTICS: list[tuple[str, tuple[str, ...]]] = [
    ("薪资范围", ("待遇", "薪资", "工资", "底薪", "提成", "月薪", "年薪", "多少钱", "薪酬", "餐补", "补贴", "年终", "几薪")),
    ("五险一金", ("五险", "一金", "社保", "公积金", "保险")),
    ("上班时间", ("几点", "上班时间", "下班", "工作时间", "班次", "几点到")),
    ("工作内容", ("工作内容", "做什么", "干什么", "职责", "岗位职责")),
    ("是否有加班", ("加班",)),
    ("休假", ("休假", "调休", "双休", "休息日", "假期", "请假", "休息几天", "一周休")),
    ("工作地点", ("地址", "地点", "在哪", "哪儿", "位置", "通勤", "坐落")),
    ("年龄要求限制", ("年龄", "多大", "超龄", "岁", "有什么要求", "什么要求", "招聘要求", "入职要求", "门槛", "条件")),
    ("岗位", ("什么岗位", "招什么", "哪些岗位", "岗位有哪些", "几个岗位", "招哪些")),
    ("学历要求", ("学历", "大专", "本科", "文凭")),
    ("招聘工具", ("boss", "直聘", "什么工具", "用什么工具", "招聘工具", "什么平台", "哪个平台", "用啥招", "用boss", "是boss")),
    ("用什么工具负责招聘", ("boss", "直聘", "什么工具", "用什么工具", "招聘工具", "什么平台", "用boss")),
    ("打招呼", ("你好", "您好", "在吗", "在嘛", "还招", "招人吗", "嗨", "哈喽")),
    ("表示不感兴趣", ("不感兴趣", "没兴趣", "不考虑", "不想做", "不合适", "算了", "不聊了", "不想聊", "暂不考虑")),
    ("闲聊,跟招聘和求职无关的话题", ("天气", "吃什么", "电视剧", "笑话", "比特币", "外卖", "抖音", "红包", "结婚", "借钱")),
]

# 确认问：吗/嘛/是不是…（事实不变，答「是的」或纠正）
_CONFIRM_TAIL_RE = re.compile(
    r"(吗|嘛|么|吧|是不是|对吧|对吗|是吧|是嘛|对不|是不)[\s?？!！.。~～]*$",
    re.I,
)
_CONFIRM_MARK_RE = re.compile(r"(是不是|对吧|对吗|是吧|是嘛|对不对|是不)", re.I)

_GREETING_VARIANTS_NAMED = (
    "在的～{title}还在招，您方便简单介绍下近况吗？",
    "嗨，还在招的。方便聊两句您这边的情况吗？",
    "在的呀，岗位还在招。您目前方便简单说下经历吗？",
)

_GREETING_VARIANTS_PLAIN = (
    "在的～还在招，方便简单介绍下您近况吗？",
    "嗨，还在招的。方便聊两句您这边情况吗？",
    "在的呀，方便简单说下目前经历吗？",
)

# 闲聊 FAQ 标题前缀（与 faq.md 对齐）
_CHAT_OFFTOPIC_PREFIXES = ("闲聊", "无关")
_GREETING_FAQ_PREFIXES = ("打招呼", "寒暄", "开场")


def load_faq_text(path: str | Path) -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def parse_faq_sections(faq: str) -> list[dict[str, str]]:
    """解析 ## 标题 FAQ，返回 [{title, body}, ...]。"""
    items: list[dict[str, str]] = []
    for block in re.split(r"\n##\s+", faq or ""):
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


def generate_reply(
    question: str,
    *,
    job_id: str | None = None,
    policy_path: str = "config/policy.yaml",
    intent: str = "other",
    history: list[dict[str, Any]] | None = None,
    chat_position: str | None = None,
    ambiguous: bool = False,
) -> dict[str, Any]:
    """生成回复草稿（默认不发送）。2.0：阶段/已说/目标 + Jev 辅诊 + FAQ/LLM。

    history: 可选会话消息 [{role, text}, ...]，role 为 candidate/recruiter/system。
    chat_position: boss chat「沟通职位」，有则围绕该岗位答，勿罗列全部岗位。
    ambiguous: 判断层认为短句/歧义时，强制提示结合上文。
    """
    from src.dialog_v2 import (
        apply_outbound_gate,
        build_dialog_state,
        memory_prompt_block,
        progress_template,
    )

    load_env()
    policy = load_policy(policy_path)
    position_name = _infer_position_from_history(history, chat_position or "")
    matched = resolve_position(policy, position_name, job_id=job_id)
    job = get_job(policy, (matched or {}).get("id") if matched else job_id)
    if matched and matched.get("title"):
        job = {**job, "title": matched.get("title") or job.get("title")}
    faq, sections, _pos = load_merged_faq(policy, position=matched, chat_position=position_name)
    position = position_name or (str(matched.get("title") or "") if matched else "")
    ambiguous_hint = ""
    if ambiguous or (history and len((question or "").strip()) <= 8):
        ambiguous_hint = "候选人最新一句较短或可能歧义，必须结合上文理解其真实意图后再答。"

    dlg = build_dialog_state(history, question, intent=intent, policy=policy)
    mem_block = memory_prompt_block(dlg)
    thr = ((policy.get("dialog_v2") or {}).get("dedup_threshold"))
    try:
        thr_f = float(thr) if thr is not None else None
    except (TypeError, ValueError):
        thr_f = None

    def _finalize(result: dict[str, Any]) -> dict[str, Any]:
        draft = (result.get("draft") or "").strip()
        gated, why = apply_outbound_gate(
            draft,
            dlg,
            job_title=str(job.get("title") or ""),
            question=question,
            threshold=thr_f,
        )
        out = {**result, "draft": gated, "dialog": dlg.to_public()}
        if why:
            out["gate"] = why
            out["reason"] = f"{result.get('reason') or ''}|gate:{why}".strip("|")
        return out

    for word in policy.get("sensitive_words") or []:
        if word and word in question:
            return _finalize({"draft": "", "handoff": True, "reason": f"敏感词：{word}"})

    answers = (job.get("answers") or {}) if isinstance(job.get("answers"), dict) else {}
    if intent in answers and str(answers[intent]).strip():
        draft = str(answers[intent]).strip()
        if not _is_useless_draft(draft):
            return _finalize({
                "draft": draft,
                "handoff": False,
                "reason": "job_answer",
                "mode": policy.get("send_mode"),
                "intent": intent,
            })

    # 不感兴趣：礼貌收尾，不再推销/追问
    if intent == "not_interested" or dlg.goal == "polite_close":
        close_sec = _find_faq_section(sections, ("表示不感兴趣", "不感兴趣"))
        if close_sec and (close_sec.get("body") or "").strip():
            draft = (close_sec.get("body") or "").strip()
            return _finalize({
                "draft": draft,
                "handoff": False,
                "reason": "not_interested:faq",
                "mode": policy.get("send_mode"),
                "intent": "not_interested",
                "faq_title": close_sec.get("title") or "表示不感兴趣",
            })
        return _finalize({
            "draft": progress_template(dlg, job_title=str(job.get("title") or ""), question=question),
            "handoff": False,
            "reason": "not_interested:close",
            "mode": policy.get("send_mode"),
            "intent": "not_interested",
        })

    # 寒暄：2.0 阶段锁 — 已离开 open / 已说过还在招 → 推进，禁止全量打招呼复读
    if _should_route_greeting(intent, question):
        if dlg.enabled and not dlg.allow_full_greeting:
            return _finalize(
                _reply_progress_branch(
                    question,
                    policy=policy,
                    job=job,
                    history=history,
                    chat_position=position,
                    intent=intent,
                    dlg=dlg,
                    mem_block=mem_block,
                    ambiguous_hint=ambiguous_hint,
                )
            )
        return _finalize(
            _reply_greeting_branch(
                question,
                policy=policy,
                job=job,
                sections=sections,
                history=history,
                chat_position=position,
                intent=intent,
                mem_block=mem_block,
            )
        )

    if intent == "resume" or _wants_send_resume(question):
        return _finalize({
            "draft": "可以的 您直接发附件简历过来就行 我看完再跟您聊哈",
            "handoff": False,
            "reason": "resume_template",
            "mode": policy.get("send_mode"),
            "intent": "resume",
        })

    faq_hit = _reply_from_faq(question, sections)
    if faq_hit:
        title = str(faq_hit["title"])
        # 2.0：已过开场后禁止再命中「打招呼」FAQ（否则易复读还在招）
        if (
            dlg.enabled
            and not dlg.allow_full_greeting
            and (title.startswith("打招呼") or title.startswith("寒暄") or title.startswith("开场"))
        ):
            return _finalize(
                _reply_progress_branch(
                    question,
                    policy=policy,
                    job=job,
                    history=history,
                    chat_position=position,
                    intent=intent,
                    dlg=dlg,
                    mem_block=mem_block,
                    ambiguous_hint=ambiguous_hint,
                )
            )
        raw_body = str(faq_hit.get("body") or "")
        body = _scope_faq_body_to_position(title, raw_body, position)
        if _need_position_clarify(title, body, position):
            draft = (
                "你现在沟通的是哪个岗位呀 我按那个跟你说工作内容～"
                if not position
                else f"你沟通的是{position} 这块工作内容我这边还没单独口径 方便说下你想了解哪块吗"
            )
            return _finalize({
                "draft": draft,
                "handoff": False,
                "reason": f"faq:{title}:need_position",
                "mode": policy.get("send_mode"),
                "intent": intent,
                "faq_title": title,
                "faq_confidence": faq_hit.get("confidence"),
                "faq_engine": faq_hit.get("engine"),
                "chat_position": position or None,
            })
        draft, how = _faq_draft_with_freedom(
            question=question,
            title=title,
            body=body,
            policy=policy,
            job=job,
            history=history,
            chat_position=position,
            mem_block=mem_block,
        )
        freedom = _faq_freedom_level(policy)
        return _finalize({
            "draft": draft,
            "handoff": False,
            "reason": f"faq:{title}:{how}",
            "mode": policy.get("send_mode"),
            "intent": intent,
            "faq_title": title,
            "faq_confidence": faq_hit.get("confidence"),
            "faq_engine": faq_hit.get("engine"),
            "faq_freedom": freedom,
            "faq_rewrite": how,
            "chat_position": position or None,
        })

    # 分支外 / other：承接 + 本轮目标推进
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    base = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    if not key or not base:
        draft = progress_template(dlg, job_title=str(job.get("title") or ""), question=question)
        if _is_useless_draft(draft):
            draft = _safe_fallback(question, faq, job, dlg=dlg)
        return _finalize({
            "draft": draft,
            "handoff": False,
            "reason": "other_no_llm_progress",
            "mode": policy.get("send_mode"),
            "intent": intent,
            "faq_title": "other",
        })

    history_block = _format_chat_history(history, question)
    fact_block = _facts_for_other_question(question, sections, position)
    pos_line = f"当前沟通职位：{position}\n" if position else "当前沟通职位：未知（勿编造具体岗位名）\n"
    casual = _casual_style_enabled(policy)
    system = (
        "你是公司招聘 HR，正在 Boss 直聘上跟候选人文字聊天。"
        "只输出给候选人看的一条回复正文（约 12～70 字），禁止思考过程、标题、引号说明。"
        "只回答候选人最新一条里明确问到的点；未问到的薪资/五险/工时/休假等一律不要提。"
        "必须遵守给定事实，不可编造；有「当前沟通职位」时只围绕该岗位说，不要把其它岗位一股脑列出。"
        "禁止只回复「无」「好」「嗯」等单字。"
        f"{ambiguous_hint}"
        f"{mem_block}"
        f"{_oral_style_rules(casual)}"
    )
    user = (
        f"岗位配置名：{job.get('title')}\n"
        f"{pos_line}"
        f"候选人意图标签：{intent}\n"
        f"本轮必须完成的目标：{dlg.goal_hint}\n"
        f"可用事实（仅供回答所问；不要主动扩展）：\n{fact_block}\n\n"
        f"{history_block}"
        f"候选人最新一条：{question}\n"
        "请直接写出回复正文（承接最新一句并完成目标；不要复读自己说过的开场白）。"
    )
    try:
        text = _llm_chat(
            user,
            purpose="generate_reply_other",
            system=system,
            history=history,
            question=question,
            temperature=_reply_temperature(policy, 0.72),
        ).strip()
    except Exception as exc:
        draft = progress_template(dlg, job_title=str(job.get("title") or ""), question=question)
        if _is_useless_draft(draft):
            draft = _safe_fallback(question, faq, job, dlg=dlg)
        return _finalize({
            "draft": draft,
            "handoff": False,
            "reason": f"other_llm_error:{exc}",
            "mode": policy.get("send_mode"),
            "intent": intent,
            "faq_title": "other",
        })

    if _is_useless_draft(text) or _looks_like_reasoning_leak(text):
        draft = progress_template(dlg, job_title=str(job.get("title") or ""), question=question)
        if _is_useless_draft(draft):
            draft = _safe_fallback(question, faq, job, dlg=dlg)
        return _finalize({
            "draft": draft,
            "handoff": False,
            "reason": "other_llm_useless",
            "mode": policy.get("send_mode"),
            "intent": intent,
            "faq_title": "other",
        })

    return _finalize({
        "draft": text,
        "handoff": False,
        "reason": "llm:other",
        "mode": policy.get("send_mode"),
        "intent": intent,
        "faq_title": "other",
        "chat_position": position or None,
    })


def _format_chat_history(
    history: list[dict[str, Any]] | None,
    question: str,
    *,
    max_turns: int = 16,
) -> str:
    """把会话消息压成给 LLM 的上下文块（兜底文本；主路径还会走 messages 数组）。"""
    turns = _history_turns(history, question, max_turns=max_turns)
    if not turns:
        return (
            "近期对话：无（若最新一条是「可以/好的」等短句，按同意继续沟通理解，"
            "结合事实口径礼貌推进，不要假装已知对方未说过的信息）\n\n"
        )
    lines = [f"{'候选人' if r == 'user' else 'HR'}：{t}" for r, t in turns]
    return "近期对话（从早到晚，务必结合理解最新一句）：\n" + "\n".join(lines) + "\n\n"


def _history_turns(
    history: list[dict[str, Any]] | None,
    question: str,
    *,
    max_turns: int = 16,
) -> list[tuple[str, str]]:
    """归一化为 [(user|assistant, text), ...]，去掉与最新一句重复的末条候选人消息。"""
    if not history:
        return []
    role_map = {
        "candidate": "user",
        "recruiter": "assistant",
        "you": "assistant",
        "user": "user",
        "assistant": "assistant",
        "hr": "assistant",
    }
    qnorm = (question or "").strip()
    turns: list[tuple[str, str]] = []
    for m in history:
        role = role_map.get(str(m.get("role") or "").lower())
        text = (m.get("text") or m.get("content") or "").strip()
        if not role or not text or role == "system":
            continue
        turns.append((role, text[:300]))
    if turns and qnorm and turns[-1][0] == "user" and turns[-1][1] == qnorm:
        turns = turns[:-1]
    if len(turns) > max_turns:
        turns = turns[-max_turns:]
    return turns


def _reply_from_faq(question: str, sections: list[dict[str, str]]) -> dict[str, Any] | None:
    if not sections:
        return None
    title, conf, engine = _pick_faq_topic(question, sections)
    if not title or title == "other":
        return None
    body = next((s["body"] for s in sections if s["title"] == title), "")
    if not body:
        return None
    # 兼容旧标题：用什么工具负责招聘 → 与招聘工具同一口径
    draft = _format_faq_reply(title, body)
    if _is_useless_draft(draft):
        return None
    return {
        "title": title,
        "body": body,
        "draft": draft,
        "confidence": conf,
        "engine": engine,
    }


def _faq_freedom_level(policy: dict[str, Any]) -> int:
    raw = (policy.get("reply") or {}).get("faq_freedom")
    try:
        n = int(raw if raw is not None else 2)
    except (TypeError, ValueError):
        n = 2
    return max(0, min(3, n))


def _faq_draft_with_freedom(
    *,
    question: str,
    title: str,
    body: str,
    policy: dict[str, Any],
    job: dict[str, Any],
    history: list[dict[str, Any]] | None,
    chat_position: str = "",
    force_freedom: int | None = None,
    mem_block: str = "",
) -> tuple[str, str]:
    """事实口径不变；自由度>0 时改写。返回 (正文, 方式 verbatim|llm|light|confirm_*)。

    只改写「当前栏目」一条；禁止把其它 FAQ 栏目塞进同一句。
    确认问（用boss嘛）：与口径一致 → 短答「是的」；不一致 → 纠正。
    force_freedom: 若给定，覆盖 policy 里的 faq_freedom（寒暄分支会强制 ≥2）。
    """
    verbatim = _format_faq_reply(title, body)
    freedom = _faq_freedom_level(policy) if force_freedom is None else max(0, min(3, int(force_freedom)))

    # 确认问：优先短答是/否，不把「是的」写进 FAQ
    if _is_confirm_question(question):
        align = _confirm_aligns_with_fact(question, body)
        if align is True:
            return _light_confirm_yes(body), "confirm_yes"
        if align is False:
            return _light_confirm_no(body), "confirm_no"

    if freedom <= 0:
        return verbatim, "verbatim"

    key = (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    base = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    if not key or not base:
        return _light_paraphrase_faq(title, body, freedom, chat_position), "light"

    history_block = _format_chat_history(history, question, max_turns=6)
    temp_map = {1: 0.62, 2: 0.82, 3: 0.98}
    pos = (chat_position or "").strip()
    job_key = _detect_job_key(pos)
    if pos and job_key:
        pos_hint = (
            f"当前沟通职位：{pos}（按「{job_key}」理解）。"
            f"事实口径已只保留该岗位；严禁提其它岗位（如人事/主播/歌手里非当前岗）的职责或清单。"
        )
    elif pos:
        pos_hint = f"当前沟通职位：{pos}。只围绕该职位说，不要罗列其它岗位。"
    else:
        pos_hint = "当前沟通职位：未知。不要主动罗列多个岗位的职责。"
    confirm_hint = ""
    if _is_confirm_question(question):
        confirm_hint = (
            "候选人这句是确认问（吗/嘛/是不是）。"
            "若其说法与事实口径一致：优先短答「是的/对的」，可带半句口径；"
            "若不一致：先否定再纠正为口径。不要复读成「用什么工具负责招聘：…」。\n"
        )
    casual = _casual_style_enabled(policy)
    system = (
        "你是公司招聘 HR，正在 Boss 直聘上跟候选人文字聊天。"
        "只输出一条回复正文（约 8～55 字），不要标题、不要引号、不要思考过程。"
        "只改写下面「事实栏目」这一条的内容；严禁补充候选人没问的其它信息"
        "（未问则不要提薪资/五险/工时/休假/加班/岗位清单等）。"
        "必须严格依据给定事实口径，不可编造；口径里没有的岗位职责一句都不要提。"
        "禁止「栏目名：内容」说明书口吻；每次措辞不同，意思不变。"
        f"{mem_block}"
        f"{_oral_style_rules(casual)}"
    )
    user = (
        f"岗位配置名：{job.get('title')}\n"
        f"{pos_hint}\n"
        f"{confirm_hint}"
        f"事实栏目（只答这个）：{title}\n"
        f"事实口径（只能用这里的信息，且已按沟通职位裁剪）：\n{body}\n\n"
        f"{history_block}"
        f"候选人问：{question}\n"
        f"改写自由度：{freedom}/3。\n"
        "请直接写出这次的回复（像微信聊天；只复述上面口径，不要扩展到其它岗）。"
    )
    try:
        text = _llm_chat(
            user,
            purpose="faq_paraphrase",
            system=system,
            history=history,
            question=question,
            temperature=_reply_temperature(policy, temp_map.get(freedom, 0.82)),
        ).strip()
    except Exception:
        return _light_paraphrase_faq(title, body, freedom, chat_position), "light"

    if _is_useless_draft(text) or _looks_like_reasoning_leak(text):
        return _light_paraphrase_faq(title, body, freedom, chat_position), "light"
    if (
        _looks_like_faq_verbatim(text, title, body, verbatim)
        or _faq_answer_overbroad(text, title)
        or _faq_leaks_other_jobs(text, title, chat_position, body)
    ):
        try:
            text2 = _llm_chat(
                user + "\n注意：只答本栏目、只限当前沟通职位对应口径，一句即可，别提其它岗。",
                purpose="faq_paraphrase_retry",
                system=system,
                history=history,
                question=question,
                temperature=min(1.0, _reply_temperature(policy, temp_map.get(freedom, 0.82)) + 0.12),
            ).strip()
            if (
                text2
                and not _is_useless_draft(text2)
                and not _looks_like_reasoning_leak(text2)
                and not _looks_like_faq_verbatim(text2, title, body, verbatim)
                and not _faq_answer_overbroad(text2, title)
                and not _faq_leaks_other_jobs(text2, title, chat_position, body)
            ):
                return text2, "llm"
        except Exception:
            pass
        return _light_paraphrase_faq(title, body, freedom, chat_position), "light"
    return text, "llm"


# 各栏目「不该出现」的其它事实痕迹（用于拦住一股脑输出）
_OVERBROAD_MARKERS: dict[str, tuple[str, ...]] = {
    "年龄要求限制": ("提成", "五险", "加班", "调休", "13:00", "21:00", "一点到", "九点", "主播", "歌手", "人事"),
    "是否有加班": ("提成", "五险", "18", "40", "调休", "年龄"),
    "薪资范围": ("五险", "加班", "调休", "18", "40岁", "一点到"),
    "五险一金": ("提成", "加班", "调休", "18", "40"),
    "上班时间": ("提成", "五险", "18", "40", "调休"),
    "休假": ("提成", "五险", "加班", "18", "40", "一点到"),
    "工作地点": ("提成", "五险", "加班", "调休", "18", "40"),
    "工作内容": ("提成", "五险", "加班", "调休", "18-40", "40岁"),
    "岗位": ("提成", "五险", "加班", "调休", "一点到", "2000"),
}


def _faq_answer_overbroad(text: str, title: str) -> bool:
    """回复是否夹带了大量无关栏目信息。"""
    t = text or ""
    markers = _OVERBROAD_MARKERS.get(title) or ()
    hits = sum(1 for m in markers if m in t)
    # 命中 2 个以上异栏目关键词，视为「一股脑」
    return hits >= 2


def _faq_leaks_other_jobs(
    text: str, title: str, position: str, scoped_body: str
) -> bool:
    """工作内容/岗位：回复是否泄露出沟通职位以外的其它岗介绍。"""
    if title not in {"工作内容", "岗位"}:
        return False
    t = (text or "").strip()
    body = scoped_body or ""
    if not t:
        return False
    mentioned = _job_keys_mentioned(t)
    allowed = _detect_job_key(position)
    if not allowed:
        return len(mentioned) >= 2
    # 其它岗名 + 像在单独介绍该岗（「主播的话就是…」）
    for key in mentioned:
        if key == allowed:
            continue
        if re.search(rf"{re.escape(key)}\s*(岗位|这边|的话|主要|就是|负责)", t):
            return True
    # 口径里没有的典型职责词
    for frag in ("娱乐直播", "驻唱", "才艺表演", "唱歌"):
        if frag in t and frag not in body:
            return True
    return False


def _split_work_content_parts(body: str) -> list[tuple[str | None, str]]:
    """把工作内容拆成 [(岗位key或None, 片段), ...]。"""
    raw = (body or "").strip()
    if not raw:
        return []
    chunks = [p.strip() for p in _MULTI_JOB_SPLIT.split(raw) if p.strip()]
    out: list[tuple[str | None, str]] = []
    for ch in chunks:
        # 「人事：负责…」或「人事负责…」
        key = None
        m = re.match(r"^(人事|主播|歌手|前台)\s*[：:]\s*(.+)$", ch)
        if m:
            label, rest = m.group(1), m.group(2).strip()
            key = _detect_job_key(label) or label
            out.append((key, rest or ch))
            continue
        key = _detect_job_key(ch)
        out.append((key, ch))
    return out


_JOB_ALIASES: dict[str, tuple[str, ...]] = {
    "人事": ("人事", "hr", "招聘专员", "前台"),
    "主播": ("主播", "直播"),
    "歌手": ("歌手", "驻唱", "唱歌"),
}

# 工作内容等多岗口径：按分号/句号切段后，段内需带岗位名才算该岗
_MULTI_JOB_SPLIT = re.compile(r"[；;。\n]+")


def _detect_job_key(position: str) -> str | None:
    p = (position or "").strip()
    if not p:
        return None
    pl = p.lower()
    # 长别名优先，避免「招聘」误伤；同串多岗时取最先命中的明确岗
    hits: list[tuple[int, str]] = []
    for key, aliases in _JOB_ALIASES.items():
        for a in aliases:
            idx = pl.find(a.lower()) if a.isascii() else p.find(a)
            if idx < 0 and a.lower() in pl:
                idx = pl.find(a.lower())
            if idx >= 0:
                hits.append((idx, key))
                break
    if not hits:
        return None
    hits.sort(key=lambda x: x[0])
    return hits[0][1]


def _job_keys_mentioned(text: str) -> set[str]:
    t = text or ""
    found: set[str] = set()
    for key, aliases in _JOB_ALIASES.items():
        if any(a in t or (a.isascii() and a.lower() in t.lower()) for a in aliases):
            found.add(key)
    return found


def _infer_position_from_history(
    history: list[dict[str, Any]] | None, explicit: str = ""
) -> str:
    """从显式沟通职位或历史系统句（沟通的职位-xxx）推断。"""
    if (explicit or "").strip():
        return explicit.strip()
    for m in reversed(history or []):
        text = (m.get("text") or "").strip()
        if not text:
            continue
        for pat in (
            r"沟通的职位[-：:]\s*(.+)$",
            r"沟通职位[=：:]\s*(.+)$",
            r"当前沟通职位[=：:]\s*(.+)$",
        ):
            m2 = re.search(pat, text)
            if m2:
                return m2.group(1).strip()[:40]
    return ""


def _scope_faq_body_to_position(title: str, body: str, position: str) -> str:
    """有沟通职位时，岗位/工作内容只保留对应片段；多岗口径禁止整段下发。"""
    body = (body or "").strip()
    key = _detect_job_key(position)
    if title == "岗位":
        if key:
            return key
        return body
    if title != "工作内容":
        return body
    if not body:
        return ""
    parts = _split_work_content_parts(body)
    if key:
        for pk, seg in parts:
            if pk == key or key in seg or any(a in seg for a in _JOB_ALIASES.get(key, ())):
                if pk == key:
                    return f"{key}：{seg}" if not seg.startswith(key) else seg
                return seg
        return ""
    keys_hit = {pk for pk, _ in parts if pk}
    if len(parts) > 1 or len(keys_hit) >= 2:
        return ""
    return body


def _need_position_clarify(title: str, body: str, position: str) -> bool:
    """工作内容等：缺沟通职位且口径是多岗时，应反问岗位而非泄密。"""
    if title != "工作内容":
        return False
    if (position or "").strip() and _detect_job_key(position):
        # 有职位但裁剪后为空 → 也视为无法精准答，反问/说明
        return not (body or "").strip()
    return not (body or "").strip()


def _facts_for_other_question(
    question: str,
    sections: list[dict[str, str]],
    position: str,
) -> str:
    """other 路径：最多给 1～2 条相关事实，禁止整份 FAQ。"""
    if not sections:
        return "(事实库为空)"
    title, conf, _engine = _pick_faq_topic(question, sections)
    lines: list[str] = []
    if position:
        lines.append(f"当前沟通职位：{position}")
    if title and title != "other" and conf >= 0.4:
        body = next((s["body"] for s in sections if s["title"] == title), "")
        body = _scope_faq_body_to_position(title, body, position)
        if body:
            lines.append(f"## {title}\n{body}")
            return "\n".join(lines)
    # 宽泛追问：只给岗位名（若有沟通职位）或岗位栏目一行
    if position:
        key = _detect_job_key(position) or position
        lines.append(f"## 岗位\n{key}")
        return "\n".join(lines)
    post = next((s for s in sections if s["title"] == "岗位"), None)
    if post:
        lines.append(f"## 岗位\n{post['body']}")
    if not lines:
        lines.append("(无匹配栏目；请礼貌反问对方具体想了解哪方面，不要编造明细)")
    return "\n".join(lines)


def _looks_like_faq_verbatim(text: str, title: str, body: str, verbatim: str) -> bool:
    """识别仍像说明书/原文直出的回复。"""
    t = (text or "").strip()
    if not t:
        return True
    if t == verbatim or t == (body or "").strip():
        return True
    if t.startswith(f"{title}：") or t.startswith(f"{title}:"):
        return True
    robotic = (
        "待遇这边：",
        "五险一金：",
        "加班情况：",
        "休假方面：",
        "地址在：",
        "年龄这边：",
        "工作内容主要是：",
    )
    if any(t.startswith(p) for p in robotic):
        return True
    b = (body or "").strip()
    if b and b in t and len(t) <= len(b) + 12:
        return True
    return False


def _light_paraphrase_faq(
    title: str, body: str, freedom: int = 1, chat_position: str = ""
) -> str:
    """无 LLM / 改写失败时的口语模板轮换：意思用事实，句子每次不同；只答本栏目。"""
    body = (body or "").strip().rstrip("。．.；;")
    key = _detect_job_key(chat_position)
    pos_bit = f"（您咨询的{key}岗）" if key else ""
    templates: dict[str, tuple[str, ...]] = {
        "薪资范围": (
            "待遇的话是{b}，您看可以吗？",
            "薪资这块目前是{b}～",
            "跟您说下，这边是{b}。",
            "可以的，待遇按{b}来。",
        ),
        "五险一金": (
            "五险一金这边是{b}。",
            "保险公积金情况：{b}～",
            "跟您说下，{b}。",
        ),
        "上班时间": (
            "上班时间是{b}。",
            "工时这边：{b}～",
            "时间安排是{b}。",
        ),
        "工作内容": (
            "日常主要是{b}。",
            "工作上大概是{b}～",
            "岗位内容这边：{b}。",
        ),
        "是否有加班": (
            "加班这块：{b}。",
            "关于加班，{b}～",
            "跟您说下，{b}。",
        ),
        "休假": (
            "休息休假是{b}。",
            "假期这块：{b}～",
            "跟您同步，{b}。",
        ),
        "工作地点": (
            "地点在{b}。",
            "办公位置是{b}～",
            "地址这边：{b}。",
        ),
        "年龄要求限制": (
            "年龄要求是{b}。",
            "年龄这边：{b}～",
            "我们这边{b}。",
            "入职要求主要是年龄{b}。",
        ),
        "岗位": (
            "目前在招的是{b}。",
            "岗位这边是{b}～",
            "我们招{b}。",
        ),
    }
    pool = templates.get(
        title,
        (
            "跟您说下，{b}。",
            "可以的，{b}～",
            "这边情况是{b}。",
        ),
    )
    text = random.choice(pool).format(b=body)
    if pos_bit and title in {"年龄要求限制", "工作内容", "薪资范围"} and freedom >= 2:
        text = text.replace("。", f"{pos_bit}。", 1) if "。" in text else text + pos_bit
    if freedom >= 3:
        tails = ("", "您还有想问的随时说～", "不清楚再问我就行。")
        text = text + random.choice(tails)
    return text.strip()


def _pick_faq_topic(question: str, sections: list[dict[str, str]]) -> tuple[str, float, str]:
    """返回 (栏目名|other, 置信度, 引擎)。other = 不在事实库内，应交 LLM。"""
    titles = [s["title"] for s in sections]
    # 关键词高置信先命中：避免 Jev 把「待遇」误判为 other
    heur_title, heur_conf, heur_engine = _heuristic_faq_topic(question, titles, sections)
    if heur_title != "other" and heur_conf >= 0.8:
        return heur_title, heur_conf, heur_engine

    try:
        from src.jev import choice, get_choice, jev_configured, system_one

        if jev_configured():
            criteria: dict[str, str | None] = {}
            for t in titles:
                criteria[t] = _FAQ_CRITERIA_HINTS.get(t) or f"询问与「{t}」直接相关的问题"
            criteria["other"] = (
                "与下列事实栏目均无关，需要自由回复"
                "（寒暄以外的综合问题、约面细节、个人情况追问、库中未覆盖的内容等）"
            )
            data = system_one(
                state={"candidate_message": question, "faq_topics": titles},
                questions={
                    "faq_topic": choice(
                        "候选人这句话主要在问哪一类岗位事实？"
                        "必须选最贴切的一项；"
                        "问「待遇/工资/底薪/提成/钱多少」必须选薪资相关栏目（如薪资范围）；"
                        "问五险/社保选五险一金；问几点上班选上班时间；"
                        "问用什么工具/平台/是否用boss/用boss嘛 → 选招聘工具（或同义栏目）；"
                        "确认问（吗/嘛）也按所确认的事实选题，不要选 other；"
                        "若库中没有对应栏目、或需要综合自由回答，选 other。",
                        criteria,
                    )
                },
                purpose="faq_topic",
            )
            picked, conf = get_choice(data, "faq_topic", "other")
            if picked in titles and conf >= 0.45:
                return picked, conf, "jev"
            # Jev 说 other 或置信不足 → 若启发式有命中则用启发式，否则 other（交给 LLM）
            if heur_title != "other":
                return heur_title, heur_conf, f"fallback_after_jev:{heur_engine}"
            return "other", conf, "jev"
    except Exception:
        pass
    return heur_title, heur_conf, heur_engine


def _heuristic_faq_topic(
    question: str,
    titles: list[str],
    sections: list[dict[str, str]] | None = None,
) -> tuple[str, float, str]:
    q = (question or "").strip()
    ql = q.lower()
    title_set = set(titles)
    for title, keys in _TOPIC_HEURISTICS:
        if title not in title_set:
            continue
        if any(k.lower() in ql or k in q for k in keys):
            return title, 0.85, "rule"
    for t in titles:
        if t and t in q:
            return t, 0.9, "rule"
    # 确认问常只提口径里的关键词（如「用boss嘛」），用正文词命中栏目
    if sections:
        q_toks = _fact_tokens(q)
        best: tuple[str, int] | None = None
        for s in sections:
            title = (s.get("title") or "").strip()
            if title not in title_set:
                continue
            overlap = q_toks & _fact_tokens(s.get("body") or "")
            # 标题词也可
            overlap |= q_toks & _fact_tokens(title)
            n = len(overlap)
            if n <= 0:
                continue
            if best is None or n > best[1]:
                best = (title, n)
        if best and best[1] >= 1:
            return best[0], 0.82, "rule_body"
    return "other", 0.0, "rule"


_CONFIRM_TAIL_RE = re.compile(r"(吗|嘛|么|吧)\s*[?？!！.。~～…]*$", re.I)
_CONFIRM_MID_RE = re.compile(r"(是不是|对不对|对吧|是嘛|是吗|有没有)", re.I)


def _is_confirm_question(question: str) -> bool:
    """短句确认问：用boss嘛 / 是双休吗 / 有五险吗。"""
    t = (question or "").strip()
    if not t or len(t) > 36:
        return False
    if _CONFIRM_TAIL_RE.search(t):
        return True
    if _CONFIRM_MID_RE.search(t):
        return True
    return False


def _fact_tokens(text: str) -> set[str]:
    """从事实/问句抽可对齐关键词。"""
    raw = text or ""
    t = raw.lower()
    tokens: set[str] = set()
    for m in re.finditer(r"[a-z]{2,}", t):
        tokens.add(m.group(0))
    for k in (
        "boss",
        "直聘",
        "五险",
        "一金",
        "公积金",
        "社保",
        "双休",
        "单休",
        "调休",
        "加班",
        "提成",
        "底薪",
        "试用期",
        "怀化",
        "人事",
        "主播",
        "歌手",
        "前台",
        "学历",
        "大专",
        "本科",
        "猎聘",
        "拉勾",
        "智联",
    ):
        if k.isascii():
            if k in t:
                tokens.add(k)
        elif k in raw:
            tokens.add(k)
    return tokens


def _confirm_aligns_with_fact(question: str, body: str) -> bool | None:
    """确认问与口径是否一致。True=答是的；False=答不是；None=交给 LLM。"""
    q = (question or "").strip()
    b = (body or "").strip()
    if not q or not b:
        return None
    body_neg = bool(re.search(r"(暂未|没有|不缴纳|未提供|不包|无加班)", b))
    q_ask_has = bool(re.search(r"(有没有|有.{0,6}(吗|嘛)|还有吗)", q))
    q_assert_neg = bool(re.search(r"(没有|不是|没缴|不用|不双休)", q))

    q_toks = _fact_tokens(q)
    b_toks = _fact_tokens(b)
    overlap = q_toks & b_toks

    # 对方点名其它平台/工具，口径里没有 → 否
    alts = ("猎聘", "拉勾", "智联", "前程无忧", "linkedin", "58")
    if any(a in q.lower() or a in q for a in alts) and not any(
        a in b.lower() or a in b for a in alts
    ):
        return False

    if overlap:
        if body_neg and q_ask_has and not q_assert_neg:
            return False  # 有五险吗 + 暂未缴纳
        if body_neg and q_assert_neg:
            return True  # 没有五险吧 + 暂未
        # 「是双休吗」vs「每月四天调休」：问句有双休、口径无双休
        if "双休" in q and "双休" not in b:
            return False
        return True

    if "双休" in q and "双休" not in b and ("调休" in b or "四天" in b):
        return False
    return None


def _light_confirm_yes(body: str) -> str:
    b = (body or "").strip().rstrip("。．.；;")
    short = (
        "是的",
        "对的",
        "是的呀",
        f"是的 {b}",
        f"对 {b}哈",
    )
    return random.choice(short)


def _light_confirm_no(body: str) -> str:
    b = (body or "").strip().rstrip("。．.；;")
    if re.search(r"(暂未|没有|不缴纳)", b):
        opts = (
            f"暂时没有哈 {b}",
            f"没有哦 {b}",
            f"这个没有 {b}～",
        )
    else:
        opts = (
            f"不是哈 {b}",
            f"不是哦 咱们是{b}" if not b.startswith("用") else f"不是哈 {b}",
            f"没有哈 是{b}～" if "用" not in b[:2] else f"不是哈 {b}",
        )
    return random.choice(opts)


def _casual_style_enabled(policy: dict[str, Any] | None) -> bool:
    """默认开启：聊天式标点与语气。policy.reply.casual_style: false 可关。"""
    reply = (policy or {}).get("reply") or {}
    return reply.get("casual_style", True) is not False


def _oral_style_rules(casual: bool) -> str:
    if not casual:
        return "语气礼貌即可。"
    return (
        "语气要像真人 HR 在手机上打字："
        "口语化、短句、偶尔用呀/哈/哦/呢/嘛；"
        "标点随意：可用空格断句、～、省略号，不必每句都用规范句号/问号；"
        "不要公文腔（如「关于…」「建议您向负责人详细确认」「按国家规定」这类书面套话）；"
        "不要列点、不要过度完整的书面复句。"
    )


def _reply_temperature(policy: dict[str, Any] | None, default: float) -> float:
    reply = (policy or {}).get("reply") or {}
    raw = reply.get("temperature")
    if raw is not None:
        try:
            return max(0.1, min(1.2, float(raw)))
        except (TypeError, ValueError):
            pass
    if _casual_style_enabled(policy):
        return default
    return float(os.environ.get("LLM_TEMPERATURE") or 0.3)


def _should_route_greeting(intent: str, question: str) -> bool:
    """真实寒暄/还招人开场才走打招呼分支；闲聊误标 greeting 不进。"""
    if is_simple_opener(question):
        return True
    if intent != "greeting":
        return False
    q = (question or "").strip()
    if not q:
        return False
    # 意图是 greeting 但内容完全不像开场 → 当普通问题继续往下走
    hiring_hint = re.search(
        r"(你好|您好|在吗|在嘛|还招|招人|嗨|哈喽|hi|hello|早上好|下午好|沟通|了解|岗位)",
        q,
        re.I,
    )
    return bool(hiring_hint) and len(q) <= 40


def _find_faq_section(
    sections: list[dict[str, str]], prefixes: tuple[str, ...]
) -> dict[str, str] | None:
    for s in sections:
        title = (s.get("title") or "").strip()
        for p in prefixes:
            if title == p or title.startswith(p):
                return s
    return None


def _greeting_fact_body(
    job: dict[str, Any],
    policy: dict[str, Any],
    sections: list[dict[str, str]],
) -> tuple[str, str]:
    """返回 (口径正文, 来源标签)。优先 FAQ「打招呼」，其次 greet.reply 作口径而非原文直出。"""
    sec = _find_faq_section(sections, _GREETING_FAQ_PREFIXES)
    if sec and (sec.get("body") or "").strip():
        return (sec["body"] or "").strip(), f"faq:{sec.get('title')}"
    custom = ((policy or {}).get("greet") or {}).get("reply")
    if isinstance(custom, str) and custom.strip() and not _is_useless_draft(custom.strip()):
        title = (job.get("title") or "").strip()
        body = custom.strip().replace("{title}", title or "本岗位")
        return body, "greet.reply"
    return (
        "还在招；礼貌确认在、简短回应，并邀请对方用一两句介绍近况或想了解哪方面",
        "default_brief",
    )


def _reply_progress_branch(
    question: str,
    *,
    policy: dict[str, Any],
    job: dict[str, Any],
    history: list[dict[str, Any]] | None,
    chat_position: str,
    intent: str,
    dlg: Any,
    mem_block: str = "",
    ambiguous_hint: str = "",
) -> dict[str, Any]:
    """2.0：greeting 阶段锁后的推进分支（承接 + 单目标，禁止再走全量打招呼）。"""
    from src.dialog_v2 import progress_template

    title = str(job.get("title") or "")
    fallback = progress_template(dlg, job_title=title, question=question)
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    base = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    if not key or not base:
        return {
            "draft": fallback,
            "handoff": False,
            "reason": f"progress:{dlg.goal}:template",
            "mode": policy.get("send_mode"),
            "intent": intent,
            "faq_title": "推进",
            "chat_position": (chat_position or "").strip() or None,
        }

    casual = _casual_style_enabled(policy)
    pos = (chat_position or "").strip()
    pos_line = f"当前沟通职位：{pos}\n" if pos else ""
    system = (
        "你是公司招聘 HR，正在 Boss 直聘上继续跟候选人聊天（不是开场白）。"
        "只输出一条短回复（约 10～45 字）。"
        "禁止再完整重复「还在招/岗位还招」类开场；若对方只是再确认是否在招，半句点头后立刻追问。"
        "必须承接最新一句，并完成给定的本轮目标。"
        f"{ambiguous_hint}"
        f"{mem_block}"
        f"{_oral_style_rules(casual)}"
    )
    user = (
        f"岗位配置名：{job.get('title')}\n"
        f"{pos_line}"
        f"本轮目标：{dlg.goal_hint}\n"
        f"{_format_chat_history(history, question, max_turns=8)}"
        f"候选人最新一条：{question}\n"
        "请直接写出推进回复。"
    )
    try:
        text = _llm_chat(
            user,
            purpose="generate_reply_progress",
            system=system,
            history=history,
            question=question,
            temperature=_reply_temperature(policy, 0.75),
        ).strip()
    except Exception:
        text = ""
    how = "llm"
    if _is_useless_draft(text) or _looks_like_reasoning_leak(text):
        text, how = fallback, "template"
    return {
        "draft": text,
        "handoff": False,
        "reason": f"progress:{dlg.goal}:{how}",
        "mode": policy.get("send_mode"),
        "intent": intent,
        "faq_title": "推进",
        "chat_position": (chat_position or "").strip() or None,
    }


def _reply_greeting_branch(
    question: str,
    *,
    policy: dict[str, Any],
    job: dict[str, Any],
    sections: list[dict[str, str]],
    history: list[dict[str, Any]] | None,
    chat_position: str,
    intent: str,
    mem_block: str = "",
) -> dict[str, Any]:
    """Jev/规则 → 打招呼分支 → LLM（FAQ 口径作约束，不再本地随机模板直出）。"""
    body, src = _greeting_fact_body(job, policy, sections)
    freedom = max(2, _faq_freedom_level(policy))  # 寒暄至少要改写，避免同句复读
    draft, how = _faq_draft_with_freedom(
        question=question,
        title="打招呼",
        body=body,
        policy=policy,
        job=job,
        history=history,
        chat_position=chat_position or "",
        force_freedom=freedom,
        mem_block=mem_block,
    )
    # 若仍落到 light/无效应急，再走寒暄专用 LLM；再不行才用本地短句池
    if how in ("verbatim", "light") or _is_useless_draft(draft):
        draft2 = _llm_greeting_once(
            question,
            body=body,
            policy=policy,
            job=job,
            history=history,
            chat_position=chat_position,
            mem_block=mem_block,
        )
        if draft2:
            draft, how = draft2, "llm"
        else:
            draft, how = _greeting_reply_fallback(job, policy), "fallback"
    return {
        "draft": draft,
        "handoff": False,
        "reason": f"greeting:{src}:{how}",
        "mode": policy.get("send_mode"),
        "intent": "greeting",
        "faq_title": "打招呼",
        "faq_freedom": freedom,
        "faq_rewrite": how,
        "chat_position": (chat_position or "").strip() or None,
    }


def _llm_greeting_once(
    question: str,
    *,
    body: str,
    policy: dict[str, Any],
    job: dict[str, Any],
    history: list[dict[str, Any]] | None,
    chat_position: str,
    mem_block: str = "",
) -> str | None:
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    base = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    if not key or not base:
        return None
    casual = _casual_style_enabled(policy)
    pos = (chat_position or "").strip()
    pos_line = f"当前沟通职位：{pos}\n" if pos else ""
    system = (
        "你是公司招聘 HR，正在 Boss 上回复开场寒暄。"
        "只输出一条很短的聊天回复（约 10～40 字）。"
        "要点：确认还在招/在的；可随口回一句；顺带请对方简单说下近况或想了解什么。"
        "不要长篇介绍岗位清单，不要主动堆薪资五险。"
        f"{mem_block}"
        f"{_oral_style_rules(casual)}"
    )
    user = (
        f"岗位配置名：{job.get('title')}\n"
        f"{pos_line}"
        f"寒暄口径（意思要保留，措辞必须每次不同）：\n{body}\n\n"
        f"{_format_chat_history(history, question, max_turns=4)}"
        f"候选人说：{question}\n"
        "请直接写出回复。"
    )
    try:
        text = _llm_chat(
            user,
            purpose="generate_reply_greeting",
            system=system,
            history=history,
            question=question,
            temperature=_reply_temperature(policy, 0.9),
        ).strip()
    except Exception:
        return None
    if _is_useless_draft(text) or _looks_like_reasoning_leak(text):
        return None
    return text


def _greeting_reply_fallback(job: dict[str, Any], policy: dict[str, Any] | None = None) -> str:
    """无 LLM 时的最后兜底（仅此场景仍可用本地短句池）。"""
    custom = ((policy or {}).get("greet") or {}).get("reply")
    if isinstance(custom, str) and custom.strip() and not _is_useless_draft(custom.strip()):
        title = (job.get("title") or "").strip()
        return custom.strip().replace("{title}", title or "本岗位")

    title = (job.get("title") or "").strip()
    if not title or title in {"默认岗位", "该岗位", "本岗位"}:
        return random.choice(_GREETING_VARIANTS_PLAIN)
    return random.choice(_GREETING_VARIANTS_NAMED).format(title=title)


def _format_faq_reply(title: str, body: str) -> str:
    body = (body or "").strip()
    #  verbatim 也尽量短口语，少用「栏目名：」说明书腔
    if title == "薪资范围":
        return f"待遇的话 {body}"
    if title == "五险一金":
        return f"五险一金这边 {body}"
    if title == "上班时间":
        return f"时间是 {body}"
    if title == "工作内容":
        return f"主要做 {body}"
    if title == "是否有加班":
        return f"加班的话 {body}"
    if title == "休假":
        return f"休息的话 {body}"
    if title == "工作地点":
        return f"地点在 {body}"
    if title == "年龄要求限制":
        return f"年龄的话 {body}"
    if title == "岗位":
        return f"在招 {body}"
    if title.startswith("打招呼") or title in _GREETING_FAQ_PREFIXES:
        return body
    if any(title.startswith(p) for p in _CHAT_OFFTOPIC_PREFIXES):
        return body
    return f"{body}"


def _wants_send_resume(question: str) -> bool:
    q = question or ""
    return any(
        k in q
        for k in (
            "发送简历",
            "发简历",
            "发份简历",
            "发一下简历",
            "可否给您发送",
            "能否发简历",
            "简历发给您",
            "简历发您",
        )
    )


def _safe_fallback(
    question: str,
    faq: str,
    job: dict[str, Any] | None = None,
    *,
    dlg: Any = None,
) -> str:
    sections = parse_faq_sections(faq)
    hit = _reply_from_faq(question, sections)
    if hit:
        return hit["draft"]
    if dlg is not None:
        from src.dialog_v2 import progress_template

        return progress_template(dlg, job_title=str((job or {}).get("title") or ""), question=question)
    title = ((job or {}).get("title") or "本岗位").strip() or "本岗位"
    return f"您好，{title}还在招，您方便发一份简历或简单介绍下经历吗？"


def _llm_chat(
    prompt: str,
    *,
    purpose: str = "generate_reply",
    system: str | None = None,
    history: list[dict[str, Any]] | None = None,
    question: str | None = None,
    temperature: float | None = None,
) -> str:
    import time

    base = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    model = (os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "deepseek-chat").strip()
    max_tokens = _effective_max_tokens(model)
    if temperature is None:
        temperature = float(os.environ.get("LLM_TEMPERATURE") or 0.3)
    timeout = int(os.environ.get("LLM_TIMEOUT") or 60)
    if not base:
        raise RuntimeError("未配置 LLM_BASE_URL")
    endpoint = f"{base}/chat/completions"
    messages = _build_chat_messages(
        prompt, system=system, history=history, question=question or ""
    )
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    started = time.perf_counter()
    try:
        data = _post_chat(endpoint, key, payload, timeout=timeout)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        content, finish = _parse_assistant_content(data)
        # 推理模型把额度花光：自动加长重试一次
        if (not content) and finish == "length" and max_tokens < 2400:
            payload = {**payload, "max_tokens": 2400}
            started2 = time.perf_counter()
            data = _post_chat(endpoint, key, payload, timeout=timeout)
            elapsed_ms = int((time.perf_counter() - started2) * 1000)
            content, finish = _parse_assistant_content(data)
        if not content:
            reason = (
                "输出被截断且无正文（reasoning 耗尽 token，请把「最大输出长度」调到 ≥1600）"
                if finish == "length"
                else "无有效正文"
            )
            _log_llm(purpose, payload, endpoint, model, data, False, reason, elapsed_ms)
            raise RuntimeError(f"LLM {reason}")
        if _looks_like_reasoning_leak(content):
            _log_llm(purpose, payload, endpoint, model, data, False, "正文疑似思考泄漏", elapsed_ms)
            raise RuntimeError("LLM 正文疑似思考泄漏")
        _log_llm(purpose, payload, endpoint, model, data, True, None, elapsed_ms)
        return content
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raw = exc.read().decode("utf-8", errors="replace")[:400]
        err = f"LLM HTTP {exc.code}: {raw}"
        _log_llm(purpose, payload, endpoint, model, None, False, err, elapsed_ms)
        raise RuntimeError(err) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _log_llm(purpose, payload, endpoint, model, None, False, str(exc), elapsed_ms)
        raise


def _effective_max_tokens(model: str) -> int:
    """推理模型会把 max_tokens 先花在 reasoning_content 上；过小会导致 content 空且 finish=length。"""
    configured = int(os.environ.get("LLM_MAX_TOKENS") or 0)
    ml = (model or "").lower()
    is_reasoner = any(x in ml for x in ("flash", "reason", "r1", "think", "qwq"))
    if is_reasoner:
        # 用户设 300 时思考链写到一半就被截断，残片可能被误当回复
        return max(configured or 0, 1600)
    return configured or 400


def _build_chat_messages(
    prompt: str,
    *,
    system: str | None,
    history: list[dict[str, Any]] | None,
    question: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for role, text in _history_turns(history, question):
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": prompt})
    return messages


def _post_chat(
    endpoint: str, key: str, payload: dict[str, Any], *, timeout: int
) -> dict[str, Any]:
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_assistant_content(data: dict[str, Any]) -> tuple[str, str | None]:
    """解析助手正文；finish=length 且 content 空时绝不从截断的 reasoning 抠字。"""
    try:
        choice = data["choices"][0]
        msg = choice["message"]
        finish = choice.get("finish_reason")
        content = str(msg.get("content") or "").strip()
        if content and not _looks_like_reasoning_leak(content):
            return _strip_draft_label(content), finish
        if content and _looks_like_reasoning_leak(content):
            return "", finish
        reasoning = str(msg.get("reasoning_content") or "").strip()
        # 截断的思考链禁止兜底抽取（正是「候选人问：可以”单独，」这类残片来源）
        if finish == "length" or not reasoning:
            return "", finish
        extracted = _extract_reply_from_reasoning(reasoning)
        if extracted and not _looks_like_reasoning_leak(extracted):
            return _strip_draft_label(extracted), finish
        return "", finish
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"LLM 响应结构异常: {json.dumps(data, ensure_ascii=False)[:300]}"
        ) from exc


def _strip_draft_label(text: str) -> str:
    """去掉模型草稿标签，避免把「回复：」一并发给候选人。"""
    t = (text or "").strip().strip("「」" + '"' + "'")
    t = re.sub(
        r"^(?:最终回复|回复正文|最终输出|输出|答案|回复)\s*[：:]\s*",
        "",
        t,
    ).strip()
    return t.strip("「」" + '"' + "'").strip()


def _looks_like_reasoning_leak(text: str) -> bool:
    """识别误把模型思考过程当回复发出的情况。

    仅针对短回复正文；JSON/长结构化输出（如 FAQ 推荐）不应套用这些启发式，
    否则「需要」「回复」等日常用词会被误判成思考泄漏，进而报「无有效正文」。
    """
    t = (text or "").strip()
    if not t:
        return True
    # FAQ / 结构化：整段 JSON 或明显的 question/answer 数组
    if t.startswith(("[", "{")):
        return False
    if '"question"' in t and '"answer"' in t:
        return False
    # 长文不像候选人聊天回复，也不按短回复泄漏规则处理
    if len(t) > 280:
        return False
    if t.endswith(("，", "、", "：", ":", "”", '"')) and len(t) < 60:
        return True
    if "”单独" in t or "候选人问" in t:
        return True
    markers = (
        "候选人问",
        "需要结合",
        "需要理解",
        "需要回答",
        "意图标签",
        "事实口径",
        "当前判定",
        "综合回复",
        "只输出回复",
        "我们需要",
        "没有上下文",
        "字数 20",
        "20-120",
        "20～120",
    )
    if any(m in t for m in markers):
        return True
    if t.count("需要") >= 2 and ("回复" in t or "回答" in t):
        return True
    return False


def _extract_reply_from_reasoning(reasoning: str) -> str:
    """仅在 content 为空且思考完整结束时兜底；严格过滤，宁缺勿错。"""
    text = (reasoning or "").strip()
    if not text:
        return ""

    def _clean_cand(raw: str) -> str:
        cand = (raw or "").strip().strip("「」" + '"' + "'")
        # 去掉草稿标签：回复：/最终回复：/回复正文：
        cand = re.sub(
            r"^(?:最终回复|回复正文|最终输出|输出|答案|回复)\s*[：:]\s*",
            "",
            cand,
        ).strip()
        cand = cand.strip("「」" + '"' + "'").strip()
        return cand

    # 优先取文末标注的最终稿（模型常写多稿，取最后一次）
    labeled = list(
        re.finditer(
            r"(?:最终回复|回复正文|最终输出|输出|答案|回复)\s*[：:]\s*(.+?)(?=\n|$)",
            text,
            flags=re.M,
        )
    )
    for m in reversed(labeled):
        cand = _clean_cand(m.group(1))
        # 草稿旁白（—— / 这算两个问题）不算可发正文
        if "——" in cand or "这算" in cand or "还是两个" in cand:
            continue
        if 8 <= len(cand) <= 160 and not _looks_like_reasoning_leak(cand):
            return cand

    parts = text.split("\n\n")
    if len(parts) >= 2:
        tail = _clean_cand(parts[-1])
        if (
            12 <= len(tail) <= 160
            and not tail.startswith(("需要", "我们", "也许", "当前", "候选", "目标", "承接", "问题", "只问", "那就"))
            and "——" not in tail
            and not _looks_like_reasoning_leak(tail)
        ):
            return tail
    return ""


def _log_llm(
    purpose: str,
    payload: dict[str, Any],
    endpoint: str,
    model: str,
    response: Any,
    ok: bool,
    error: str | None,
    elapsed_ms: int,
) -> None:
    try:
        from admin.model_log import log_model_call

        log_model_call(
            kind="llm",
            purpose=purpose,
            request=payload,
            response=response,
            ok=ok,
            error=error,
            elapsed_ms=elapsed_ms,
            model=model,
            endpoint=endpoint,
        )
    except Exception:
        pass
