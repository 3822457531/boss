"""判断层：优先走 Jev System One；不可用时关键词降级。"""

from __future__ import annotations

import re
from typing import Any

from src.jev import (
    JevError,
    choice,
    get_choice,
    get_noul,
    get_score,
    jev_configured,
    noul,
    score,
    system_one,
)
from src.synonyms import (
    is_pure_ack_message,
    looks_like_wechat_request,
    synonym_hints_for_jev,
    wechat_terms,
)

_ACTION_CRITERIA: dict[str, str | None] = {
    "continue": "继续站内文字沟通即可，不必点平台按钮（含：问是否在招、自我介绍、常规问答）",
    "wechat": "应发起换微信（候选人要微信/联系方式，或需站外深聊）",
    "request_resume": "对方明确提到发简历/要简历，或沟通已充分需附件简历时，应点击求简历",
    "agree_resume": "对方已发来附件简历确认卡，应点同意",
    "invite_interview": "沟通充分，应发出面试邀请话术（时间/形式）",
    "ask_phone": "应礼貌索要手机号以便约面或联系",
}

_ACTION_LABELS = {
    "continue": "继续沟通",
    "wechat": "换微信",
    "request_resume": "求简历",
    "agree_resume": "同意收简历",
    "invite_interview": "邀请面试",
    "ask_phone": "换电话",
}

_INTENT_CRITERIA: dict[str, str | None] = {
    "salary": "询问薪资、待遇、年终奖、五险一金金额等报酬",
    "schedule": "询问加班、双休、工时、排班、弹性上班",
    "location": "询问工作地点、通勤、远程、出差",
    "interview": "约面试、面试形式/时间、流程节点",
    "resume": "简历、作品集、附件简历相关",
    "offer": "入职、到岗、试用期、offer 相关",
    "greeting": "打招呼、询问是否还在招人、简单寒暄",
    "not_interested": (
        "明确表示不感兴趣、不考虑、不想做、算了、不合适、暂不考虑、"
        "没兴趣、不招了吗以外的拒绝对方岗位/沟通（含婉拒继续聊）"
    ),
    "other": "其它或无法归类",
}

_INTENT_HINTS = (
    ("salary", ("薪", "工资", "年薪", "月薪", "待遇", "薪酬", "五险")),
    ("schedule", ("加班", "双休", "工作时间", "班次", "弹性")),
    ("location", ("地点", "地址", "通勤", "远程", "在家", "出差")),
    ("interview", ("面试", "约面", "线下", "线上", "时间方便")),
    ("resume", ("简历", "作品", "附件")),
    ("offer", ("offer", "入职", "到岗", "试用")),
    (
        "not_interested",
        (
            "不感兴趣",
            "没兴趣",
            "不考虑",
            "暂不考虑",
            "不想做",
            "不合适",
            "算了",
            "不用了",
            "先这样吧",
            "不聊了",
            "不想聊",
            "岗位不合适",
            "不太合适",
            "谢谢不了",
            "不用谢谢",
        ),
    ),
)

_GREETING_RE = re.compile(
    r"(你好|您好|在吗|在嘛|还在招|还招人?吗|招人吗|招人么|嗨|哈喽|hi|hello|早上好|下午好)",
    re.I,
)

# 含这些实质词的不算寒暄（避免「地址在哪里」因「在」误判）
_SUBSTANTIVE_RE = re.compile(
    r"(地址|地点|在哪|哪儿|薪|待遇|工资|五险|一金|社保|加班|微信|简历|面试|电话|手机|"
    r"休息|休假|调休|双休|上班|几点|内容|职责|年龄|要求)"
)


def is_simple_opener(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _SUBSTANTIVE_RE.search(t):
        return False
    if len(t) <= 20 and _GREETING_RE.search(t):
        return True
    # 极短纯寒暄
    if len(t) <= 10 and re.fullmatch(
        r"(在吗|在嘛|你好|您好|还招吗|还在招|还在招吗|招人吗|招人么|嗨|哈喽|hello|hi)[?？!！.。~\s]*",
        t,
        re.I,
    ):
        return True
    return False


class Judge:
    """Jev 判断；关闭或失败时降级规则。"""

    def __init__(self) -> None:
        self.enabled = jev_configured()

    def close(self) -> None:
        return None

    def evaluate_incoming(
        self,
        question: str,
        *,
        policy: dict[str, Any],
        job: dict[str, Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        batch: list[str] | None = None,
    ) -> dict[str, Any]:
        """对候选人消息打意图 / 是否需回复 / 转人工 / 红线 / 下一步动作。

        history: 近期会话 [{role, text}, ...]，用于短句歧义与不必回复判断。
        batch: 候选人一次连发的多条消息（按时间顺序）。≥2 条时按「整批」判断：
            意图/红线取自全批文本；纯收悉须整批都是收悉；寒暄快路径不再直接放行。
        """
        pending = [str(t).strip() for t in (batch or []) if str(t or "").strip()]
        multi = len(pending) >= 2
        # 整批文本：用于红线/敏感词/多义词这类「扫一遍」的判断
        scan_text = "\n".join(pending) if multi else question
        # 纯收悉：必须整批每条都是收悉，否则「好的」+「工资多少」会被误判成不必回复
        pure_ack = all(is_pure_ack_message(t) for t in pending) if multi else is_pure_ack_message(question)
        thresholds = policy.get("thresholds") or {}
        redlines = list(policy.get("redlines") or []) + list(policy.get("sensitive_words") or [])
        syn = synonym_hints_for_jev(scan_text)
        recent = _recent_messages_for_jev(history, limit=12)

        for word in policy.get("sensitive_words") or []:
            if word and word in scan_text:
                return _judgment_base(
                    intent="other",
                    intent_confidence=1.0,
                    handoff_prob=1.0,
                    redline_prob=1.0,
                    next_action="continue",
                    action_confidence=0.0,
                    handoff=True,
                    need_reply=True,
                    reason=f"敏感词：{word}",
                    engine="rule",
                    thresholds=thresholds,
                )

        # 纯收悉：我方刚说过实质内容 → 不必回复（省 Jev）
        if pure_ack and _recruiter_recently_spoke(history):
            return _judgment_base(
                intent="other",
                intent_confidence=0.85,
                handoff_prob=0.0,
                redline_prob=0.0,
                next_action="continue",
                action_confidence=0.9,
                handoff=False,
                need_reply=False,
                reason="纯确认收悉，不必回复",
                engine="rule_ack",
                thresholds=thresholds,
                extra={"normalized_concepts": syn.get("normalized_concepts") or []},
            )

        # 简单寒暄/还招人吗：直接放行。连发多条时不走快路径，交给 Jev/启发式整体判
        if is_simple_opener(question) and not multi:
            return _judgment_base(
                intent="greeting",
                intent_confidence=0.9,
                handoff_prob=0.05,
                redline_prob=0.0,
                next_action="continue",
                action_confidence=0.9,
                handoff=False,
                need_reply=True,
                reason="",
                engine="rule_opener",
                thresholds=thresholds,
            )

        # 口语要微信：规则快路径（整批里任意一条在要微信即命中）
        if looks_like_wechat_request(scan_text):
            return _judgment_base(
                intent="other",
                intent_confidence=0.88,
                handoff_prob=0.05,
                redline_prob=0.0,
                next_action="wechat",
                action_confidence=0.92,
                handoff=False,
                need_reply=True,
                reason="多义词命中交换微信",
                engine="rule_wechat",
                thresholds=thresholds,
                extra={"normalized_concepts": syn.get("normalized_concepts") or []},
            )

        if not self.enabled:
            intent = self._heuristic_intent(scan_text)
            next_action, action_conf = self._heuristic_action(scan_text)
            need_reply = not (pure_ack and _recruiter_recently_spoke(history))
            return _judgment_base(
                intent=intent,
                intent_confidence=0.5,
                handoff_prob=0.2,
                redline_prob=0.1 if any(r in scan_text for r in redlines if r) else 0.0,
                next_action=next_action,
                action_confidence=action_conf,
                handoff=False,
                need_reply=need_reply,
                reason="jev_disabled",
                engine="rule",
                thresholds=thresholds,
                extra={"ambiguous": _is_ambiguous_utterance(question) and not multi},
            )

        state: dict[str, Any] = {
            "candidate_message": scan_text,
            "redlines": redlines,
            "job_title": (job or {}).get("title") or "",
            "recent_messages": recent,
            "synonym_hints": syn.get("synonym_hints") or [],
            "normalized_concepts": syn.get("normalized_concepts") or [],
            "note": (
                "若 synonym_hints 含 exchange_wechat，口语如 vx/绿泡泡 均视为要换微信；"
                "若 candidate_offers_wechat / note 提示对方主动留微信看作品，不要选 wechat 动作，应 continue 文字沟通；"
                "若含 ack_no_reply 且上文我方已说明完毕、对方无新问题，则不必再回。"
                "短句歧义请结合 recent_messages 理解。"
                "candidate_message 可能一次含多行（候选人连发多条）："
                "intent / next_action 必须覆盖其中全部问题，不要只看最后一行；"
                "只要有一行是实质提问，need_reply 就应为 true。"
            ),
        }
        if multi:
            state["candidate_messages"] = pending
        try:
            data = system_one(
                state=state,
                questions={
                    "intent": choice(
                        "结合上下文，候选人最新一句最接近哪一类招聘咨询意图？"
                        "若明确婉拒/不感兴趣/不考虑本岗位，选 not_interested，不要归到 other。",
                        _INTENT_CRITERIA,
                    ),
                    "need_reply": noul(
                        "是否需要 HR 再发一条文字回复？"
                        "对方仅说「收到/好的/知道了/嗯」等收悉、无新问题且无需推进动作 → 否；"
                        "明确不感兴趣也通常需要一句礼貌收尾 → 是；"
                        "有提问、要微信/简历/约面、开场寒暄、自我介绍后待回应 → 是。",
                        criteria={
                            "true": "需要文字回复或推进",
                            "false": "不必回复（纯确认收悉）",
                        },
                    ),
                    "need_handoff": noul(
                        "是否必须转给真人 HR（投诉、辱骂、复杂薪资谈判、明确要求真人、法律风险）？"
                        "普通询问是否在招、打招呼、常规岗位问题应判为否。",
                        criteria={
                            "true": "必须真人介入",
                            "false": "可用标准话术自动回复",
                        },
                    ),
                    "redline_hit": noul(
                        "候选人是否在诱导 HR 做出红线承诺（保证入职、包过试用、不用面试等）？",
                        criteria={"true": "触及或诱导红线", "false": "未触及"},
                    ),
                    "next_action": choice(
                        "结合上下文，此刻 HR 最该执行哪一个下一步动作？"
                        "仅寒暄、问是否还在招、自我介绍想聊聊 → continue；"
                        "对方明确要求互换微信（换微信/留个微信/加个微信吗）→ wechat；"
                        "自我介绍末尾「有意可加微信看作品」是对方留联系方式，不是要求发起换微信 → continue；"
                        "对方明确要发简历 → request_resume；附件确认卡 → agree_resume；"
                        "约面 → invite_interview；要手机号 → ask_phone。",
                        _ACTION_CRITERIA,
                    ),
                },
                purpose="evaluate_incoming",
            )
            intent, conf = get_choice(data, "intent", "other")
            if intent not in _INTENT_CRITERIA:
                intent = "other"
            need_reply_prob = get_noul(data, "need_reply", 0.85)
            handoff_prob = get_noul(data, "need_handoff", 0.0)
            redline_prob = get_noul(data, "redline_hit", 0.0)
            next_action, action_conf = get_choice(data, "next_action", "continue")
            if next_action not in _ACTION_CRITERIA:
                next_action = "continue"
            # 自我介绍里「有意可加微信」≠ 要求发起换微信
            from src.synonyms import looks_like_candidate_offers_wechat

            if next_action == "wechat" and looks_like_candidate_offers_wechat(scan_text):
                next_action = "continue"
                action_conf = min(action_conf, 0.4)
                reasons_pre = ["对方主动留微信/作品，已取消换微信动作"]
            else:
                reasons_pre = []
            # 平台动作优先于「不必回复」
            need_reply = need_reply_prob >= 0.45
            if next_action != "continue":
                need_reply = True
            handoff = False
            reasons: list[str] = list(reasons_pre)
            if conf < float(thresholds.get("intent_confidence_min") or 0.55):
                intent = "other"
                reasons.append("意图置信度偏低已降为 other（仍自动回）")
            if handoff_prob > float(thresholds.get("handoff_prob_max") or 0.35):
                handoff = True
                reasons.append("需转人工概率过高")
            if redline_prob > float(thresholds.get("redline_hit_prob_max") or 0.2):
                handoff = True
                reasons.append("红线风险过高")
            if not need_reply:
                reasons.append("判定不必回复")
            ambiguous = (_is_ambiguous_utterance(question) and not multi) or (
                intent == "other" and conf < 0.55
            ) or (0.35 <= need_reply_prob <= 0.65)
            return _judgment_base(
                intent=intent,
                intent_confidence=conf,
                handoff_prob=handoff_prob,
                redline_prob=redline_prob,
                next_action=next_action,
                action_confidence=action_conf,
                handoff=handoff,
                need_reply=need_reply,
                reason="；".join(reasons),
                engine="jev",
                thresholds=thresholds,
                extra={
                    "raw_model": data.get("model"),
                    "need_reply_prob": need_reply_prob,
                    "ambiguous": ambiguous,
                    "normalized_concepts": syn.get("normalized_concepts") or [],
                },
            )
        except Exception as exc:
            intent = self._heuristic_intent(scan_text)
            next_action, action_conf = self._heuristic_action(scan_text)
            need_reply = not (pure_ack and _recruiter_recently_spoke(history))
            return _judgment_base(
                intent=intent,
                intent_confidence=0.4,
                handoff_prob=0.3,
                redline_prob=0.0,
                next_action=next_action,
                action_confidence=action_conf,
                handoff=False,
                need_reply=need_reply,
                reason=f"Jev 失败已降级规则：{exc}",
                engine="fallback",
                thresholds=thresholds,
                extra={"ambiguous": _is_ambiguous_utterance(question) and not multi},
            )

    def score_pair_quality(self, question: str, reply: str, policy: dict[str, Any]) -> float:
        if not self.enabled:
            return self._heuristic_quality(question, reply, policy)
        try:
            data = system_one(
                state={"question": question, "reply": reply, "redlines": policy.get("redlines") or []},
                questions={
                    "quality": score(
                        "这条 HR 回复作为可复用风格样本的质量（只看语气与专业度，不看事实是否过时）",
                        [
                            "0 禁用：含承诺/攻击性/明显不当",
                            "1 勉强可用",
                            "2 自然专业可复用",
                        ],
                    )
                },
                purpose="score_pair_quality",
            )
            s, _ = get_score(data, "quality", 1.0)
            if s > 2:
                s = min(2.0, s)
            return max(0.0, min(2.0, float(s)))
        except Exception:
            return self._heuristic_quality(question, reply, policy)

    def label_intent(self, question: str, job: dict[str, Any]) -> tuple[str, float]:
        result = self.evaluate_incoming(
            question,
            policy={"thresholds": {}, "redlines": [], "sensitive_words": []},
            job=job,
        )
        return str(result["intent"]), float(result["intent_confidence"])

    def score_draft_quality(self, question: str, draft: str, policy: dict[str, Any]) -> float:
        text = (draft or "").strip()
        if not text:
            return 0.0
        # 硬拦截：过短 / 占位词，不交给 Jev（避免「无」被误判为可通过）
        if _is_useless_draft(text):
            return 0.0
        for r in policy.get("redlines") or []:
            if r and r in text:
                return 0.0
        if is_simple_opener(question) and len(text) >= 6:
            return 0.85
        if not self.enabled:
            return 0.7 if len(text) >= 8 else 0.0
        try:
            data = system_one(
                state={"question": question, "draft": text, "redlines": policy.get("redlines") or []},
                questions={
                    "pass": noul(
                        "这份草稿是否可以发给候选人？"
                        "必须同时满足：①不是单字/占位（如「无」「好」）；②语气得体；"
                        "③基本回应了候选人（例如对方要发简历应表示可以发）；④无红线承诺。"
                        "任一不满足应判为否。",
                        criteria={
                            "true": "完整得体、可直接发送",
                            "false": "过短、答非所问、占位词或不应发送",
                        },
                    )
                },
                purpose="score_draft_quality",
            )
            return get_noul(data, "pass", 0.5)
        except Exception:
            return 0.65 if len(text) >= 8 else 0.0

    def _heuristic_quality(self, question: str, reply: str, policy: dict[str, Any]) -> float:
        red = list(policy.get("redlines") or [])
        if any(r and r in reply for r in red):
            return 0.0
        score_v = 1.5
        if len(reply) < 8:
            score_v -= 0.5
        if len(question) < 4:
            score_v -= 0.3
        return max(0.0, min(2.0, score_v))

    def _heuristic_intent(self, question: str) -> str:
        if is_simple_opener(question):
            return "greeting"
        q = question.lower()
        # 不感兴趣优先于其它关键词（避免「不考虑加班」误伤：需偏整句拒岗）
        if any(
            h in question
            for h in (
                "不感兴趣",
                "没兴趣",
                "不考虑这个",
                "不考虑贵司",
                "不考虑该",
                "不想做",
                "岗位不合适",
                "不太合适",
                "先不考虑",
                "暂不考虑",
                "算了不了",
                "不聊了",
                "不想聊",
            )
        ) or re.search(r"(算了|不用了|先这样)[吧哈呀啊~！!。\s]*$", (question or "").strip()):
            return "not_interested"
        for intent, hints in _INTENT_HINTS:
            if intent == "not_interested":
                continue  # 上面已专项处理，避免过宽
            if any(h.lower() in q for h in hints):
                return intent
        return "other"

    def _heuristic_action(self, question: str) -> tuple[str, float]:
        q = (question or "").strip().lower()
        if not q:
            return "continue", 0.5
        if any(k in q for k in ("同意接收", "同意查看", "附件简历", "发来一份附件", "简历确认", "请求您接收", "向您发送了附件")):
            return "agree_resume", 0.9
        if looks_like_wechat_request(question):
            return "wechat", 0.92
        if any(k.lower() in q for k in wechat_terms()) and "有意可加" not in question:
            return "wechat", 0.85
        if any(k in q for k in ("手机号", "电话", "打个电话", "留个号", "联系方式")) and "微信" not in q:
            return "ask_phone", 0.8
        if any(k in q for k in ("约面", "面试时间", "什么时候面试", "线下面试", "线上面试", "安排面试")):
            return "invite_interview", 0.8
        if any(k in q for k in ("发简历", "简历发您", "简历发你", "要简历", "附件简历吗", "没有简历", "发送简历", "可否给您发送")):
            return "request_resume", 0.78
        # 「还有空缺/介绍自己」等开场 → 先文字，不点求简历
        return "continue", 0.7


def action_label(action: str) -> str:
    return _ACTION_LABELS.get(action, action or "继续沟通")


def _recent_messages_for_jev(
    history: list[dict[str, Any]] | None, *, limit: int = 12
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in (history or [])[-limit:]:
        role = str(m.get("role") or "").strip()
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if role not in {"candidate", "recruiter", "system"}:
            role = "other"
        out.append({"role": role, "text": text[:240]})
    return out


def _recruiter_recently_spoke(history: list[dict[str, Any]] | None) -> bool:
    """最近几条里是否已有我方实质回复（用于纯 ack 跳过）。"""
    for m in reversed(history or []):
        role = m.get("role")
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if role == "candidate":
            # 跳过当前句之前的其它候选人句，继续往前找 recruiter
            continue
        if role == "recruiter" and len(text) >= 6:
            return True
        if role == "system":
            continue
    # 再扫一遍：任意 recruiter
    return any(
        m.get("role") == "recruiter" and len((m.get("text") or "").strip()) >= 6
        for m in (history or [])[-8:]
    )


def _is_ambiguous_utterance(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if len(t) <= 6:
        return True
    if re.fullmatch(r"(可以|好|行|嗯|哦|啊|嗯嗯|好的|收到)[?？!！.。~\s]*", t):
        return True
    return False


_USELESS_DRAFTS = frozenset(
    {
        "无",
        "有",
        "好",
        "哦",
        "嗯",
        "啊",
        "行",
        "可",
        "否",
        "好的",
        "好的，",
        "嗯嗯",
        "哈哈",
        "收到",
        "ok",
        "OK",
        "none",
        "N/A",
        "n/a",
    }
)


def _is_useless_draft(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if len(t) < 6:
        return True
    if t in _USELESS_DRAFTS:
        return True
    # 纯标点 / 只有语气词
    if re.fullmatch(r"[\W_]+", t, flags=re.UNICODE):
        return True
    return False


def _judgment_base(
    *,
    intent: str,
    intent_confidence: float,
    handoff_prob: float,
    redline_prob: float,
    next_action: str,
    action_confidence: float,
    handoff: bool,
    reason: str,
    engine: str,
    thresholds: dict[str, Any],
    need_reply: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if next_action not in _ACTION_CRITERIA:
        next_action = "continue"
    wechat_min = float(thresholds.get("wechat_prob_min") or 0.75)
    action_min = float(thresholds.get("action_confidence_min") or 0.7)
    suggest_wechat = (
        (not handoff)
        and next_action == "wechat"
        and action_confidence >= wechat_min
    )
    # 非 continue 动作需达到 action_confidence_min；微信另用 wechat_prob_min
    if next_action == "wechat":
        actionable = suggest_wechat
    elif next_action == "continue":
        actionable = False
    else:
        actionable = (not handoff) and action_confidence >= action_min
    if next_action != "continue":
        need_reply = True
    out: dict[str, Any] = {
        "intent": intent,
        "intent_confidence": intent_confidence,
        "handoff_prob": handoff_prob,
        "redline_prob": redline_prob,
        "next_action": next_action,
        "next_action_label": action_label(next_action),
        "action_confidence": action_confidence,
        "actionable": actionable,
        "wechat_prob": action_confidence if next_action == "wechat" else 0.0,
        "suggest_wechat": suggest_wechat,
        "handoff": handoff,
        "need_reply": bool(need_reply),
        "ambiguous": bool((extra or {}).get("ambiguous", False)),
        "reason": reason,
        "engine": engine,
    }
    if extra:
        out.update(extra)
    return out
