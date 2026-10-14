"""子进程调用 boss CLI，带超时、截断与日志。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from admin.paths import BOSS_LOG, ROOT
from admin.audit import audit
from admin.deps import ensure_chrome_env, find_bundled_boss, probe_boss_runtime

OUTPUT_LIMIT = 8000
# 同一 Boss 浏览器会话：打招呼 / 回消息 / list 等串行，避免抢页
BOSS_LOCK = threading.RLock()
# 粗略记录当前大致所在页：chat=沟通相关，away=职位/推荐等（用于少跳转策略）
_PAGE_HINT = "unknown"
_PAGE_HINT_LOCK = threading.Lock()

SIDE_EFFECT_ACTIONS = {
    "send",
    "greet",
    "not-fit",
    "wechat",
    "exchange-wechat",
    "agree-resume",
    "request-attachment-resume",
    "remark",
    "interview",
    "invite-interview",
}

_CHAT_CMDS = frozenset({"list", "chat", "send", "action"})
_AWAY_CMDS = frozenset(
    {"recommend", "positions", "greet", "search", "deep-search", "preview", "jd", "interaction"}
)


def get_page_hint() -> str:
    with _PAGE_HINT_LOCK:
        return _PAGE_HINT


def note_page_hint(hint: str) -> None:
    global _PAGE_HINT
    if hint not in {"chat", "away", "unknown"}:
        return
    with _PAGE_HINT_LOCK:
        _PAGE_HINT = hint


def _infer_page_hint(cmd: str) -> str | None:
    c = (cmd or "").lower()
    if c in _CHAT_CMDS:
        return "chat"
    if c in _AWAY_CMDS:
        return "away"
    return None



@lru_cache(maxsize=4)
def resolve_boss_argv(boss_bin: str | None = None) -> tuple[str, ...]:
    raw = (boss_bin or os.environ.get("BOSS_BIN") or "").strip()
    path = Path(raw) if raw else None

    def from_file(file_path: Path) -> tuple[str, ...]:
        suffix = file_path.suffix.lower()
        if suffix == ".js":
            bundled = find_bundled_boss()
            node = (bundled[0] if bundled else None) or Path(shutil.which("node") or "node")
            return (str(node), str(file_path))
        if suffix in {".cmd", ".bat"}:
            return ("cmd", "/c", str(file_path))
        return (str(file_path),)

    # 显式 BOSS_BIN
    if path is not None and path.is_file():
        return from_file(path.resolve())

    # 1) 安装包侧车 runtime（优先）
    bundled = find_bundled_boss()
    if bundled:
        node, cli = bundled
        ensure_chrome_env()
        return (str(node), str(cli))

    # 2) PATH 中的 boss
    for name in (raw or "boss", "boss", "boss.cmd", "boss.exe"):
        if not name:
            continue
        found = shutil.which(name)
        if found:
            ensure_chrome_env()
            return from_file(Path(found))

    # 3) 历史探测路径
    for prefix in (
        Path(r"D:\node\node_cache"),
        Path.home() / "AppData" / "Roaming" / "npm",
    ):
        cli_js = prefix / "node_modules" / "@joohw" / "boss-cli" / "dist" / "cli" / "index.js"
        if cli_js.is_file():
            ensure_chrome_env()
            return (shutil.which("node") or "node", str(cli_js))
        shim = prefix / "boss.cmd"
        if shim.is_file():
            ensure_chrome_env()
            return from_file(shim)

    info = probe_boss_runtime()
    raise FileNotFoundError(info.get("hint") or "找不到 boss-cli。请重新安装客户端（含 runtime）或设置 BOSS_BIN。")


def run_boss(
    args: list[str],
    *,
    timeout: float = 180.0,
    confirmed: bool = False,
    log: bool = True,
) -> dict[str, Any]:
    """执行 boss 子命令；副作用动作必须 confirmed=True。

    全局 BOSS_LOCK：同一浏览器会话上的 list/chat/send/recommend/greet 串行执行，
    避免打招呼与自动回复互相抢页。
    log=False 时不写运行日志 / 审计（用于静默探测）。
    """
    head = (args[0] if args else "").lower()
    if head in SIDE_EFFECT_ACTIONS or (head == "action" and len(args) > 1 and args[1] in SIDE_EFFECT_ACTIONS):
        if not confirmed:
            raise PermissionError("该操作会产生对外副作用，请在界面二次确认后再执行。")

    argv = [*resolve_boss_argv(), *args]
    with BOSS_LOCK:
        row = _run_boss_unlocked(argv, timeout=timeout, log=log)
        if row.get("exit_code") == 0 and not row.get("risk"):
            hint = _infer_page_hint(head)
            if hint:
                note_page_hint(hint)
        return row


def _run_boss_unlocked(
    argv: tuple[str, ...] | list[str],
    *,
    timeout: float,
    log: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=str(ROOT),
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        row = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "argv": _safe_argv(list(argv)),
            "exit_code": completed.returncode,
            "elapsed_ms": elapsed_ms,
            "stdout": stdout[:OUTPUT_LIMIT],
            "stderr": stderr[:OUTPUT_LIMIT],
            "truncated": len(stdout) > OUTPUT_LIMIT or len(stderr) > OUTPUT_LIMIT,
        }
        hit = _looks_like_risk(stdout + "\n" + stderr)
        if hit:
            row["risk"] = True
            row["risk_hit"] = hit
            row["risk_hint"] = (
                f"检测到验证码 / 登录失效 / 风控相关提示（命中「{hit}」），已停止自动流程建议。"
            )
        if log:
            _append_boss_log(row)
            audit("boss_run", {"argv": row["argv"], "exit_code": row["exit_code"], "elapsed_ms": elapsed_ms})
        return row
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        row = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "argv": _safe_argv(list(argv)),
            "exit_code": -1,
            "elapsed_ms": elapsed_ms,
            "stdout": ((exc.stdout or "") if isinstance(exc.stdout, str) else "")[:OUTPUT_LIMIT],
            "stderr": f"超时（>{timeout}s）",
            "truncated": False,
            "risk": False,
        }
        if log:
            _append_boss_log(row)
        raise TimeoutError(f"boss 命令超时（{timeout}s）") from exc


def leave_chat_index(*, prefer: str = "search") -> dict[str, Any]:
    """离开 /web/chat/index，避免长期停在会话页导致未读列表拉空。

    prefer:
      - interaction：互动 → 搜索（拉未读前）
      - search / search-only：优先搜索页（发完后离开，防秒回已读）
      - positions：CLI 职位页兜底优先
    """
    import time

    prefer = (prefer or "search").strip().lower()
    script_err = ""
    script_prefer = {
        "search": "search",
        "search-only": "search-only",
        "interaction": "interaction",
    }.get(prefer, "search")

    # 1) 同浏览器会话脚本
    script = ROOT / "tools" / "boss_leave_chat.mjs"
    if script.is_file():
        node = shutil.which("node") or "node"
        with BOSS_LOCK:
            try:
                completed = subprocess.run(
                    [node, str(script), f"--prefer={script_prefer}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    check=False,
                    cwd=str(ROOT),
                )
            except Exception as exc:
                script_err = str(exc)[:200]
                completed = None
            if completed is not None:
                payload: dict[str, Any] = {}
                for line in reversed((completed.stdout or "").strip().splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            payload = json.loads(line)
                            break
                        except json.JSONDecodeError:
                            continue
                if completed.returncode == 0 and payload.get("ok"):
                    via = str(payload.get("left_via") or script_prefer)
                    note_page_hint("away")
                    time.sleep(1.0)
                    return {
                        "exit_code": 0,
                        "stdout": (completed.stdout or "")[:800],
                        "stderr": (completed.stderr or "")[:400],
                        "left_via": via,
                        "url": payload.get("url"),
                        "argv": ["node", str(script.name), f"--prefer={script_prefer}"],
                    }
                script_err = str(
                    payload.get("error")
                    or (completed.stderr or completed.stdout or "leave script failed")
                )[:200]
    else:
        script_err = "boss_leave_chat.mjs missing"

    # 2) CLI 兜底
    if prefer in {"search", "search-only"}:
        order = ["search", "positions"]
    elif prefer == "positions":
        order = ["positions", "search"]
    else:
        order = ["search", "positions"]
    last: dict[str, Any] = {}
    for cmd in order:
        try:
            last = run_boss([cmd], timeout=120, confirmed=True)
        except Exception as exc:
            last = {"exit_code": -1, "stderr": str(exc), "argv": [cmd]}
            continue
        if last.get("risk"):
            return last
        if last.get("exit_code") == 0:
            last["left_via"] = cmd
            note_page_hint("away")
            time.sleep(1.0)
            return last
    if last:
        extra = f" | leave_script: {script_err}" if script_err else ""
        last["stderr"] = ((last.get("stderr") or "") + extra)[:400]
        return last
    return {
        "exit_code": -1,
        "stderr": f"leave_chat_index 失败（{script_err or 'unknown'}）",
    }


_PROBE_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_PROBE_TTL_SEC = 120.0
_PROBE_REFRESHING = False
_PROBE_REFRESH_LOCK = threading.Lock()


def _boss_cli_present() -> tuple[bool, str]:
    try:
        resolve_boss_argv()
        return True, "已检测到boss-ai引擎。登录状态请在浏览器会话中确认。"
    except Exception as exc:
        return False, str(exc)


def _store_probe(data: dict[str, Any]) -> dict[str, Any]:
    _PROBE_CACHE["ts"] = time.monotonic()
    _PROBE_CACHE["data"] = data
    return data


def _argv_is_version(argv: list[str] | tuple[str, ...] | None) -> bool:
    return any(str(p).strip().lower() == "version" for p in (argv or []))


def _boss_log_has_version() -> bool:
    for row in read_boss_logs(500):
        if _argv_is_version(row.get("argv")):
            return True
    return False


def _run_version_probe(*, timeout: float = 8.0) -> dict[str, Any]:
    try:
        # 激活后只记一次「查询版本」，后续探测静默不写日志
        should_log = not _boss_log_has_version()
        ver = run_boss(["version"], timeout=timeout, confirmed=True, log=should_log)
        ok = ver.get("exit_code") == 0
        return _store_probe(
            {
                "cli_ok": ok,
                "version_snippet": (ver.get("stdout") or "")[:200],
                "logged_in": None,
                "hint": "已检测到boss-ai引擎。登录状态请在浏览器会话中确认。",
                "raw": None,
                "probed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            }
        )
    except Exception as exc:
        present, hint = _boss_cli_present()
        return _store_probe(
            {
                "cli_ok": present,
                "version_snippet": "",
                "logged_in": None if present else False,
                "hint": hint if present else str(exc),
                "raw": None,
                "probed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            }
        )


def _schedule_probe_refresh() -> None:
    global _PROBE_REFRESHING
    with _PROBE_REFRESH_LOCK:
        if _PROBE_REFRESHING:
            return
        _PROBE_REFRESHING = True

    def _job() -> None:
        global _PROBE_REFRESHING
        try:
            _run_version_probe(timeout=8.0)
        finally:
            with _PROBE_REFRESH_LOCK:
                _PROBE_REFRESHING = False

    threading.Thread(target=_job, daemon=True, name="boss-probe-refresh").start()


def probe_boss_login(*, force: bool = False, wait: bool = False) -> dict[str, Any]:
    """探测 boss 是否可用。

    默认不阻塞：命中缓存立即返回；否则只做 PATH/侧车探测并后台跑 version。
    force/wait=True 时同步执行 version（给手动刷新用）。
    """
    from admin.deps import deps_status

    deps = deps_status()
    now = time.monotonic()
    cached = _PROBE_CACHE.get("data")
    fresh = cached is not None and (now - float(_PROBE_CACHE.get("ts") or 0)) < _PROBE_TTL_SEC
    if force or wait:
        row = _run_version_probe(timeout=12.0 if force else 8.0)
        row["deps"] = deps
        return row
    if fresh and cached is not None:
        return {**cached, "cached": True, "deps": deps}

    rt = deps.get("boss_cli") or {}
    chrome = deps.get("chrome") or {}
    present = bool(rt.get("ok"))
    hints = [rt.get("hint") or ""]
    if not chrome.get("installed"):
        hints.append(chrome.get("hint") or "未安装 Chrome")
    quick = {
        "cli_ok": present,
        "version_snippet": (cached or {}).get("version_snippet", "") if cached else "",
        "logged_in": None if present else False,
        "hint": "；".join(h for h in hints if h),
        "raw": None,
        "cached": bool(cached),
        "deps": deps,
        "source": rt.get("source") or "none",
    }
    if present:
        _schedule_probe_refresh()
    if cached is not None:
        return {**cached, "cached": True, "deps": deps, "source": rt.get("source")}
    return quick



def read_boss_logs(limit: int = 100) -> list[dict[str, Any]]:
    if not BOSS_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(BOSS_LOG.read_text(encoding="utf-8").splitlines()[-limit:]):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _append_boss_log(row: dict[str, Any]) -> None:
    BOSS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(BOSS_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe_argv(argv: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for i, part in enumerate(argv):
        if skip_next:
            skip_next = False
            out.append("***")
            continue
        low = part.lower()
        if any(k in low for k in ("api-key", "apikey", "secret", "token")):
            out.append(part)
            skip_next = True
            continue
        if re_looks_like_key(part):
            out.append("***")
        else:
            out.append(part)
    return out


def re_looks_like_key(s: str) -> bool:
    if len(s) < 16:
        return False
    return s.startswith(("sk-", "sk_", "ts_", "key-")) or (s.isalnum() and len(s) > 24)


def _looks_like_risk(text: str) -> str | None:
    """返回命中的风险短语；未命中返回 None。

    故意不用过宽词（如单独的「异常」「风控」）：recommend 的 stdout 含候选人优势文案，
    简历里「异常处理」「风险管控/风控模型」等会误触发并打断打招呼。
    """
    t = text.lower()
    # 按长度降序，优先匹配更具体的短语
    keys = (
        "请重新登录",
        "登录失效",
        "滑动验证",
        "安全验证",
        "人机验证",
        "触发风控",
        "账号异常",
        "访问异常",
        "操作频繁",
        "验证码",
        "captcha",
    )
    for k in keys:
        if k.lower() in t:
            return k
    return None
