"""卡密总控台 FastAPI。"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server import db
from server.auth import COOKIE_NAME, create_session, destroy_session, valid_session
from server.paths import RELEASES_DIR, ROOT, STATIC_DIR

app = FastAPI(title="直聊小蜜总控台", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    # 允许从 .env.server 或项目 .env 注入
    _load_env_file(ROOT / ".env.server")
    _load_env_file(ROOT / ".env")
    db.init_db()
    # 总控模型密钥为空时，沿用客户机仓库 .env 中的 DeepSeek / Jev
    try:
        db.import_model_config_from_env_file(ROOT / ".env", only_if_empty=True)
    except Exception:
        pass


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v


def _require_admin(request: Request) -> None:
    if not valid_session(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(401, "未登录")


def _client_ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for") or ""
    if xf:
        return xf.split(",")[0].strip()
    return request.client.host if request.client else ""


# ---- admin auth ----


class LoginBody(BaseModel):
    password: str


@app.get("/api/admin/me")
def admin_me(request: Request) -> dict[str, Any]:
    ok = valid_session(request.cookies.get(COOKIE_NAME))
    settings = db.get_settings()
    return {"ok": ok, "site_name": settings["site_name"]}


@app.post("/api/admin/login")
def admin_login(body: LoginBody, response: Response) -> dict[str, Any]:
    if not db.verify_admin(body.password):
        db.audit("admin", "login_fail", {})
        raise HTTPException(401, "密码错误")
    token = create_session()
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=12 * 3600)
    db.audit("admin", "login_ok", {})
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response) -> dict[str, Any]:
    destroy_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


# ---- settings / models ----


@app.get("/api/admin/settings")
def admin_settings(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return db.get_settings()


class SettingsBody(BaseModel):
    site_name: str | None = None
    heartbeat_sec: int | None = None
    force_update: bool | None = None
    announcement: str | None = None
    grace_hours: int | None = None
    new_password: str | None = None


@app.put("/api/admin/settings")
def admin_settings_put(body: SettingsBody, request: Request) -> dict[str, Any]:
    _require_admin(request)
    patch = body.model_dump(exclude_none=True)
    new_pwd = patch.pop("new_password", None)
    out = db.update_settings(patch, new_password=new_pwd)
    db.audit("admin", "settings_save", {})
    return out


@app.get("/api/admin/presets")
def admin_presets(request: Request) -> dict[str, Any]:
    _require_admin(request)
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
            {"id": "typesafe", "name": "TypeSafe 官方", "base_url": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest"},
            {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api", "model": "~typesafe/jev-latest"},
            {"id": "vercel", "name": "Vercel AI Gateway", "base_url": "https://ai-gateway.vercel.sh/typesafe", "model": "typesafe-ai/jev"},
            {"id": "custom", "name": "自定义", "base_url": "", "model": "jev-latest"},
        ],
    }


@app.get("/api/admin/models")
def admin_models(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return db.get_model_config(include_secrets=False)


class ModelsBody(BaseModel):
    llm: dict[str, Any] | None = None
    jev: dict[str, Any] | None = None


@app.put("/api/admin/models")
def admin_models_put(body: ModelsBody, request: Request) -> dict[str, Any]:
    _require_admin(request)
    out = db.save_model_config(body.llm, body.jev)
    db.audit("admin", "models_save", {})
    return out


@app.delete("/api/admin/models/llm/key")
def admin_clear_llm_key(request: Request) -> dict[str, Any]:
    _require_admin(request)
    db.save_model_config({"api_key": ""}, None)
    db.audit("admin", "models_clear_llm_key", {})
    return db.get_model_config(include_secrets=False)


@app.delete("/api/admin/models/jev/key")
def admin_clear_jev_key(request: Request) -> dict[str, Any]:
    _require_admin(request)
    db.save_model_config(None, {"api_key": ""})
    db.audit("admin", "models_clear_jev_key", {})
    return db.get_model_config(include_secrets=False)


@app.post("/api/admin/models/test-llm")
def admin_test_llm(request: Request) -> dict[str, Any]:
    _require_admin(request)
    cfg = db.get_model_config(include_secrets=True)["llm"]
    return _probe_llm(cfg)


@app.post("/api/admin/models/test-jev")
def admin_test_jev(request: Request) -> dict[str, Any]:
    _require_admin(request)
    cfg = db.get_model_config(include_secrets=True)["jev"]
    return _probe_jev(cfg)


# ---- licenses / activations ----


class LicenseCreate(BaseModel):
    count: int = 1
    note: str = ""
    max_devices: int = 1
    valid_days: int = 365


@app.get("/api/admin/licenses")
def admin_licenses(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return {"items": db.list_licenses()}


@app.post("/api/admin/licenses")
def admin_licenses_create(body: LicenseCreate, request: Request) -> dict[str, Any]:
    _require_admin(request)
    items = db.create_licenses(
        count=body.count,
        note=body.note,
        max_devices=body.max_devices,
        valid_days=body.valid_days,
    )
    return {"items": items, "count": len(items)}


@app.post("/api/admin/licenses/{license_id}/revoke")
def admin_license_revoke(license_id: int, request: Request) -> dict[str, Any]:
    _require_admin(request)
    db.revoke_license(license_id)
    return {"ok": True}


@app.get("/api/admin/activations")
def admin_activations(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return {"items": db.list_activations()}


@app.post("/api/admin/activations/{activation_id}/revoke")
def admin_activation_revoke(activation_id: int, request: Request) -> dict[str, Any]:
    _require_admin(request)
    db.revoke_activation(activation_id)
    return {"ok": True}


@app.get("/api/admin/audit")
def admin_audit(request: Request, limit: int = 100) -> dict[str, Any]:
    _require_admin(request)
    return {"items": db.list_audit(limit)}


# ---- releases ----


@app.get("/api/admin/releases")
def admin_releases(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return {"items": db.list_releases(), "latest": db.get_latest_release()}


@app.post("/api/admin/releases")
async def admin_release_upload(
    request: Request,
    file: UploadFile = File(...),
    version: str = "",
    notes: str = "",
    force_update: bool = False,
) -> dict[str, Any]:
    _require_admin(request)
    if not version.strip():
        raise HTTPException(400, "版本号必填")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    name = Path(file.filename or f"ZhiLiaoXiaoMi-Setup-{version}.exe").name
    safe = "".join(c for c in name if c.isalnum() or c in "._-")
    if not safe:
        safe = f"ZhiLiaoXiaoMi-Setup-{version.strip()}.exe"
    dest = RELEASES_DIR / safe
    dest.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    row = db.publish_release(
        version=version.strip(),
        filename=safe,
        sha256=sha,
        size_bytes=len(raw),
        notes=notes,
        force_update=force_update,
        set_latest=True,
    )
    return row


# ---- client public API ----


class ActivateBody(BaseModel):
    license_key: str
    device_id: str
    machine_name: str = ""


class SessionBody(BaseModel):
    session_token: str
    device_id: str


@app.post("/api/v1/activate")
def client_activate(body: ActivateBody, request: Request) -> dict[str, Any]:
    try:
        return db.activate_client(
            license_key=body.license_key,
            device_id=body.device_id,
            machine_name=body.machine_name,
            ip=_client_ip(request),
        )
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/heartbeat")
def client_heartbeat(body: SessionBody, request: Request) -> dict[str, Any]:
    try:
        return db.heartbeat_client(
            session_token=body.session_token,
            device_id=body.device_id,
            ip=_client_ip(request),
        )
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.post("/api/v1/deactivate")
def client_deactivate(body: SessionBody) -> dict[str, Any]:
    db.deactivate_client(session_token=body.session_token, device_id=body.device_id)
    return {"ok": True}


@app.get("/api/v1/client/version")
def client_version(current: str = "0.0.0") -> dict[str, Any]:
    latest = db.get_latest_release()
    settings = db.get_settings()
    if not latest:
        return {
            "has_update": False,
            "current": current,
            "latest": current,
            "force": False,
            "announcement": settings["announcement"],
        }
    newer = db.compare_version(latest["version"], current) > 0
    force = bool(latest.get("force_update") or settings.get("force_update")) and newer
    return {
        "has_update": newer,
        "current": current,
        "latest": latest["version"],
        "force": force,
        "url": f"/api/v1/client/download/{latest['version']}",
        "sha256": latest["sha256"],
        "size": latest["size_bytes"],
        "notes": latest.get("notes") or "",
        "announcement": settings["announcement"],
    }


@app.get("/api/v1/client/download/{version}")
def client_download(version: str) -> FileResponse:
    items = {r["version"]: r for r in db.list_releases()}
    row = items.get(version) or (db.get_latest_release() if version == "latest" else None)
    if not row:
        raise HTTPException(404, "版本不存在")
    path = RELEASES_DIR / row["filename"]
    if not path.exists():
        raise HTTPException(404, "安装包文件缺失")
    return FileResponse(path, filename=row["filename"], media_type="application/octet-stream")


def _probe_llm(cfg: dict[str, Any]) -> dict[str, Any]:
    import json
    import urllib.request

    key = (cfg.get("api_key") or "").strip()
    base = (cfg.get("base_url") or "").rstrip("/")
    model = cfg.get("model") or ""
    if not key or not base:
        return {"ok": False, "human": "未配置 API Key 或地址"}
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout") or 30)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return {"ok": True, "human": "大模型连通正常", "raw": raw[:200]}
    except Exception as exc:
        return {"ok": False, "human": "连接失败", "error": str(exc)}


def _probe_jev(cfg: dict[str, Any]) -> dict[str, Any]:
    import json
    import urllib.request

    key = (cfg.get("api_key") or "").strip()
    base = (cfg.get("base_url") or "https://api.typesafe.ai").rstrip("/")
    if not key:
        return {"ok": False, "human": "未配置 Jev Key"}
    url = base if "systemone" in base else base + "/v1/systemone"
    payload = {
        "model": cfg.get("model") or "jev-latest",
        "state": {"ping": True},
        "questions": {"ok": {"type": "noul", "instructions": "always true", "criteria": {"true": "ok", "false": "no"}}},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ok = isinstance(data, dict) and "answers" in data
        return {"ok": ok, "human": "Jev 连通正常" if ok else "响应缺 answers", "raw": str(data)[:200]}
    except Exception as exc:
        return {"ok": False, "human": "连接失败", "error": str(exc)}


def _apply_model_env_from_db() -> None:
    """把总控 DB 中的 LLM/Jev 写入进程环境，供 sim_chat / batch_test / reply / jev 读取。"""
    cfg = db.get_model_config(include_secrets=True)
    llm = cfg.get("llm") or {}
    jev = cfg.get("jev") or {}

    def _set(key: str, value: Any) -> None:
        if value is None:
            return
        s = str(value).strip() if not isinstance(value, bool) else ("true" if value else "false")
        if s == "" and key.endswith("_API_KEY"):
            return
        os.environ[key] = s

    _set("LLM_ENABLED", llm.get("enabled", True))
    _set("LLM_PROVIDER", llm.get("provider") or "deepseek")
    if llm.get("base_url"):
        _set("LLM_BASE_URL", llm.get("base_url"))
        _set("OPENAI_BASE_URL", llm.get("base_url"))
    if llm.get("model"):
        _set("LLM_MODEL", llm.get("model"))
        _set("OPENAI_MODEL", llm.get("model"))
    if (llm.get("api_key") or "").strip():
        _set("LLM_API_KEY", llm.get("api_key"))
        _set("OPENAI_API_KEY", llm.get("api_key"))
    if llm.get("timeout") is not None:
        _set("LLM_TIMEOUT", llm.get("timeout"))
    if llm.get("max_tokens") is not None:
        _set("LLM_MAX_TOKENS", llm.get("max_tokens"))
    if llm.get("temperature") is not None:
        _set("LLM_TEMPERATURE", llm.get("temperature"))

    _set("JEV_ENABLED", jev.get("enabled", True))
    _set("JEV_PROVIDER", jev.get("provider") or "typesafe")
    if jev.get("base_url"):
        _set("JEV_BASE_URL", jev.get("base_url"))
    if jev.get("model"):
        _set("JEV_MODEL", jev.get("model"))
    if (jev.get("api_key") or "").strip():
        _set("JEV_API_KEY", jev.get("api_key"))
        _set("TYPESAFE_API_KEY", jev.get("api_key"))
    # 防止后续 load_env_into_os() 用仓库 .env 盖掉总控密钥
    os.environ["BOSS_MODEL_ENV_LOCKED"] = "1"


# ---- 模拟聊天 / 批量测试（仅总控；不调 boss-cli） ----


class SimReset(BaseModel):
    candidate: str | None = None
    job_title: str | None = None
    force_reply: bool = False


class SimMessage(BaseModel):
    text: str = Field(..., min_length=1)


class SimInjectHr(BaseModel):
    text: str = Field(..., min_length=1)


class SimOptions(BaseModel):
    force_reply: bool | None = None


class SimAutoPlay(BaseModel):
    turns: int = 5
    persona: str = "active"
    opener: str | None = None
    reset_first: bool = True
    job_title: str | None = None
    candidate: str | None = None
    candidate_mode: str = "auto"


class BatchPhrasesBody(BaseModel):
    text: str | None = None
    phrases: list[str] | None = None
    persist: bool = True


class BatchStartBody(BaseModel):
    job_title: str | None = None


@app.get("/api/admin/sim/session")
def admin_sim_session(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import STORE

    return STORE.snapshot()


@app.get("/api/admin/sim/personas")
def admin_sim_personas(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import PERSONAS

    return {
        "items": [
            {"id": k, "label": v["label"], "opener": v["opener"], "style": v["style"]}
            for k, v in PERSONAS.items()
        ]
    }


@app.patch("/api/admin/sim/session")
def admin_sim_patch(request: Request, body: SimOptions) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import STORE

    try:
        return STORE.set_options(force_reply=body.force_reply)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/admin/sim/reset")
def admin_sim_reset(request: Request, body: SimReset) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import STORE

    _apply_model_env_from_db()
    data = STORE.reset(
        candidate=body.candidate,
        job_title=body.job_title,
        force_reply=body.force_reply,
    )
    db.audit(
        "admin",
        "sim_reset",
        {"candidate": (data.get("session") or {}).get("candidate")},
    )
    return data


@app.post("/api/admin/sim/message")
def admin_sim_message(request: Request, body: SimMessage) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import STORE

    _apply_model_env_from_db()
    try:
        data = STORE.send(body.text)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        "admin",
        "sim_message",
        {
            "candidate": (data.get("session") or {}).get("candidate"),
            "outcome": (data.get("turn") or {}).get("outcome"),
        },
    )
    return data


@app.post("/api/admin/sim/reply-now")
def admin_sim_reply_now(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import STORE

    _apply_model_env_from_db()
    try:
        data = STORE.reply_now()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        "admin",
        "sim_reply_now",
        {
            "candidate": (data.get("session") or {}).get("candidate"),
            "outcome": (data.get("turn") or {}).get("outcome"),
        },
    )
    return data


@app.post("/api/admin/sim/inject-hr")
def admin_sim_inject_hr(request: Request, body: SimInjectHr) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import STORE

    try:
        data = STORE.inject_recruiter(body.text)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        "admin",
        "sim_inject_hr",
        {"candidate": (data.get("session") or {}).get("candidate")},
    )
    return data


@app.post("/api/admin/sim/auto")
def admin_sim_auto(request: Request, body: SimAutoPlay) -> dict[str, Any]:
    _require_admin(request)
    from src.sim_chat import STORE

    _apply_model_env_from_db()
    try:
        data = STORE.auto_play(
            turns=body.turns,
            persona=body.persona,
            opener=body.opener,
            reset_first=body.reset_first,
            job_title=body.job_title,
            candidate=body.candidate,
            candidate_mode=body.candidate_mode,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(
        "admin",
        "sim_auto",
        {
            "persona": data.get("persona"),
            "turns": len(data.get("transcript") or []),
            "candidate": (data.get("session") or {}).get("candidate"),
        },
    )
    return data


@app.get("/api/admin/batch-test/status")
def admin_batch_test_status(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.batch_test import RUNNER

    return RUNNER.snapshot()


@app.get("/api/admin/batch-test/phrases")
def admin_batch_test_phrases(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.batch_test import RUNNER, phrases_to_text

    items = RUNNER.get_phrases()
    return {"count": len(items), "phrases": items, "text": phrases_to_text(items)}


@app.put("/api/admin/batch-test/phrases")
def admin_batch_test_set_phrases(request: Request, body: BatchPhrasesBody) -> dict[str, Any]:
    _require_admin(request)
    from src.batch_test import RUNNER, phrases_to_text

    try:
        if body.phrases is not None:
            items = RUNNER.set_phrases(body.phrases, persist=body.persist)
        else:
            items = RUNNER.set_phrases(body.text or "", persist=body.persist)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit("admin", "batch_phrases", {"count": len(items), "persist": body.persist})
    return {"ok": True, "count": len(items), "phrases": items, "text": phrases_to_text(items)}


@app.post("/api/admin/batch-test/load-default")
def admin_batch_test_load_default(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.batch_test import RUNNER, phrases_to_text

    try:
        items = RUNNER.load_default()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit("admin", "batch_load_default", {"count": len(items)})
    return {"ok": True, "count": len(items), "phrases": items, "text": phrases_to_text(items)}


@app.post("/api/admin/batch-test/start")
def admin_batch_test_start(request: Request, body: BatchStartBody) -> dict[str, Any]:
    _require_admin(request)
    from src.batch_test import RUNNER

    _apply_model_env_from_db()
    try:
        data = RUNNER.start(job_title=body.job_title)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit("admin", "batch_start", {"total": (data.get("status") or {}).get("total")})
    return data


@app.post("/api/admin/batch-test/stop")
def admin_batch_test_stop(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.batch_test import RUNNER

    data = RUNNER.stop()
    db.audit("admin", "batch_stop", {})
    return data


@app.get("/api/admin/batch-test/export")
def admin_batch_test_export(request: Request) -> dict[str, Any]:
    _require_admin(request)
    from src.batch_test import RUNNER

    snap = RUNNER.snapshot()
    rows = []
    for r in snap.get("results") or []:
        rows.append(
            {
                "index": r.get("index"),
                "question": r.get("question"),
                "answer": r.get("answer"),
                "outcome": r.get("outcome"),
                "intent": r.get("intent"),
                "action": r.get("action") or r.get("next_action"),
                "reason": r.get("reason"),
                "handoff": r.get("handoff"),
                "notes": r.get("notes"),
                "elapsed_ms": r.get("elapsed_ms"),
                "error": r.get("error"),
            }
        )
    return {"status": snap.get("status"), "count": len(rows), "rows": rows}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
