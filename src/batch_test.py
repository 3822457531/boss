"""批量话术测试：每条独立走真实 Jev + 生成（不调 boss-cli）。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from admin.config_io import load_env_into_os, read_policy
from admin.paths import ROOT
from src.judge import Judge, action_label
from src.policy import get_job
from src.sim_chat import _process_turn

DEFAULT_PHRASES_PATH = ROOT / "config" / "batch_test_phrases.txt"
RESULTS_DIR = ROOT / "data" / "batch_test"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

LogFn = Callable[[str], None]


def load_phrases_text(raw: str) -> list[str]:
    """从文本解析话术：每行一条，空行与 # 注释忽略。"""
    out: list[str] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def load_phrases_file(path: Path | None = None) -> list[str]:
    p = path or DEFAULT_PHRASES_PATH
    if not p.exists():
        return []
    return load_phrases_text(p.read_text(encoding="utf-8"))


def phrases_to_text(phrases: list[str]) -> str:
    return "\n".join(phrases) + ("\n" if phrases else "")


def run_one(
    question: str,
    *,
    policy: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
    judge: Judge | None = None,
    job_title: str | None = None,
) -> dict[str, Any]:
    """单条独立测试：无历史上下文，force_reply=True。"""
    load_env_into_os()
    policy = policy or read_policy()
    job = job or get_job(policy, None)
    title = (job_title or "").strip() or (job.get("title") or "默认岗位")
    job = {**job, "title": title}
    own_judge = judge is None
    if own_judge:
        judge = Judge()
    assert judge is not None
    q = (question or "").strip()
    started = time.perf_counter()
    try:
        prior = [
            {
                "id": uuid.uuid4().hex[:10],
                "role": "system",
                "text": f"沟通的职位-{title}",
                "ts": datetime.now().strftime("%H:%M:%S"),
            },
            {
                "id": uuid.uuid4().hex[:10],
                "role": "candidate",
                "text": q,
                "ts": datetime.now().strftime("%H:%M:%S"),
            },
        ]
        result = _process_turn(
            q,
            policy=policy,
            job=job,
            force_reply=True,
            judge=judge,
            prior_messages=prior,
            candidate_name="批量测试",
            chat_position=title or None,
        )
        judgment = result.get("judgment") or {}
        draft = (result.get("draft") or "").strip()
        # 从 new_messages 里取最后一条 HR 正文（有些路径 draft 为空但有气泡）
        if not draft:
            for m in reversed(result.get("new_messages") or []):
                if m.get("role") == "recruiter" and (m.get("text") or "").strip():
                    draft = (m.get("text") or "").strip()
                    break
        notes = [
            (m.get("text") or "").strip()
            for m in (result.get("new_messages") or [])
            if m.get("role") == "system" and (m.get("text") or "").strip()
        ]
        return {
            "question": q,
            "answer": draft,
            "outcome": result.get("outcome"),
            "reason": result.get("reason") or "",
            "intent": judgment.get("intent"),
            "next_action": judgment.get("next_action"),
            "action": result.get("action"),
            "action_label": action_label(str(result.get("action") or judgment.get("next_action") or "")),
            "handoff": bool(judgment.get("handoff")),
            "notes": notes,
            "elapsed_ms": result.get("elapsed_ms")
            or int((time.perf_counter() - started) * 1000),
            "error": None,
        }
    except Exception as exc:
        return {
            "question": q,
            "answer": "",
            "outcome": "error",
            "reason": str(exc)[:300],
            "intent": None,
            "next_action": None,
            "action": None,
            "action_label": "",
            "handoff": False,
            "notes": [],
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc)[:300],
        }
    finally:
        if own_judge:
            judge.close()


class BatchTestRunner:
    """后台批量跑话术，可供管理台轮询。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._phrases: list[str] = load_phrases_file()
        self._results: list[dict[str, Any]] = []
        self._logs: list[str] = []
        self.status: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "done": 0,
            "ok": 0,
            "handoff": 0,
            "skip": 0,
            "error": 0,
            "last_message": "未开始",
            "job_title": "",
        }

    def log(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        with self._lock:
            self._logs.append(line)
            self._logs = self._logs[-300:]
            self.status["last_message"] = msg

    def logs(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def get_phrases(self) -> list[str]:
        with self._lock:
            return list(self._phrases)

    def set_phrases(self, phrases: list[str] | str, *, persist: bool = False) -> list[str]:
        if isinstance(phrases, str):
            items = load_phrases_text(phrases)
        else:
            items = [str(p).strip() for p in phrases if str(p).strip()]
        with self._lock:
            if self.status.get("running"):
                raise RuntimeError("测试进行中，无法改话术")
            self._phrases = items
            if persist:
                header = (
                    "# 批量测试话术（每行一条；# 开头为注释）\n"
                    f"# 共 {len(items)} 条（管理台保存）。\n\n"
                )
                DEFAULT_PHRASES_PATH.parent.mkdir(parents=True, exist_ok=True)
                DEFAULT_PHRASES_PATH.write_text(
                    header + phrases_to_text(items), encoding="utf-8"
                )
            return list(items)

    def load_default(self) -> list[str]:
        return self.set_phrases(load_phrases_file())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": dict(self.status),
                "phrases_count": len(self._phrases),
                "results": list(self._results),
                "logs": list(self._logs[-80:]),
            }

    def start(self, *, job_title: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("批量测试已在运行")
            if not self._phrases:
                raise RuntimeError("话术为空，请先导入")
            self._stop.clear()
            self._results = []
            title = (job_title or "").strip()
            self.status.update(
                {
                    "running": True,
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "finished_at": None,
                    "total": len(self._phrases),
                    "done": 0,
                    "ok": 0,
                    "handoff": 0,
                    "skip": 0,
                    "error": 0,
                    "last_message": "启动中…",
                    "job_title": title,
                }
            )
            phrases = list(self._phrases)
        self._thread = threading.Thread(
            target=self._run,
            args=(phrases, title),
            name="batch-test",
            daemon=True,
        )
        self._thread.start()
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self.log("收到停止请求…")
        return self.snapshot()

    def _run(self, phrases: list[str], job_title: str) -> None:
        load_env_into_os()
        policy = read_policy()
        job = get_job(policy, None)
        judge = Judge()
        self.log(f"开始批量测试，共 {len(phrases)} 条")
        try:
            for i, q in enumerate(phrases, start=1):
                if self._stop.is_set():
                    self.log(f"已停止（完成 {i - 1}/{len(phrases)}）")
                    break
                self.log(f"[{i}/{len(phrases)}] {q[:40]}")
                row = run_one(q, policy=policy, job=job, judge=judge, job_title=job_title or None)
                row["index"] = i
                with self._lock:
                    self._results.append(row)
                    self.status["done"] = i
                    oc = str(row.get("outcome") or "")
                    if row.get("error") or oc == "error":
                        self.status["error"] = int(self.status.get("error") or 0) + 1
                    elif row.get("handoff") or oc == "handoff":
                        self.status["handoff"] = int(self.status.get("handoff") or 0) + 1
                    elif oc in ("skip",):
                        self.status["skip"] = int(self.status.get("skip") or 0) + 1
                    else:
                        self.status["ok"] = int(self.status.get("ok") or 0) + 1
                ans = (row.get("answer") or "")[:36]
                self.log(
                    f"  → {row.get('outcome')} | {row.get('intent') or '-'} | "
                    f"{ans or '(无正文)'}"
                )
        finally:
            judge.close()
            with self._lock:
                self.status["running"] = False
                self.status["finished_at"] = datetime.now().isoformat(timespec="seconds")
            path = self.save_results()
            self.log(f"结束，结果已写入 {path.name}")

    def save_results(self) -> Path:
        with self._lock:
            payload = {
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "status": dict(self.status),
                "results": list(self._results),
            }
        name = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = RESULTS_DIR / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        latest = RESULTS_DIR / "latest.json"
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return path


RUNNER = BatchTestRunner()
