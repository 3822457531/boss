"""多义词 / 口语别名：归一化检测与 Jev 提示。"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PATH = _ROOT / "config" / "synonyms.yaml"

_PUNCT_RE = re.compile(r"[\s?？!！.。,，~～…]+")


def synonyms_path() -> Path:
    """可写配置路径：冻结包走 AppData；开发走仓库 config。"""
    try:
        from admin.paths import ROOT

        return ROOT / "config" / "synonyms.yaml"
    except Exception:
        return _DEFAULT_PATH


@lru_cache(maxsize=4)
def load_synonyms(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else synonyms_path()
    if not p.exists() or yaml is None:
        return {"concepts": {}}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {"concepts": {}}
    concepts = data.get("concepts") or {}
    return {"concepts": concepts if isinstance(concepts, dict) else {}}


def reload_synonyms() -> None:
    load_synonyms.cache_clear()


def save_synonyms(data: dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else synonyms_path()
    if yaml is None:
        raise RuntimeError("需要 PyYAML")
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    p.write_text(text, encoding="utf-8")
    reload_synonyms()


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", (text or "").strip()).lower()


def concept_terms(concept_id: str) -> list[str]:
    concepts = load_synonyms().get("concepts") or {}
    block = concepts.get(concept_id) or {}
    terms = list(block.get("terms") or [])
    return [str(t).strip() for t in terms if str(t).strip()]


def concept_canonical(concept_id: str) -> str:
    concepts = load_synonyms().get("concepts") or {}
    block = concepts.get(concept_id) or {}
    return str(block.get("canonical") or concept_id).strip()


def detect_concepts(text: str) -> list[str]:
    """返回命中的 concept id 列表（按配置顺序）。"""
    raw = text or ""
    norm = _norm(raw)
    if not norm and not raw.strip():
        return []
    hits: list[str] = []
    concepts = load_synonyms().get("concepts") or {}
    for cid, block in concepts.items():
        terms = [str(t).strip() for t in (block or {}).get("terms") or [] if str(t).strip()]
        # 长词优先，避免短「好」误伤（ack 用整句匹配）
        terms_sorted = sorted(terms, key=len, reverse=True)
        for term in terms_sorted:
            tl = term.lower()
            if cid == "ack_no_reply":
                if _is_pure_ack(raw, term):
                    hits.append(cid)
                    break
            else:
                if term in raw or tl in norm:
                    hits.append(cid)
                    break
    return hits


def _is_pure_ack(text: str, term: str) -> bool:
    """短确认：整句归一化后等于词，或仅含该词+语气。"""
    t = (text or "").strip()
    if not t:
        return False
    n = _norm(t)
    tn = _norm(term)
    if not tn:
        return False
    if n == tn:
        return True
    # 「好的～」「收到！」类
    if n.startswith(tn) and len(n) <= len(tn) + 2:
        return True
    return False


def is_pure_ack_message(text: str) -> bool:
    """是否为纯收悉类短句（可不必回复）。含额外实质内容则否。"""
    t = (text or "").strip()
    if not t:
        return False
    if "exchange_wechat" in detect_concepts(t):
        return False
    # 含问号或明显提问词 → 需要回
    if "?" in t or "？" in t:
        return False
    if re.search(r"(吗|嘛|呢|多少|怎么|什么|哪|能否|可以发|方便)", t):
        # 「可以」单独是 ack；「可以发吗」要回
        if len(_norm(t)) > 6:
            return False
    concepts = detect_concepts(t)
    if "ack_no_reply" not in concepts:
        return False
    # 归一化后很短，或整句几乎只有 ack 词
    n = _norm(t)
    if len(n) > 18:
        return False
    # 去掉所有 ack 词后几乎为空
    rest = t
    for term in sorted(concept_terms("ack_no_reply"), key=len, reverse=True):
        rest = re.sub(re.escape(term), "", rest, flags=re.I)
    rest_n = _norm(rest)
    return len(rest_n) <= 2


def looks_like_candidate_offers_wechat(text: str) -> bool:
    """对方主动留微信/邀请「有意可加」看作品——不是要 HR 点平台换微信。"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(
        re.search(
            r"(有意可加|可加我|加我微|加我v|我的微|微信号\s*[:：]|vx\s*[:：]|"
            r"微信看|加微信看|看视频号|视频号作品|作品.*微信|微信.*作品|"
            r"方便加我|加我看)",
            t,
            re.I,
        )
    )


def looks_like_wechat_request(text: str) -> bool:
    """候选人是否在要求互换微信（平台动作）。主动留联系方式不算。"""
    t = text or ""
    if looks_like_candidate_offers_wechat(t):
        return False
    return "exchange_wechat" in detect_concepts(t)


def synonym_hints_for_jev(text: str) -> dict[str, Any]:
    """写入 Jev state 的提示块。"""
    hits = detect_concepts(text)
    if looks_like_candidate_offers_wechat(text):
        hits = [h for h in hits if h != "exchange_wechat"]
    hints: list[str] = []
    for cid in hits:
        canon = concept_canonical(cid)
        terms = concept_terms(cid)[:12]
        hints.append(f"{cid}→{canon}（口语可含：{'/'.join(terms)}）")
    offer_note = ""
    if looks_like_candidate_offers_wechat(text):
        offer_note = "候选人在自我介绍里主动留微信/作品，不是要求 HR 发起换微信。"
    return {
        "normalized_concepts": hits,
        "synonym_hints": hints,
        "pure_ack": is_pure_ack_message(text),
        "candidate_offers_wechat": looks_like_candidate_offers_wechat(text),
        "note": offer_note,
    }


def wechat_terms() -> tuple[str, ...]:
    terms = concept_terms("exchange_wechat")
    return tuple(terms) if terms else (
        "换微信",
        "交换微信",
        "留个微信",
        "加个微信",
        "绿泡泡",
        "加个v",
        "加下v",
        "v信",
        "威信",
    )
