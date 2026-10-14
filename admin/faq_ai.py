"""用总控下发的 LLM 为指定职位生成专属 FAQ 推荐（仅预览，不直接写入）。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from admin.config_io import load_env_into_os, read_env_map
from admin.faq_io import load_faq
from admin.paths import FAQ_PATH


def llm_ready() -> tuple[bool, str]:
    load_env_into_os()
    env = read_env_map()
    enabled = (env.get("LLM_ENABLED") or "true").strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        return False, "大模型未启用（请在总控台开启并下发）"
    base = (env.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").strip()
    key = (env.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    model = (env.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "").strip()
    if not base or not key or not model:
        return False, "尚未下发大模型配置（请在总控台配置后重新激活/等待心跳）"
    return True, f"{env.get('LLM_PROVIDER') or 'llm'} · {model}"


def suggest_position_faq(
    *,
    title: str,
    aliases: list[str] | None = None,
    existing_pos: list[dict[str, str]] | None = None,
    count: int = 8,
) -> dict[str, Any]:
    """调用 LLM 生成职位专属 FAQ 推荐条目。"""
    ok, tip = llm_ready()
    if not ok:
        raise RuntimeError(tip)

    title = (title or "").strip() or "未命名职位"
    aliases = [a.strip() for a in (aliases or []) if str(a).strip()]
    existing_pos = existing_pos or []
    common = load_faq(FAQ_PATH)[:40]
    n = max(4, min(12, int(count or 8)))

    common_hint = "\n".join(
        f"- {it.get('question')}" for it in common if it.get("question")
    ) or "（暂无）"
    exist_hint = "\n".join(
        f"- {it.get('question')}" for it in existing_pos if it.get("question")
    ) or "（暂无）"
    alias_txt = "、".join(aliases) if aliases else "无"

    system = (
        "你是资深招聘 HR 助手。请为指定岗位生成「职位专属 FAQ」口径，"
        "用于自动回复候选人。只输出 JSON，不要 Markdown 围栏，不要解释。"
    )
    prompt = f"""请为招聘岗位生成 {n} 条专属 FAQ。

岗位名称：{title}
别名：{alias_txt}

已有公共 FAQ 标题（公司通用，请勿重复，专属 FAQ 应突出本岗位差异）：
{common_hint}

本岗位已有专属 FAQ 标题（可补充空白点，勿机械复述）：
{exist_hint}

要求：
1. 问题用短标题（如：薪资范围、加班情况、任职要求、工作地点、晋升空间）
2. 答案 2～5 句，口语、可直接发给候选人；不确定的用「面议/入职沟通」等稳妥说法，勿编造离谱承诺
3. 内容必须紧扣「{title}」，不要写成其他岗位
4. 严格输出 JSON 数组，每项仅含 question、answer 两个字符串字段，例如：
[{{"question":"薪资范围","answer":"…"}}]
"""

    from src.reply import _llm_chat

    raw = _llm_chat(
        prompt,
        purpose="position_faq_suggest",
        system=system,
        temperature=0.4,
    )
    items = _parse_faq_json(raw)
    if not items:
        raise RuntimeError("模型未返回可用 FAQ，请稍后重试")
    return {
        "ok": True,
        "model_tip": tip,
        "items": items[:n],
        "raw_preview": (raw or "")[:500],
    }


def suggest_common_faq(
    *,
    industry: str,
    existing: list[dict[str, str]] | None = None,
    count: int = 10,
) -> dict[str, Any]:
    """按行业生成公共 FAQ 推荐（公司通用口径，预览不写入）。"""
    ok, tip = llm_ready()
    if not ok:
        raise RuntimeError(tip)

    industry = (industry or "").strip()
    if not industry:
        raise RuntimeError("请填写行业，例如：餐饮、教培、物流、互联网")
    existing = existing or []
    n = max(6, min(16, int(count or 10)))
    exist_hint = "\n".join(
        f"- {it.get('question')}" for it in existing if it.get("question")
    ) or "（暂无）"

    system = (
        "你是资深招聘 HR 助手。请为指定行业生成「公司公共 FAQ」口径，"
        "适用于该行业多家岗位共用。只输出 JSON，不要 Markdown 围栏，不要解释。"
    )
    prompt = f"""请为「{industry}」行业生成 {n} 条公司公共 FAQ。

行业：{industry}

已有公共 FAQ 标题（请补充空白点，勿机械重复）：
{exist_hint}

要求：
1. 覆盖候选人常问点：薪资福利、上班时间/休息、社保、试用期、工作地点/通勤、加班、餐补/宿舍（按行业取舍）、入职材料、面试流程、是否还招人等
2. 问题用短标题；答案 2～5 句，口语、可直接发给候选人；不确定处用「以门店/岗位具体安排为准」「面议」等稳妥说法，勿编造离谱承诺
3. 口径要贴合「{industry}」行业习惯，但保持公司通用（不要写成某一个具体岗位专属）
4. 严格输出 JSON 数组，每项仅含 question、answer 两个字符串字段
"""

    from src.reply import _llm_chat

    raw = _llm_chat(
        prompt,
        purpose="common_faq_suggest",
        system=system,
        temperature=0.4,
    )
    items = _parse_faq_json(raw)
    if not items:
        raise RuntimeError("模型未返回可用 FAQ，请稍后重试")
    return {
        "ok": True,
        "industry": industry,
        "model_tip": tip,
        "items": items[:n],
        "raw_preview": (raw or "")[:500],
    }


def _parse_faq_json(text: str) -> list[dict[str, str]]:
    raw = (text or "").strip()
    if not raw:
        return []
    # 去掉 ```json 围栏
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    # 截取最外层数组
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 宽松：逐行 question/answer
        return _parse_faq_loose(text)
    if isinstance(data, dict):
        data = data.get("items") or data.get("faq") or data.get("data") or []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        q = str(row.get("question") or row.get("q") or row.get("title") or "").strip()
        a = str(row.get("answer") or row.get("a") or row.get("body") or "").strip()
        if q and a:
            out.append({"question": q[:80], "answer": a[:1200]})
    return out


def _parse_faq_loose(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    q: str | None = None
    buf: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(?:#{1,3}\s*|Q[:：]\s*|\d+[\.\)、]\s*)(.+)$", s, re.I)
        if m and (s.startswith("#") or s.lower().startswith("q") or re.match(r"^\d+", s)):
            if q and buf:
                items.append({"question": q, "answer": "\n".join(buf).strip()})
            q = m.group(1).strip()[:80]
            buf = []
            continue
        if q:
            if re.match(r"^A[:：]\s*", s, re.I):
                s = re.sub(r"^A[:：]\s*", "", s, flags=re.I)
            buf.append(s)
    if q and buf:
        items.append({"question": q, "answer": "\n".join(buf).strip()})
    return [it for it in items if it["question"] and it["answer"]]
