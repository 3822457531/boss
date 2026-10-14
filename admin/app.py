"""本地管理服务：仅绑定 127.0.0.1。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from admin.audit import audit, read_audit
from admin.boss_runner import probe_boss_login, read_boss_logs, run_boss
from admin.deps import deps_status, open_chrome_download, probe_chrome
from admin.config_io import (
    apply_jev_save,
    apply_llm_save,
    clear_jev_key,
    clear_llm_key,
    ensure_daily_stats,
    jev_public_view,
    llm_public_view,
    load_env_into_os,
    read_env_map,
    read_policy,
    write_policy,
)
from admin.faq_io import load_faq, preview_from_paste, preview_from_upload, save_faq
from admin import license_client as lic
from admin.model_test import test_jev_connection, test_llm_connection
from admin.paths import FAQ_PATH, ROOT, BUNDLE_ROOT
from admin import drafts as drafts_io
from admin.tasks import cancel_task, get_task, mark_interrupted_on_startup
from admin.version import CLIENT_VERSION
from src.auto_loop import LOOP


def _static_dir() -> Path:
    # 冻结：_MEIPASS/admin/static；开发：admin/static
    candidates = [
        Path(__file__).resolve().parent / "static",
        BUNDLE_ROOT / "admin" / "static",
        BUNDLE_ROOT / "static",
    ]
    for p in candidates:
        if (p / "index.html").exists():
            return p
    return candidates[0]


STATIC_DIR = _static_dir()

app = FastAPI(title="直聊小蜜", docs_url=None, redoc_url=None)

_LICENSE_PUBLIC = {
    "/api/license/status",
    "/api/license/activate",
    "/api/license/deactivate",
    "/api/license/check-update",
    "/api/license/apply-update",
    "/api/deps",
}


def _license_bypass() -> bool:
    return (os.environ.get("LICENSE_BYPASS") or "").strip().lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def license_gate(request: Request, call_next):
    path = request.url.path or ""
    if (
        path.startswith("/static")
        or path in {"/", "/favicon.ico"}
        or path in _LICENSE_PUBLIC
        or path.startswith("/api/deps/")
        or _license_bypass()
    ):
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)
    if not lic.is_activated():
        return JSONResponse({"detail": "未激活", "code": "license_required"}, status_code=403)
    if lic.license_locked():
        return JSONResponse({"detail": "卡密失效或心跳失败，请重新激活", "code": "license_locked"}, status_code=403)
    return await call_next(request)


def _schedule_boss_login(*, reason: str, delay_sec: float = 1.2) -> None:
    """后台拉起 Boss 扫码登录页；不阻塞请求/启动。"""

    def _run() -> None:
        if delay_sec > 0:
            time.sleep(delay_sec)
        try:
            result = run_boss(["login"], timeout=60.0, confirmed=True)
            audit(
                "boss_open_login_auto",
                {
                    "reason": reason,
                    "exit_code": result.get("exit_code"),
                    "ok": int(result.get("exit_code") or 0) == 0,
                },
            )
        except Exception as exc:
            audit("boss_open_login_auto_fail", {"reason": reason, "error": str(exc)})

    threading.Thread(target=_run, daemon=True, name=f"boss-login-{reason}").start()


@app.on_event("startup")
def _startup() -> None:
    load_env_into_os()
    mark_interrupted_on_startup()
    audit("admin_start", {"bind": "127.0.0.1", "version": CLIENT_VERSION})
    if lic.is_activated() and not _license_bypass():
        try:
            lic.heartbeat()
        except Exception as exc:
            audit("license_heartbeat_fail", {"error": str(exc)})
            lic.set_locked(True)
        lic.start_heartbeat_loop()
        # 已激活：有 Chrome 时再自动打开 Boss 扫码登录
        if not lic.license_locked() and probe_chrome().get("installed"):
            _schedule_boss_login(reason="startup", delay_sec=1.5)


# ---------- models ----------


class LlmSave(BaseModel):
    enabled: bool = False
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    timeout: int = 30
    max_tokens: int = 1600
    temperature: float = 0.3
    api_key: str | None = None


class JevSave(BaseModel):
    enabled: bool = False
    provider: str = "typesafe"
    base_url: str = ""
    model: str = "jev-latest"
    api_key: str | None = None


class FaqSave(BaseModel):
    items: list[dict[str, str]]
    path: str | None = None


class FaqPaste(BaseModel):
    text: str


class FaqAiSuggest(BaseModel):
    industry: str = ""
    count: int = 10


class PositionPatch(BaseModel):
    aliases: list[str] | None = None
    enabled: bool | None = None
    faq_path: str | None = None
    title: str | None = None


class SynonymsBody(BaseModel):
    concepts: dict[str, Any] = Field(default_factory=dict)


class BossRun(BaseModel):
    args: list[str]
    confirmed: bool = False
    confirm_text: str | None = None
    timeout: float = 180.0


class PolicyBody(BaseModel):
    policy: dict[str, Any]


class DraftAction(BaseModel):
    id: str
    draft: str | None = None
    confirmed: bool = False


# ---------- overview ----------


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    load_env_into_os()
    env = read_env_map()
    policy = read_policy()
    faq = load_faq()
    if lic.is_activated():
        remote = lic.remote_model_view()
        rv_llm = remote.get("llm") or {}
        rv_jev = remote.get("jev") or {}
        llm = {
            "enabled": bool(rv_llm.get("enabled", True)),
            "provider": rv_llm.get("provider") or "",
            "model": rv_llm.get("model") or "",
            "api_key": {
                "configured": bool(rv_llm.get("api_key_configured")),
                "mask": "****" if rv_llm.get("api_key_configured") else "",
            },
            "source": "server",
        }
        jev = {
            "enabled": bool(rv_jev.get("enabled", True)),
            "provider": rv_jev.get("provider") or "",
            "model": rv_jev.get("model") or "",
            "api_key": {
                "configured": bool(rv_jev.get("api_key_configured")),
                "mask": "****" if rv_jev.get("api_key_configured") else "",
            },
            "source": "server",
        }
    else:
        llm = {**llm_public_view(env), "source": "local"}
        jev = {**jev_public_view(env), "source": "local"}
    boss = probe_boss_login()
    chrome = probe_chrome()
    deps = deps_status()
    stats, rolled = ensure_daily_stats(policy)
    if rolled:
        write_policy(policy)
    auto = LOOP.snapshot()
    pending_drafts = len(drafts_io.list_drafts(status="pending", limit=500))
    issues: list[dict[str, str]] = []
    if not chrome.get("installed"):
        issues.append({
            "level": "error",
            "text": "未安装 Google Chrome（自动化依赖 Chrome 打开招聘网页）",
            "goto": "overview",
            "action": "chrome_download",
        })
    if not boss.get("cli_ok"):
        issues.append({
            "level": "error",
            "text": boss.get("hint") or "程序组件缺失，请重新安装直聊小蜜",
            "goto": "logs",
        })
    if not llm["api_key"]["configured"]:
        issues.append({
            "level": "warn",
            "text": "AI 回复服务还没准备好，请联系管理员",
            "goto": "overview",
        })
    if not jev["api_key"]["configured"]:
        issues.append({
            "level": "warn",
            "text": "消息判断服务还没准备好，请联系管理员",
            "goto": "overview",
        })
    if len(faq) == 0:
        issues.append({"level": "warn", "text": "还没配置常用问答，去「职位与知识库」添加", "goto": "positions"})
    pos_stats = {}
    try:
        from src.policy import position_stats

        pos_stats = position_stats(policy)
    except Exception:
        pos_stats = {}
    if pending_drafts > 0:
        pass  # 全自动模式：不再提示待审草稿
    send_mode = "auto_send"
    return {
        "boss": boss,
        "chrome": chrome,
        "deps": deps,
        "llm": llm,
        "jev": jev,
        "faq_count": len(faq),
        "positions": pos_stats,
        "send_mode": send_mode,
        "send_mode_label": "AI 直接发送" if send_mode == "auto_send" else "只出草稿",
        "today_auto_sent": stats.get("today_auto_sent", 0),
        "today_handoff": stats.get("today_handoff", 0),
        "daily_max": (policy.get("rate_limit") or {}).get("daily_max"),
        "pending_drafts": pending_drafts,
        "auto": {
            "running": auto.get("running"),
            "last_message": auto.get("last_message"),
            "last_skip_reason": auto.get("last_skip_reason"),
            "processed": auto.get("processed"),
            "sent": auto.get("sent"),
            "handoff": auto.get("handoff"),
        },
        "adoption": drafts_io.adoption_stats(),
        "issues": issues,
        "onboarding": len(issues) >= 3 and not llm["api_key"]["configured"],
    }


# ---------- LLM / Jev ----------


@app.get("/api/llm")
def get_llm() -> dict[str, Any]:
    if lic.is_activated():
        view = lic.remote_model_view().get("llm") or {}
        return {
            "enabled": bool(view.get("enabled", True)),
            "provider": view.get("provider") or "",
            "base_url": view.get("base_url") or "",
            "model": view.get("model") or "",
            "timeout": view.get("timeout") or 30,
            "max_tokens": view.get("max_tokens") or 1600,
            "temperature": view.get("temperature") or 0.3,
            "api_key": {
                "configured": bool(view.get("api_key_configured")),
                "mask": "****(服务器下发)" if view.get("api_key_configured") else "",
            },
            "readonly": True,
            "source": "server",
        }
    return {**llm_public_view(), "readonly": False, "source": "local"}


@app.put("/api/llm")
def put_llm(body: LlmSave) -> dict[str, Any]:
    if lic.is_activated():
        raise HTTPException(403, "模型由总控台下发，客户机不可修改")
    try:
        view = apply_llm_save(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit("llm_save", {"provider": body.provider, "model": body.model, "enabled": body.enabled})
    return view


@app.post("/api/llm/test")
def llm_test() -> dict[str, Any]:
    result = test_llm_connection()
    audit("llm_test", {"ok": result.get("ok"), "human": result.get("human")})
    return result


@app.delete("/api/llm/key")
def llm_clear_key() -> dict[str, Any]:
    if lic.is_activated():
        raise HTTPException(403, "模型由总控台下发，客户机不可清除密钥")
    clear_llm_key()
    audit("llm_clear_key", {})
    return llm_public_view()


@app.get("/api/jev")
def get_jev() -> dict[str, Any]:
    if lic.is_activated():
        view = lic.remote_model_view().get("jev") or {}
        return {
            "enabled": bool(view.get("enabled", True)),
            "provider": view.get("provider") or "",
            "base_url": view.get("base_url") or "",
            "model": view.get("model") or "",
            "api_key": {
                "configured": bool(view.get("api_key_configured")),
                "mask": "****(服务器下发)" if view.get("api_key_configured") else "",
            },
            "readonly": True,
            "source": "server",
        }
    return {**jev_public_view(), "readonly": False, "source": "local"}


@app.put("/api/jev")
def put_jev(body: JevSave) -> dict[str, Any]:
    if lic.is_activated():
        raise HTTPException(403, "Jev 由总控台下发，客户机不可修改")
    try:
        view = apply_jev_save(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit("jev_save", {"provider": body.provider, "model": body.model, "enabled": body.enabled})
    return view


@app.post("/api/jev/test")
def jev_test() -> dict[str, Any]:
    result = test_jev_connection()
    audit("jev_test", {"ok": result.get("ok"), "human": result.get("human")})
    return result


@app.delete("/api/jev/key")
def jev_clear_key() -> dict[str, Any]:
    if lic.is_activated():
        raise HTTPException(403, "Jev 由总控台下发，客户机不可清除密钥")
    clear_jev_key()
    audit("jev_clear_key", {})
    return jev_public_view()


@app.get("/api/presets")
def presets() -> dict[str, Any]:
    return {
        "llm": [
            {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
            {"id": "qwen", "name": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
            {"id": "doubao", "name": "豆包/火山方舟", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-pro-32k"},
            {"id": "zhipu", "name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
            {"id": "moonshot", "name": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
            {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
            {"id": "qianfan", "name": "百度千帆", "base_url": "https://qianfan.baidubce.com/v2", "model": "ernie-4.0-8k"},
            {"id": "ollama", "name": "Ollama（本地）", "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.1"},
            {"id": "vllm", "name": "vLLM（本地）", "base_url": "http://127.0.0.1:8000/v1", "model": "default"},
            {"id": "custom", "name": "自定义", "base_url": "", "model": ""},
        ],
        "jev": [
            {"id": "typesafe", "name": "TypeSafe 官方", "base_url": "https://api.typesafe.ai", "model": "jev-latest"},
            {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api", "model": "~typesafe/jev-latest"},
            {"id": "vercel", "name": "Vercel AI Gateway", "base_url": "https://ai-gateway.vercel.sh/typesafe", "model": "typesafe-ai/jev"},
            {"id": "custom", "name": "自定义", "base_url": "", "model": "jev-latest"},
        ],
    }


# ---------- policy ----------


@app.get("/api/policy")
def get_policy() -> dict[str, Any]:
    return read_policy()


@app.put("/api/policy")
def put_policy(body: PolicyBody) -> dict[str, Any]:
    data = body.policy
    # 强制锁定：仅 not-fit
    actions = data.setdefault("actions", {})
    actions["not-fit"] = False
    if data.get("send_mode") == "auto_send" and read_policy().get("send_mode") != "auto_send":
        # 仅记录；确认由前端弹窗完成
        audit("send_mode_auto", {})
    write_policy(data)
    audit("policy_save", {"send_mode": data.get("send_mode"), "wechat": actions.get("wechat")})
    return read_policy()


# ---------- FAQ ----------


@app.get("/api/faq")
def get_faq(path: str | None = None) -> dict[str, Any]:
    from admin.faq_io import resolve_faq_path

    target = resolve_faq_path(path)
    return {"items": load_faq(target), "path": str(target.relative_to(ROOT) if target.is_relative_to(ROOT) else target)}


@app.put("/api/faq")
def put_faq(body: FaqSave) -> dict[str, Any]:
    n = save_faq(body.items, path=body.path)
    return {"ok": True, "count": n, "path": body.path or str(FAQ_PATH.relative_to(ROOT))}


@app.post("/api/faq/preview-upload")
async def faq_preview_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        items = preview_from_upload(file.filename or "upload.md", raw)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"items": items, "count": len(items)}


@app.post("/api/faq/preview-paste")
def faq_preview_paste(body: FaqPaste) -> dict[str, Any]:
    items = preview_from_paste(body.text)
    return {"items": items, "count": len(items)}


@app.post("/api/faq/ai-suggest")
def faq_ai_suggest(body: FaqAiSuggest) -> dict[str, Any]:
    """按行业用总控 LLM 生成公共 FAQ 推荐（预览，不写入）。"""
    from admin.faq_ai import suggest_common_faq

    load_env_into_os()
    existing = load_faq()
    try:
        result = suggest_common_faq(
            industry=body.industry,
            existing=existing,
            count=body.count or 10,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"AI 推荐失败：{exc}") from exc
    audit(
        "common_faq_ai_suggest",
        {"industry": result.get("industry"), "count": len(result.get("items") or [])},
    )
    return result


# ---------- positions / synonyms ----------


@app.get("/api/positions")
def get_positions() -> dict[str, Any]:
    from admin.positions_io import list_positions_view

    return list_positions_view()


@app.post("/api/positions/sync")
def positions_sync() -> dict[str, Any]:
    from admin.positions_io import sync_positions_from_boss

    result = sync_positions_from_boss()
    audit("positions_sync", {"ok": result.get("ok"), "count": result.get("count")})
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "同步失败")
    return result


@app.put("/api/positions/{pos_id}")
def put_position(pos_id: str, body: PositionPatch) -> dict[str, Any]:
    from admin.positions_io import update_position

    try:
        item = update_position(pos_id, body.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit("position_update", {"id": pos_id})
    return {"ok": True, "item": item}


@app.get("/api/positions/{pos_id}/faq")
def get_position_faq(pos_id: str) -> dict[str, Any]:
    from admin.positions_io import list_positions_view
    from admin.faq_io import resolve_faq_path

    view = list_positions_view()
    item = next((x for x in view["items"] if str(x.get("id")) == pos_id), None)
    if not item:
        raise HTTPException(404, "职位不存在")
    rel = (item.get("faq_path") or "").strip()
    if not rel:
        return {"items": [], "path": "", "empty": True}
    target = resolve_faq_path(rel)
    return {"items": load_faq(target), "path": rel, "empty": not target.exists()}


@app.put("/api/positions/{pos_id}/faq")
def put_position_faq(pos_id: str, body: FaqSave) -> dict[str, Any]:
    from admin.positions_io import list_positions_view, update_position
    from src.policy import default_faq_path_for_title

    view = list_positions_view()
    item = next((x for x in view["items"] if str(x.get("id")) == pos_id), None)
    if not item:
        raise HTTPException(404, "职位不存在")
    rel = (item.get("faq_path") or "").strip() or default_faq_path_for_title(
        read_policy(), str(item.get("title") or pos_id)
    )
    if not item.get("faq_path"):
        update_position(pos_id, {"faq_path": rel})
    n = save_faq(body.items, path=rel)
    audit("position_faq_save", {"id": pos_id, "path": rel, "count": n})
    return {"ok": True, "count": n, "path": rel}


@app.post("/api/positions/{pos_id}/faq/ai-suggest")
def position_faq_ai_suggest(pos_id: str) -> dict[str, Any]:
    """用总控下发的 LLM 生成职位专属 FAQ 推荐（预览，不写入）。"""
    from admin.faq_ai import suggest_position_faq
    from admin.faq_io import resolve_faq_path
    from admin.positions_io import list_positions_view

    load_env_into_os()
    view = list_positions_view()
    item = next((x for x in view["items"] if str(x.get("id")) == pos_id), None)
    if not item:
        raise HTTPException(404, "职位不存在")
    existing: list[dict[str, str]] = []
    rel = (item.get("faq_path") or "").strip()
    if rel:
        try:
            existing = load_faq(resolve_faq_path(rel))
        except Exception:
            existing = []
    try:
        result = suggest_position_faq(
            title=str(item.get("title") or ""),
            aliases=list(item.get("aliases") or []),
            existing_pos=existing,
            count=8,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"AI 推荐失败：{exc}") from exc
    audit(
        "position_faq_ai_suggest",
        {"id": pos_id, "title": item.get("title"), "count": len(result.get("items") or [])},
    )
    return result


@app.get("/api/synonyms")
def get_synonyms() -> dict[str, Any]:
    from src.synonyms import load_synonyms, synonyms_path

    data = load_synonyms()
    p = synonyms_path()
    path_str = str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p)
    return {"concepts": data.get("concepts") or {}, "path": path_str}


@app.put("/api/synonyms")
def put_synonyms(body: SynonymsBody) -> dict[str, Any]:
    from src.synonyms import save_synonyms

    save_synonyms({"concepts": body.concepts})
    audit("synonyms_save", {"keys": list((body.concepts or {}).keys())})
    return {"ok": True}


# ---------- tasks / logs / boss ----------


@app.get("/api/tasks/{task_id}")
def task_status(task_id: str) -> dict[str, Any]:
    t = get_task(task_id)
    if not t:
        # try disk
        path = ROOT / "data" / "tasks" / f"{task_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise HTTPException(404, "任务不存在")
    return t.to_dict()


@app.post("/api/tasks/{task_id}/cancel")
def task_cancel(task_id: str) -> dict[str, Any]:
    ok = cancel_task(task_id)
    return {"ok": ok}


@app.get("/api/logs/boss")
def logs_boss() -> dict[str, Any]:
    return {"items": read_boss_logs(150)}


@app.get("/api/logs/audit")
def logs_audit() -> dict[str, Any]:
    return {"items": read_audit(200)}


@app.get("/api/logs/model")
def logs_model(kind: str | None = None, purpose: str | None = None, limit: int = 100) -> dict[str, Any]:
    from admin.model_log import read_model_logs

    return {"items": read_model_logs(limit=min(limit, 300), kind=kind or None, purpose=purpose or None)}


@app.delete("/api/logs/model")
def logs_model_clear() -> dict[str, Any]:
    from admin.model_log import clear_model_logs

    clear_model_logs()
    audit("model_logs_clear", {})
    return {"ok": True}


@app.get("/api/deps")
def api_deps() -> dict[str, Any]:
    return deps_status()


@app.post("/api/deps/chrome/open-download")
def api_chrome_open_download(intl: bool = False) -> dict[str, Any]:
    return open_chrome_download(intl=intl)


@app.post("/api/boss/run")
def boss_run(body: BossRun) -> dict[str, Any]:
    try:
        return run_boss(body.args, timeout=body.timeout, confirmed=body.confirmed)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(500, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc


@app.post("/api/boss/open-login")
def boss_open_login() -> dict[str, Any]:
    """打开 Boss 网页登录页，供用户扫码；命令立即返回，不轮询登录结果。"""
    chrome = probe_chrome()
    if not chrome.get("installed"):
        return {
            "ok": False,
            "need_chrome": True,
            "download_url": chrome.get("download_url") or "",
            "human": "未检测到 Google Chrome。请先安装 Chrome 后再扫码登录。",
        }
    try:
        result = run_boss(["login"], timeout=60.0, confirmed=True)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"未找到自动化引擎：{exc}") from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    audit("boss_open_login", {"exit_code": result.get("exit_code")})
    ok = int(result.get("exit_code") or 0) == 0
    return {
        "ok": ok,
        "human": "已打开登录页，请用手机 App 扫码登录"
        if ok
        else "打开登录页失败，请确认自动化引擎 / Chrome 可用",
        "stdout": (result.get("stdout") or "")[:500],
        "stderr": (result.get("stderr") or "")[:500],
    }


# ---------- auto loop + drafts ----------


@app.get("/api/auto/status")
def auto_status() -> dict[str, Any]:
    from src import resume_followup as rf

    snap = LOOP.snapshot()
    snap["adoption"] = drafts_io.adoption_stats()
    snap["send_mode"] = read_policy().get("send_mode")
    snap["resume_followups"] = rf.list_pending()[:20]
    rr = read_policy().get("resume_review") or {}
    snap["resume_review"] = {
        "enabled": rr.get("enabled", True) is not False,
        "read_min_sec": int(rr.get("read_min_sec") or 240),
        "read_max_sec": int(rr.get("read_max_sec") or 360),
    }
    return snap


@app.post("/api/auto/start")
def auto_start() -> dict[str, Any]:
    load_env_into_os()
    policy = read_policy()
    if policy.get("send_mode") == "auto_send":
        audit("auto_start_with_auto_send", {})
    return LOOP.start()


@app.post("/api/auto/stop")
def auto_stop() -> dict[str, Any]:
    return LOOP.stop("用户停止")


@app.post("/api/auto/once")
def auto_once() -> dict[str, Any]:
    """只处理当前未读一轮。"""
    load_env_into_os()
    try:
        return LOOP.run_once()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


# ---------- 自动打招呼（recommend → greet） ----------


class GreetStart(BaseModel):
    confirmed: bool = False
    names: list[str] | None = None


class GreetRecommend(BaseModel):
    job_keyword: str | None = None


class GreetConfig(BaseModel):
    enabled: bool = True
    daily_max: int = 30
    job_keyword: str = ""
    only_can_greet: bool = True
    window_start: str = ""
    window_end: str = ""
    unread_first: bool = False
    pause_reply_during_greet: bool = False
    exclusive_window: bool = False
    # 推荐筛选
    age_min: int | None = None
    age_max: int | None = None
    gender: str = "any"  # any | female | male
    keywords: str = ""  # 期望/优势需包含，逗号分隔
    keywords_exclude: str = ""  # 排除词
    # 日程（与回消息互斥）
    greet_start: str | None = None
    greet_end: str | None = None
    reply_start: str | None = None
    reply_end: str | None = None


class ScheduleBody(BaseModel):
    greet_start: str = ""
    greet_end: str = ""
    reply_start: str = ""
    reply_end: str = ""


@app.get("/api/greet/status")
def greet_status() -> dict[str, Any]:
    from src.greet_loop import GREET

    return GREET.snapshot()


@app.post("/api/greet/recommend")
def greet_recommend(body: GreetRecommend) -> dict[str, Any]:
    from src.greet_loop import GREET

    load_env_into_os()
    try:
        return GREET.fetch_recommend(body.job_keyword)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/greet/start")
def greet_start(body: GreetStart) -> dict[str, Any]:
    from src.greet_loop import GREET

    load_env_into_os()
    try:
        return GREET.start(names=body.names, confirmed=body.confirmed)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/greet/stop")
def greet_stop() -> dict[str, Any]:
    from src.greet_loop import GREET

    return GREET.stop("用户停止")


@app.get("/api/schedule")
def get_schedule() -> dict[str, Any]:
    from src.schedule import schedule_public

    return schedule_public(read_policy())


@app.put("/api/schedule")
def put_schedule(body: ScheduleBody) -> dict[str, Any]:
    from src.schedule import norm_hm, schedule_public, validate_schedule

    sch = {
        "greet_start": norm_hm(body.greet_start),
        "greet_end": norm_hm(body.greet_end),
        "reply_start": norm_hm(body.reply_start),
        "reply_end": norm_hm(body.reply_end),
    }
    err = validate_schedule(sch)
    if err:
        raise HTTPException(400, err)
    p = read_policy()
    p["schedule"] = sch
    # 同步到旧字段，兼容
    g = dict(p.get("greet") or {})
    g["window_start"] = sch["greet_start"]
    g["window_end"] = sch["greet_end"]
    p["greet"] = g
    write_policy(p)
    audit("schedule_save", sch)
    return {"ok": True, "schedule": schedule_public(p)}


@app.put("/api/greet/config")
def greet_config(body: GreetConfig) -> dict[str, Any]:
    from src.schedule import norm_hm, schedule_public, validate_schedule

    p = read_policy()
    g = dict(p.get("greet") or {})
    g["enabled"] = body.enabled
    g["daily_max"] = max(0, int(body.daily_max))
    g["job_keyword"] = (body.job_keyword or "").strip()
    g["only_can_greet"] = body.only_can_greet
    g["unread_first"] = bool(body.unread_first)
    g["pause_reply_during_greet"] = bool(body.pause_reply_during_greet)
    g["exclusive_window"] = bool(body.exclusive_window)

    # 年龄 / 性别 / 关键词
    def _opt_age(v: int | None) -> int | None:
        if v is None:
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        if n <= 0:
            return None
        return max(16, min(65, n))

    amin = _opt_age(body.age_min)
    amax = _opt_age(body.age_max)
    if amin is not None and amax is not None and amax < amin:
        amin, amax = amax, amin
    g["age_min"] = amin
    g["age_max"] = amax
    g["gender"] = (
        "female"
        if str(body.gender or "").lower() in ("female", "女", "f")
        else "male"
        if str(body.gender or "").lower() in ("male", "男", "m")
        else "any"
    )
    g["keywords"] = (body.keywords or "").strip()
    g["keywords_exclude"] = (body.keywords_exclude or "").strip()

    # 日程：优先用新字段，否则用 window_*
    sch = dict(p.get("schedule") or {})
    gs = norm_hm(body.greet_start if body.greet_start is not None else body.window_start)
    ge = norm_hm(body.greet_end if body.greet_end is not None else body.window_end)
    rs = norm_hm(body.reply_start if body.reply_start is not None else sch.get("reply_start"))
    re_ = norm_hm(body.reply_end if body.reply_end is not None else sch.get("reply_end"))
    # 若本次请求带了 reply_* 或 greet_*，整表校验
    next_sch = {
        "greet_start": gs,
        "greet_end": ge,
        "reply_start": rs,
        "reply_end": re_,
    }
    err = validate_schedule(next_sch)
    if err:
        raise HTTPException(400, err)
    p["schedule"] = next_sch
    g["window_start"] = gs
    g["window_end"] = ge
    p["greet"] = g
    write_policy(p)
    audit(
        "greet_config",
        {
            "daily_max": g["daily_max"],
            "job": g["job_keyword"],
            "age_min": g.get("age_min"),
            "age_max": g.get("age_max"),
            "gender": g.get("gender"),
            "keywords": g.get("keywords"),
            "schedule": next_sch,
        },
    )
    from src.greet_loop import GREET

    return {
        "ok": True,
        "greet": g,
        "schedule": schedule_public(p),
        "status": GREET.snapshot(),
    }


class SendModeBody(BaseModel):
    send_mode: str
    confirmed: bool = False


@app.put("/api/auto/send-mode")
def auto_set_send_mode(body: SendModeBody) -> dict[str, Any]:
    """切换 draft_only / auto_send，立即写入 policy.yaml。"""
    mode = (body.send_mode or "").strip()
    if mode not in {"draft_only", "auto_send"}:
        raise HTTPException(400, "send_mode 只能是 draft_only 或 auto_send")
    policy = read_policy()
    prev = policy.get("send_mode") or "draft_only"
    if mode == "auto_send" and prev != "auto_send" and not body.confirmed:
        raise HTTPException(403, "切换到 AI 直接发送需要二次确认")
    policy["send_mode"] = mode
    write_policy(policy)
    audit("send_mode_change", {"from": prev, "to": mode})
    return {"ok": True, "send_mode": mode, "previous": prev}


@app.get("/api/drafts")
def get_drafts(status: str | None = None) -> dict[str, Any]:
    return {"items": drafts_io.list_drafts(status=status), "stats": drafts_io.adoption_stats()}


@app.post("/api/drafts/send")
def draft_send(body: DraftAction) -> dict[str, Any]:
    if not body.confirmed:
        raise HTTPException(403, "发送草稿需要二次确认")
    items = {d["id"]: d for d in drafts_io.list_drafts(limit=5000)}
    row = items.get(body.id)
    if not row:
        raise HTTPException(404, "草稿不存在")
    text = (body.draft if body.draft is not None else row.get("draft") or "").strip()
    if not text:
        raise HTTPException(400, "草稿为空")
    # 打开会话再发送
    name = row.get("candidate") or ""
    chat = run_boss(["chat", name], timeout=180, confirmed=True)
    if chat.get("exit_code") != 0:
        raise HTTPException(500, (chat.get("stderr") or "打开会话失败")[:300])
    from src.send_timing import sleep_before_send

    sleep_before_send(text, read_policy())
    sent = run_boss(["send", "--text", text], timeout=120, confirmed=True)
    if sent.get("exit_code") != 0:
        raise HTTPException(500, (sent.get("stderr") or sent.get("stdout") or "发送失败")[:300])
    status = "edited_sent" if body.draft and body.draft != row.get("draft") else "sent"
    updated = drafts_io.update_draft(body.id, status=status, draft=text, reason="")

    # 草稿标记为待求简历时：发完文字后再点求简历（Boss 互发门槛）
    resume_boss = None
    want_resume = (
        row.get("action") == "request_resume"
        or str(row.get("reason") or "").startswith("warmup_before_request_resume")
    )
    if want_resume and (read_policy().get("actions") or {}).get("request-attachment-resume"):
        import time as _time

        _time.sleep(1.0)
        resume_boss = run_boss(["action", "request-attachment-resume"], timeout=120, confirmed=True)
        if resume_boss.get("exit_code") == 0:
            updated = drafts_io.update_draft(
                body.id, action="request_resume", reason="已发送并自动求简历"
            ) or updated
            audit("draft_send_then_request_resume", {"id": body.id, "candidate": name})
        else:
            audit(
                "draft_send_request_resume_fail",
                {
                    "id": body.id,
                    "err": (resume_boss.get("stderr") or resume_boss.get("stdout") or "")[:200],
                },
            )

    # 采用率统计
    p2 = read_policy()
    st, _ = ensure_daily_stats(p2)
    st["draft_adopted"] = int(st.get("draft_adopted") or 0) + 1
    st["draft_total"] = int(st.get("draft_total") or 0) + 1
    st["today_auto_sent"] = int(st.get("today_auto_sent") or 0) + 1
    write_policy(p2)
    audit("draft_send", {"id": body.id, "candidate": name, "status": status})
    return {"ok": True, "draft": updated, "boss": sent, "resume_action": resume_boss}


@app.post("/api/drafts/discard")
def draft_discard(body: DraftAction) -> dict[str, Any]:
    updated = drafts_io.update_draft(body.id, status="discarded")
    if not updated:
        raise HTTPException(404, "草稿不存在")
    audit("draft_discard", {"id": body.id})
    return {"ok": True, "draft": updated}


@app.post("/api/drafts/handoff")
def draft_handoff(body: DraftAction) -> dict[str, Any]:
    updated = drafts_io.update_draft(body.id, status="handoff", reason="人工标记转人工")
    if not updated:
        raise HTTPException(404, "草稿不存在")
    audit("draft_handoff", {"id": body.id})
    return {"ok": True, "draft": updated}


@app.post("/api/drafts/restore")
def draft_restore(body: DraftAction) -> dict[str, Any]:
    """转人工条目恢复为待审草稿。"""
    updated = drafts_io.update_draft(body.id, status="pending", reason="")
    if not updated:
        raise HTTPException(404, "草稿不存在")
    if body.draft is not None:
        updated = drafts_io.update_draft(body.id, draft=body.draft) or updated
    audit("draft_restore", {"id": body.id})
    return {"ok": True, "draft": updated}


@app.post("/api/drafts/regenerate")
def draft_regenerate(body: DraftAction) -> dict[str, Any]:
    from src.reply import generate_reply
    from src.judge import Judge
    from src.policy import get_job

    load_env_into_os()
    items = {d["id"]: d for d in drafts_io.list_drafts(limit=5000)}
    row = items.get(body.id)
    if not row:
        raise HTTPException(404, "草稿不存在")
    question = str(row.get("question") or "")
    if not question:
        raise HTTPException(400, "原问题为空，无法重生成")
    policy = read_policy()
    job = get_job(policy, None)
    judge = Judge()
    judgment = judge.evaluate_incoming(question, policy=policy, job=job)
    next_action = str(judgment.get("next_action") or "continue")
    from src.auto_loop import _TEXT_ACTION_TEMPLATES

    if judgment.get("actionable") and next_action in _TEXT_ACTION_TEMPLATES:
        draft = _TEXT_ACTION_TEMPLATES[next_action]
        gen_reason = f"action_template:{next_action}"
    else:
        gen = generate_reply(question, job_id=job.get("id"), intent=str(judgment.get("intent") or "other"))
        draft = (gen.get("draft") or "").strip()
        gen_reason = gen.get("reason") or ""
    q = judge.score_draft_quality(question, draft, policy) if draft else 0.0
    judge.close()
    updated = drafts_io.update_draft(
        body.id,
        draft=draft,
        status="pending",
        judgment=judgment,
        draft_quality=q,
        reason=gen_reason,
        action=next_action if judgment.get("actionable") else row.get("action"),
    )
    audit("draft_regenerate", {"id": body.id})
    return {"ok": True, "draft": updated}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ---------- 卡密 / 更新 ----------


class LicenseActivateBody(BaseModel):
    server_url: str | None = None
    license_key: str


class LicenseUpdateBody(BaseModel):
    server_url: str | None = None


def _default_license_server() -> str:
    return (
        (os.environ.get("LICENSE_SERVER_DEFAULT") or "").strip().rstrip("/")
        or "https://your-license-server.example.com"
    )


@app.get("/api/license/status")
def license_status() -> dict[str, Any]:
    st = lic.status_public()
    st["bypass"] = _license_bypass()
    if not st.get("server_url"):
        st["server_url"] = _default_license_server()
    return st


@app.post("/api/license/activate")
def license_activate(body: LicenseActivateBody) -> dict[str, Any]:
    url = (body.server_url or "").strip().rstrip("/") or _default_license_server()
    key = (body.license_key or "").strip()
    if not key:
        raise HTTPException(400, "请输入卡密")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "总控台地址配置无效，请联系管理员")
    try:
        result = lic.activate(url, key)
    except Exception as exc:
        err = (str(exc).strip() or type(exc).__name__)
        audit("license_activate_fail", {"error": err})
        raise HTTPException(400, err) from exc
    lic.set_locked(False)
    lic.start_heartbeat_loop()
    audit("license_activate_ok", {"server": url, "config_ok": result.get("config_ok", True)})
    # 激活成功后立刻打开 Boss 扫码登录，不依赖前端是否刷新成功
    _schedule_boss_login(reason="activate", delay_sec=0.8)
    human = "激活成功，正在打开扫码登录页"
    if result.get("config_error"):
        human = f"激活成功，但模型配置注入失败：{result['config_error']}"
    return {
        "ok": True,
        **result,
        "status": lic.status_public(),
        "boss_login": "opening",
        "human": human,
    }


@app.post("/api/license/deactivate")
def license_deactivate() -> dict[str, Any]:
    lic.deactivate_local()
    lic.set_locked(False)
    audit("license_deactivate", {})
    return {"ok": True}


@app.get("/api/license/check-update")
def license_check_update(server_url: str | None = None) -> dict[str, Any]:
    return lic.check_update(server_url)


@app.get("/api/license/update-progress")
def license_update_progress() -> dict[str, Any]:
    return lic.get_update_progress()


@app.post("/api/license/apply-update")
async def license_apply_update(body: LicenseUpdateBody) -> dict[str, Any]:
    import asyncio

    info = lic.check_update(body.server_url)
    if not info.get("has_update"):
        return {"ok": False, "human": info.get("error") or "已是最新版本", **info}
    url_path = info.get("url") or ""
    sha = (info.get("sha256") or "").strip().lower()
    expect_size = int(info.get("size") or 0)
    tmp = Path(tempfile.gettempdir()) / f"ZhiLiaoXiaoMi-Setup-{info.get('latest') or 'update'}.exe"
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: lic.download_update(
                url_path, tmp, body.server_url, expected_size=expect_size or None
            ),
        )
    except Exception as exc:
        raise HTTPException(400, f"下载失败：{exc}") from exc
    if sha:
        import hashlib

        lic.set_update_progress(status="verifying", human="正在校验安装包…")
        dig = await loop.run_in_executor(
            None, lambda: hashlib.sha256(tmp.read_bytes()).hexdigest().lower()
        )
        if dig != sha:
            tmp.unlink(missing_ok=True)
            lic.set_update_progress(status="error", error="sha256 不匹配", human="安装包校验失败")
            raise HTTPException(400, "安装包校验失败（sha256 不匹配）")
    try:
        lic.set_update_progress(status="launching", human="正在启动安装程序…", pct=100)
        subprocess.Popen([str(tmp)], close_fds=True)
    except Exception as exc:
        lic.set_update_progress(status="error", error=str(exc), human="无法启动安装包")
        raise HTTPException(500, f"无法启动安装包：{exc}") from exc
    audit("license_update_launch", {"version": info.get("latest"), "file": str(tmp)})
    lic.set_update_progress(status="done", pct=100, human="已启动安装程序")
    return {"ok": True, "human": "已启动安装程序，请按提示完成升级后重新打开", "path": str(tmp), **info}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")






