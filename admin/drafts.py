"""草稿队列读写。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from admin.paths import DRAFTS_PATH


def append_draft(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row.setdefault("id", uuid.uuid4().hex[:12])
    row.setdefault("ts", datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
    row.setdefault("status", "pending")
    DRAFTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DRAFTS_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def list_drafts(status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if not DRAFTS_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in DRAFTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if status and obj.get("status") != status:
            continue
        rows.append(obj)
    rows.reverse()
    return rows[:limit]


def update_draft(draft_id: str, **fields: Any) -> dict[str, Any] | None:
    if not DRAFTS_PATH.exists():
        return None
    lines = DRAFTS_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found: dict[str, Any] | None = None
    for line in lines:
        if not line.strip().startswith("{"):
            out.append(line)
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if obj.get("id") == draft_id:
            obj.update(fields)
            obj["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            found = obj
            out.append(json.dumps(obj, ensure_ascii=False))
        else:
            out.append(line)
    if found is None:
        return None
    DRAFTS_PATH.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return found


def adoption_stats() -> dict[str, Any]:
    rows = list_drafts(limit=5000)
    total = len([r for r in rows if r.get("status") in {"sent", "discarded", "pending", "handoff", "edited_sent"}])
    sent = len([r for r in rows if r.get("status") in {"sent", "edited_sent"}])
    pending = len([r for r in rows if r.get("status") == "pending"])
    handoff = len([r for r in rows if r.get("status") == "handoff"])
    return {
        "total": total,
        "sent": sent,
        "pending": pending,
        "handoff": handoff,
        "adopt_rate": round(sent / total, 3) if total else None,
    }
