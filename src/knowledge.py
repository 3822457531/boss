"""从规整后的 {role, text} 序列挖问答配对。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


MIN_QUESTION_CHARS = 4
MIN_REPLY_CHARS = 6

_DEFAULT_RED_LINES = ("保证入职", "包过试用期", "一定能转正")


@dataclass
class QAPair:
    """一条「候选人问 → HR 答」风格样本。"""

    question: str
    reply: str
    source: str = ""
    date: str = ""
    intent: str = "other"
    quality: float = 2.0
    meta: dict[str, Any] = field(default_factory=dict)


def _contains_red_line(text: str, red_lines: Iterable[str]) -> bool:
    return any(line and line in text for line in red_lines)


def extract_pairs(
    messages: list[dict[str, str]],
    *,
    source: str = "",
    date: str = "",
    min_question_chars: int = MIN_QUESTION_CHARS,
    min_reply_chars: int = MIN_REPLY_CHARS,
    red_lines: Iterable[str] = _DEFAULT_RED_LINES,
) -> list[QAPair]:
    """相邻 candidate → recruiter 挖配对；过滤过短与红线回复。"""
    pairs: list[QAPair] = []
    pending_q: str | None = None
    for msg in messages:
        role = msg.get("role", "")
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        if role == "candidate":
            pending_q = text
            continue
        if role == "recruiter" and pending_q is not None:
            if (
                len(pending_q) >= min_question_chars
                and len(text) >= min_reply_chars
                and not _contains_red_line(text, red_lines)
            ):
                pairs.append(
                    QAPair(question=pending_q, reply=text, source=source, date=date)
                )
            pending_q = None
        elif role == "system":
            # 系统条打断配对，避免跨系统消息错配
            pending_q = None
    return pairs


def dedupe(pairs: list[QAPair]) -> list[QAPair]:
    """按 question+reply 归一化去重，保留首次出现。"""
    seen: set[str] = set()
    out: list[QAPair] = []
    for pair in pairs:
        key = re.sub(r"\s+", " ", f"{pair.question}\n{pair.reply}").strip().lower()
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out.append(pair)
    return out


def save_pairs(pairs: list[QAPair], path: str | Path) -> int:
    """写入 JSONL，返回条数。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        for pair in pairs:
            handle.write(json.dumps(asdict(pair), ensure_ascii=False) + "\n")
    return len(pairs)
