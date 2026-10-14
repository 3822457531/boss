"""客户机卡密激活、心跳、远程模型配置注入。"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from admin.version import CLIENT_VERSION, PRODUCT_NAME
from server.crypto import decrypt_config

_ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """打包后用 LOCALAPPDATA，开发态用仓库 data/。"""
    if getattr(sys, "frozen", False) or os.environ.get("BOSS_ADMIN_APPDATA"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        d = base / PRODUCT_NAME
    else:
        d = _ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def identity_path() -> Path:
    return data_dir() / "client_identity.json"


def license_state_path() -> Path:
    return data_dir() / "license_client.json"


def load_identity() -> dict[str, str]:
    path = identity_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("device_id"):
                return data
        except Exception:
            pass
    device_id = str(uuid.uuid4())
    data = {"device_id": device_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_license_state() -> dict[str, Any]:
    path = license_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_license_state(data: dict[str, Any]) -> None:
    path = license_state_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_activated() -> bool:
    st = load_license_state()
    return bool(st.get("session_token") and st.get("server_url") and st.get("license_key"))


def machine_name() -> str:
    try:
        return socket.gethostname() or "unknown"
    except Exception:
        return "unknown"


def _exc_text(exc: BaseException) -> str:
    msg = str(exc).strip()
    if msg:
        return msg
    return type(exc).__name__ or "未知错误"


def _post(server_url: str, path: str, payload: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    base = server_url.rstrip("/")
    url = base + path
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": f"{PRODUCT_NAME}/{CLIENT_VERSION}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail") or raw
        except Exception:
            detail = raw
        if isinstance(detail, list):
            detail = "; ".join(str(x) for x in detail)
        raise RuntimeError(str(detail).strip() or f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(f"无法连接总控台：{_exc_text(exc)}") from exc


def _get(server_url: str, path: str, timeout: float = 20) -> dict[str, Any]:
    url = server_url.rstrip("/") + path
    req = urllib.request.Request(url, headers={"User-Agent": f"{PRODUCT_NAME}/{CLIENT_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def activate(server_url: str, license_key: str) -> dict[str, Any]:
    ident = load_identity()
    data = _post(
        server_url,
        "/api/v1/activate",
        {
            "license_key": license_key.strip(),
            "device_id": ident["device_id"],
            "machine_name": machine_name(),
        },
    )
    if not data.get("session_token"):
        raise RuntimeError("总控台未返回会话，激活失败")
    st = {
        "server_url": server_url.rstrip("/"),
        "license_key": data.get("license_key") or license_key.strip().upper(),
        "session_token": data["session_token"],
        "device_id": ident["device_id"],
        "expires_at": data.get("expires_at") or "",
        "heartbeat_sec": int(data.get("heartbeat_sec") or 600),
        "crypto_salt": (data.get("crypto_salt") or "").strip(),
        "activated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fail_count": 0,
    }
    config_error = ""
    try:
        used = apply_config_blob(
            data.get("config_blob") or "",
            st["license_key"],
            st["device_id"],
            salt=st.get("crypto_salt") or None,
        )
        if used:
            st["crypto_salt"] = used
    except Exception as exc:
        # 会话已落盘：激活本身成功，模型配置可在心跳时重试
        config_error = _exc_text(exc)
    save_license_state(st)
    return {
        "ok": True,
        "expires_at": st["expires_at"],
        "announcement": data.get("announcement") or "",
        "config_ok": not config_error,
        "config_error": config_error,
    }


def heartbeat() -> dict[str, Any]:
    st = load_license_state()
    if not st.get("session_token"):
        raise RuntimeError("未激活")
    data = _post(
        st["server_url"],
        "/api/v1/heartbeat",
        {"session_token": st["session_token"], "device_id": st["device_id"]},
    )
    st["last_heartbeat"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    st["expires_at"] = data.get("expires_at") or st.get("expires_at") or ""
    st["heartbeat_sec"] = int(data.get("heartbeat_sec") or st.get("heartbeat_sec") or 600)
    st["fail_count"] = 0
    if data.get("license_key"):
        st["license_key"] = data["license_key"]
    if data.get("crypto_salt"):
        st["crypto_salt"] = str(data["crypto_salt"]).strip()
    used = apply_config_blob(
        data.get("config_blob") or "",
        st["license_key"],
        st["device_id"],
        salt=st.get("crypto_salt") or None,
    )
    if used:
        st["crypto_salt"] = used
    save_license_state(st)
    return {"ok": True, "announcement": data.get("announcement") or ""}


def deactivate_local() -> None:
    st = load_license_state()
    if st.get("session_token") and st.get("server_url"):
        try:
            _post(
                st["server_url"],
                "/api/v1/deactivate",
                {"session_token": st["session_token"], "device_id": st["device_id"]},
                timeout=10,
            )
        except Exception:
            pass
    save_license_state({})


def _salt_candidates(preferred: str | None = None) -> list[str]:
    """解密时尝试的 salt 列表：响应下发 > 本机缓存/环境 > 本地 .env.server（联调）。"""
    out: list[str] = []

    def add(value: str | None) -> None:
        s = (value or "").strip()
        if s and s not in out:
            out.append(s)

    add(preferred)
    add(os.environ.get("LICENSE_CRYPTO_SALT"))
    st = load_license_state()
    add(st.get("crypto_salt") if isinstance(st.get("crypto_salt"), str) else None)
    for path in (_ROOT / ".env.server", Path.cwd() / ".env.server"):
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("LICENSE_CRYPTO_SALT="):
                    add(line.split("=", 1)[1].strip().strip('"').strip("'"))
                    break
        except Exception:
            pass
        break
    add("boss-style-kb-v1")
    return out


def apply_config_blob(
    blob: str,
    license_key: str,
    device_id: str,
    *,
    salt: str | None = None,
) -> str | None:
    """解密并注入模型环境。成功时返回实际使用的 crypto_salt。"""
    if not blob:
        return salt
    last_err: Exception | None = None
    cfg: dict[str, Any] | None = None
    used_salt: str | None = None
    for cand in _salt_candidates(salt):
        try:
            cfg = decrypt_config(blob, license_key=license_key, device_id=device_id, salt=cand)
            used_salt = cand
            break
        except Exception as exc:
            last_err = exc
            continue
    if cfg is None:
        raise RuntimeError(_exc_text(last_err) if last_err else "模型配置解密失败") from last_err
    llm = cfg.get("llm") or {}
    jev = cfg.get("jev") or {}
    mapping = {
        "LLM_ENABLED": "true" if llm.get("enabled", True) else "false",
        "LLM_PROVIDER": str(llm.get("provider") or ""),
        "LLM_BASE_URL": str(llm.get("base_url") or ""),
        "LLM_MODEL": str(llm.get("model") or ""),
        "LLM_TIMEOUT": str(int(llm.get("timeout") or 30)),
        "LLM_MAX_TOKENS": str(int(llm.get("max_tokens") or 1600)),
        "LLM_TEMPERATURE": str(float(llm.get("temperature") or 0.3)),
        "LLM_API_KEY": str(llm.get("api_key") or ""),
        "OPENAI_API_KEY": str(llm.get("api_key") or ""),
        "OPENAI_BASE_URL": str(llm.get("base_url") or ""),
        "OPENAI_MODEL": str(llm.get("model") or ""),
        "JEV_ENABLED": "true" if jev.get("enabled", True) else "false",
        "JEV_PROVIDER": str(jev.get("provider") or "typesafe"),
        "JEV_BASE_URL": str(jev.get("base_url") or ""),
        "JEV_MODEL": str(jev.get("model") or "jev-latest"),
        "JEV_API_KEY": str(jev.get("api_key") or ""),
        "TYPESAFE_API_KEY": str(jev.get("api_key") or ""),
    }
    for k, v in mapping.items():
        if v is not None:
            os.environ[k] = v
    # 缓存脱敏视图供 UI
    cache_path = data_dir() / "remote_model_view.json"
    view = {
        "llm": {
            "enabled": llm.get("enabled", True),
            "provider": llm.get("provider"),
            "base_url": llm.get("base_url"),
            "model": llm.get("model"),
            "timeout": llm.get("timeout"),
            "max_tokens": llm.get("max_tokens"),
            "temperature": llm.get("temperature"),
            "api_key_configured": bool(llm.get("api_key")),
            "source": "server",
        },
        "jev": {
            "enabled": jev.get("enabled", True),
            "provider": jev.get("provider"),
            "base_url": jev.get("base_url"),
            "model": jev.get("model"),
            "api_key_configured": bool(jev.get("api_key")),
            "source": "server",
        },
        "announcement": cfg.get("announcement") or "",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    cache_path.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
    return used_salt


def remote_model_view() -> dict[str, Any]:
    path = data_dir() / "remote_model_view.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"llm": {"source": "server"}, "jev": {"source": "server"}}


def check_update(server_url: str | None = None) -> dict[str, Any]:
    st = load_license_state()
    base = (server_url or st.get("server_url") or "").rstrip("/")
    if not base:
        return {"has_update": False, "error": "未配置服务器"}
    try:
        return _get(base, f"/api/v1/client/version?current={CLIENT_VERSION}")
    except Exception as exc:
        return {"has_update": False, "error": str(exc)}


_update_prog_lock = threading.Lock()
_update_prog: dict[str, Any] = {
    "status": "idle",  # idle | downloading | verifying | launching | done | error
    "received": 0,
    "total": 0,
    "pct": 0,
    "human": "",
    "error": "",
}


def get_update_progress() -> dict[str, Any]:
    with _update_prog_lock:
        return dict(_update_prog)


def set_update_progress(**kwargs: Any) -> None:
    _set_update_progress(**kwargs)


def _set_update_progress(**kwargs: Any) -> None:
    with _update_prog_lock:
        explicit_pct = "pct" in kwargs
        _update_prog.update(kwargs)
        if explicit_pct:
            _update_prog["pct"] = max(0, min(100, int(_update_prog.get("pct") or 0)))
            return
        total = int(_update_prog.get("total") or 0)
        received = int(_update_prog.get("received") or 0)
        if total > 0:
            _update_prog["pct"] = max(0, min(100, int(received * 100 / total)))
        elif _update_prog.get("status") == "downloading":
            _update_prog["pct"] = 0


def download_update(
    url_path: str,
    dest: Path,
    server_url: str | None = None,
    *,
    expected_size: int | None = None,
) -> Path:
    st = load_license_state()
    base = (server_url or st.get("server_url") or "").rstrip("/")
    url = url_path if url_path.startswith("http") else base + url_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    expect = max(0, int(expected_size or 0))
    _set_update_progress(
        status="downloading",
        received=0,
        total=expect,
        pct=0,
        human="正在连接服务器…",
        error="",
    )
    req = urllib.request.Request(url, headers={"User-Agent": f"{PRODUCT_NAME}/{CLIENT_VERSION}"})
    received = 0
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0) or expect
            _set_update_progress(total=total, human="开始下载…")
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                if total > 0:
                    human = f"已下载 {received / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB"
                else:
                    human = f"已下载 {received / (1024 * 1024):.1f} MB"
                _set_update_progress(received=received, total=total, human=human)
    except Exception as exc:
        _set_update_progress(status="error", error=str(exc), human="下载失败")
        raise
    _set_update_progress(
        status="downloaded",
        received=received,
        pct=100,
        human="下载完成，准备安装…",
    )
    return dest


_hb_thread: threading.Thread | None = None
_hb_stop = threading.Event()
_lock_flag = False


def license_locked() -> bool:
    return _lock_flag


def set_locked(v: bool) -> None:
    global _lock_flag
    _lock_flag = v


def start_heartbeat_loop() -> None:
    global _hb_thread
    if _hb_thread and _hb_thread.is_alive():
        return
    _hb_stop.clear()

    def _run() -> None:
        while not _hb_stop.is_set():
            st = load_license_state()
            wait = max(60, int(st.get("heartbeat_sec") or 600))
            if not st.get("session_token"):
                if _hb_stop.wait(30):
                    break
                continue
            try:
                heartbeat()
                set_locked(False)
            except Exception:
                st = load_license_state()
                fails = int(st.get("fail_count") or 0) + 1
                st["fail_count"] = fails
                save_license_state(st)
                if fails >= 3:
                    set_locked(True)
            if _hb_stop.wait(wait):
                break

    _hb_thread = threading.Thread(target=_run, name="license-heartbeat", daemon=True)
    _hb_thread.start()


def stop_heartbeat_loop() -> None:
    _hb_stop.set()


def status_public() -> dict[str, Any]:
    st = load_license_state()
    return {
        "activated": is_activated(),
        "locked": license_locked(),
        "server_url": st.get("server_url") or "",
        "expires_at": st.get("expires_at") or "",
        "last_heartbeat": st.get("last_heartbeat") or "",
        "device_id": (st.get("device_id") or load_identity().get("device_id") or "")[:16],
        "version": CLIENT_VERSION,
        "fail_count": int(st.get("fail_count") or 0),
    }
