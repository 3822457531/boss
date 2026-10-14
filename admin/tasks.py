"""后台异步任务（风格评估等）。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from admin.paths import TASKS_DIR
from admin.audit import audit


@dataclass
class TaskState:
    id: str
    kind: str
    status: str = "pending"  # pending|running|done|failed|cancelled|interrupted
    created_at: str = ""
    updated_at: str = ""
    total: int = 0
    done: int = 0
    message: str = ""
    error: str = ""
    result: Any = None
    failed_items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_LOCK = threading.Lock()
_TASKS: dict[str, TaskState] = {}
_CANCEL: set[str] = set()


def create_task(kind: str, total: int = 0, message: str = "") -> TaskState:
    tid = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(timespec="seconds")
    state = TaskState(id=tid, kind=kind, created_at=now, updated_at=now, total=total, message=message)
    with _LOCK:
        _TASKS[tid] = state
    _persist(state)
    return state


def get_task(tid: str) -> TaskState | None:
    with _LOCK:
        return _TASKS.get(tid)


def cancel_task(tid: str) -> bool:
    with _LOCK:
        if tid not in _TASKS:
            return False
        _CANCEL.add(tid)
        t = _TASKS[tid]
        if t.status in {"pending", "running"}:
            t.status = "cancelled"
            t.updated_at = datetime.now().isoformat(timespec="seconds")
            t.message = "用户取消"
            _persist(t)
        return True


def is_cancelled(tid: str) -> bool:
    with _LOCK:
        return tid in _CANCEL


def update_task(tid: str, **kwargs: Any) -> None:
    with _LOCK:
        t = _TASKS.get(tid)
        if not t:
            return
        for k, v in kwargs.items():
            setattr(t, k, v)
        t.updated_at = datetime.now().isoformat(timespec="seconds")
        _persist(t)


def run_in_background(tid: str, fn: Callable[[], None]) -> None:
    def wrapper() -> None:
        update_task(tid, status="running")
        try:
            fn()
            if is_cancelled(tid):
                update_task(tid, status="cancelled", message="已取消")
            else:
                st = get_task(tid)
                if st and st.status not in {"failed", "cancelled"}:
                    update_task(tid, status="done", message=st.message or "完成")
        except Exception as exc:
            update_task(tid, status="failed", error=str(exc)[:500])
            audit("task_failed", {"id": tid, "error": str(exc)[:200]})

    threading.Thread(target=wrapper, daemon=True).start()


def mark_interrupted_on_startup() -> None:
    """服务重启：磁盘上 running 任务标为 interrupted。"""
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    for path in TASKS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("status") in {"running", "pending"}:
            data["status"] = "interrupted"
            data["message"] = "服务重启，任务已中断"
            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tid = data.get("id")
            if tid:
                with _LOCK:
                    _TASKS[tid] = TaskState(**{k: data[k] for k in TaskState.__dataclass_fields__ if k in data})


def _persist(state: TaskState) -> None:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = TASKS_DIR / f"{state.id}.json"
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
