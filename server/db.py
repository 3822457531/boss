"""MySQL 连接与建表 / CRUD。"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

from server.crypto import (
    current_crypto_salt,
    encrypt_config,
    generate_license_key,
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)

TZ = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(TZ)


def _now_s() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def parse_dsn(dsn: str | None = None) -> dict[str, Any]:
    """支持 mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4 或 mysql://..."""
    raw = (dsn or os.environ.get("LICENSE_MYSQL_DSN") or "").strip()
    if not raw:
        raise RuntimeError(
            "未配置 LICENSE_MYSQL_DSN，例如："
            "mysql+pymysql://root:pass@127.0.0.1:3306/boss_license?charset=utf8mb4"
        )
    raw = raw.replace("mysql+pymysql://", "mysql://", 1)
    if not raw.startswith("mysql://"):
        raw = "mysql://" + raw
    u = urlparse(raw)
    db = (u.path or "/").lstrip("/") or "boss_license"
    charset = "utf8mb4"
    if u.query:
        for part in u.query.split("&"):
            if part.startswith("charset="):
                charset = part.split("=", 1)[1] or charset
    return {
        "host": u.hostname or "127.0.0.1",
        "port": int(u.port or 3306),
        "user": unquote(u.username or "root"),
        "password": unquote(u.password or ""),
        "database": db,
        "charset": charset,
    }


@contextmanager
def connect(*, with_db: bool = True) -> Iterator[pymysql.Connection]:
    cfg = parse_dsn()
    kwargs = {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password": cfg["password"],
        "charset": cfg["charset"],
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if with_db:
        kwargs["database"] = cfg["database"]
    conn = pymysql.connect(**kwargs)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    cfg = parse_dsn()
    with connect(with_db=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg['database']}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_settings (
                  id TINYINT PRIMARY KEY DEFAULT 1,
                  password_hash VARCHAR(255) NOT NULL,
                  site_name VARCHAR(128) NOT NULL DEFAULT '直聊小蜜总控',
                  heartbeat_sec INT NOT NULL DEFAULT 600,
                  force_update TINYINT(1) NOT NULL DEFAULT 0,
                  announcement TEXT,
                  grace_hours INT NOT NULL DEFAULT 24,
                  updated_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS model_config (
                  id TINYINT PRIMARY KEY DEFAULT 1,
                  llm_json LONGTEXT NOT NULL,
                  jev_json LONGTEXT NOT NULL,
                  updated_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS licenses (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  license_key VARCHAR(64) NOT NULL UNIQUE,
                  note VARCHAR(255) DEFAULT '',
                  max_devices INT NOT NULL DEFAULT 1,
                  valid_days INT NOT NULL DEFAULT 365,
                  status VARCHAR(16) NOT NULL DEFAULT 'unused',
                  created_at DATETIME NOT NULL,
                  activated_at DATETIME NULL,
                  expires_at DATETIME NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS activations (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  license_id BIGINT NOT NULL,
                  device_id VARCHAR(64) NOT NULL,
                  machine_name VARCHAR(128) DEFAULT '',
                  session_hash VARCHAR(64) NOT NULL,
                  license_key_cache VARCHAR(64) NOT NULL,
                  ip VARCHAR(64) DEFAULT '',
                  status VARCHAR(16) NOT NULL DEFAULT 'active',
                  activated_at DATETIME NOT NULL,
                  last_heartbeat DATETIME NOT NULL,
                  UNIQUE KEY uk_lic_dev (license_id, device_id),
                  KEY idx_session (session_hash),
                  CONSTRAINT fk_act_lic FOREIGN KEY (license_id) REFERENCES licenses(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS releases (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  version VARCHAR(32) NOT NULL UNIQUE,
                  filename VARCHAR(255) NOT NULL,
                  sha256 VARCHAR(64) NOT NULL,
                  size_bytes BIGINT NOT NULL DEFAULT 0,
                  notes TEXT,
                  force_update TINYINT(1) NOT NULL DEFAULT 0,
                  is_latest TINYINT(1) NOT NULL DEFAULT 0,
                  created_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS server_audit (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  ts DATETIME NOT NULL,
                  actor VARCHAR(64) NOT NULL,
                  action VARCHAR(64) NOT NULL,
                  detail_json LONGTEXT
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute("SELECT id FROM admin_settings WHERE id=1")
            if not cur.fetchone():
                pwd = (os.environ.get("LICENSE_ADMIN_PASSWORD") or "").strip()
                if not pwd:
                    raise RuntimeError(
                        "未配置 LICENSE_ADMIN_PASSWORD：总控台首次初始化必须设置管理员口令"
                        "（参考 .env.server.example，勿使用弱口令）"
                    )
                cur.execute(
                    "INSERT INTO admin_settings (id, password_hash, updated_at) VALUES (1, %s, %s)",
                    (hash_password(pwd), _now_s()),
                )
            cur.execute("SELECT id FROM model_config WHERE id=1")
            if not cur.fetchone():
                llm = {
                    "enabled": True,
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "timeout": 30,
                    "max_tokens": 1600,
                    "temperature": 0.3,
                    "api_key": "",
                }
                jev = {
                    "enabled": True,
                    "provider": "typesafe",
                    "base_url": "https://api.typesafe.ai",
                    "model": "jev-1.13.0",
                    "api_key": "",
                }
                cur.execute(
                    "INSERT INTO model_config (id, llm_json, jev_json, updated_at) VALUES (1, %s, %s, %s)",
                    (json.dumps(llm, ensure_ascii=False), json.dumps(jev, ensure_ascii=False), _now_s()),
                )


def audit(actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO server_audit (ts, actor, action, detail_json) VALUES (%s,%s,%s,%s)",
                (_now_s(), actor, action, json.dumps(detail or {}, ensure_ascii=False)),
            )


def get_settings() -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admin_settings WHERE id=1")
            row = cur.fetchone() or {}
    return {
        "site_name": row.get("site_name") or "直聊小蜜总控",
        "heartbeat_sec": int(row.get("heartbeat_sec") or 600),
        "force_update": bool(row.get("force_update")),
        "announcement": row.get("announcement") or "",
        "grace_hours": int(row.get("grace_hours") or 24),
        "password_configured": bool(row.get("password_hash")),
    }


def update_settings(patch: dict[str, Any], *, new_password: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admin_settings WHERE id=1")
            row = cur.fetchone()
            if not row:
                raise RuntimeError("settings missing")
            site = patch.get("site_name", row["site_name"])
            hb = int(patch.get("heartbeat_sec", row["heartbeat_sec"]))
            fu = 1 if patch.get("force_update", row["force_update"]) else 0
            ann = patch.get("announcement", row.get("announcement") or "")
            grace = int(patch.get("grace_hours", row.get("grace_hours") or 24))
            pwd = row["password_hash"]
            if new_password and new_password.strip():
                pwd = hash_password(new_password.strip())
            cur.execute(
                """UPDATE admin_settings SET password_hash=%s, site_name=%s, heartbeat_sec=%s,
                   force_update=%s, announcement=%s, grace_hours=%s, updated_at=%s WHERE id=1""",
                (pwd, site, hb, fu, ann, grace, _now_s()),
            )
    return get_settings()


def verify_admin(password: str) -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM admin_settings WHERE id=1")
            row = cur.fetchone()
    if not row:
        return False
    return verify_password(password, row["password_hash"])


def get_model_config(*, include_secrets: bool = False) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT llm_json, jev_json FROM model_config WHERE id=1")
            row = cur.fetchone() or {}
    llm = json.loads(row.get("llm_json") or "{}")
    jev = json.loads(row.get("jev_json") or "{}")
    if not include_secrets:
        for block in (llm, jev):
            key = (block.get("api_key") or "").strip()
            block["api_key_configured"] = bool(key)
            if key:
                block["api_key_mask"] = f"****{key[-4:]}" if len(key) >= 4 else "****"
            block.pop("api_key", None)
    return {"llm": llm, "jev": jev}


def save_model_config(llm: dict[str, Any] | None, jev: dict[str, Any] | None) -> dict[str, Any]:
    current = get_model_config(include_secrets=True)
    next_llm = {**current["llm"], **(llm or {})}
    next_jev = {**current["jev"], **(jev or {})}
    # 掩码 / 空串：空串且非显式清除字段时保留原 key；
    # 显式 api_key="" 用于清除（调用方在 clear 接口传入）。
    for block, name in ((next_llm, "llm"), (next_jev, "jev")):
        if name == "llm" and llm is not None and "api_key" in llm:
            k = llm.get("api_key")
            if isinstance(k, str) and k.startswith("****"):
                block["api_key"] = current["llm"].get("api_key") or ""
            elif k is None:
                block["api_key"] = current["llm"].get("api_key") or ""
            else:
                block["api_key"] = k.strip()
        elif name == "jev" and jev is not None and "api_key" in jev:
            k = jev.get("api_key")
            if isinstance(k, str) and k.startswith("****"):
                block["api_key"] = current["jev"].get("api_key") or ""
            elif k is None:
                block["api_key"] = current["jev"].get("api_key") or ""
            else:
                block["api_key"] = k.strip()
        else:
            k = block.get("api_key")
            if isinstance(k, str) and (not k.strip() or k.startswith("****")):
                block["api_key"] = current[name].get("api_key") or ""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE model_config SET llm_json=%s, jev_json=%s, updated_at=%s WHERE id=1",
                (
                    json.dumps(next_llm, ensure_ascii=False),
                    json.dumps(next_jev, ensure_ascii=False),
                    _now_s(),
                ),
            )
    return get_model_config(include_secrets=False)


def import_model_config_from_env_file(env_path: Path | None = None, *, only_if_empty: bool = True) -> bool:
    """从客户机 .env 导入 LLM/Jev 到总控（沿用同一套 key）。"""
    path = env_path or (Path(__file__).resolve().parents[1] / ".env")
    if not path.is_file():
        return False
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    current = get_model_config(include_secrets=True)
    if only_if_empty and (current["llm"].get("api_key") or "").strip() and (current["jev"].get("api_key") or "").strip():
        return False

    def _bool(raw: str | None, default: bool = True) -> bool:
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    llm_patch: dict[str, Any] = {
        "enabled": _bool(env.get("LLM_ENABLED"), True),
        "provider": env.get("LLM_PROVIDER") or current["llm"].get("provider") or "deepseek",
        "base_url": env.get("LLM_BASE_URL") or env.get("OPENAI_BASE_URL") or current["llm"].get("base_url") or "",
        "model": env.get("LLM_MODEL") or env.get("OPENAI_MODEL") or current["llm"].get("model") or "",
        "timeout": int(env.get("LLM_TIMEOUT") or current["llm"].get("timeout") or 30),
        "max_tokens": int(env.get("LLM_MAX_TOKENS") or current["llm"].get("max_tokens") or 1600),
        "temperature": float(env.get("LLM_TEMPERATURE") or current["llm"].get("temperature") or 0.3),
    }
    jev_patch: dict[str, Any] = {
        "enabled": _bool(env.get("JEV_ENABLED"), True),
        "provider": env.get("JEV_PROVIDER") or current["jev"].get("provider") or "typesafe",
        "base_url": env.get("JEV_BASE_URL") or current["jev"].get("base_url") or "",
        "model": env.get("JEV_MODEL") or current["jev"].get("model") or "jev-latest",
    }
    llm_key = (env.get("LLM_API_KEY") or env.get("OPENAI_API_KEY") or "").strip()
    jev_key = (env.get("JEV_API_KEY") or env.get("TYPESAFE_API_KEY") or "").strip()
    if llm_key:
        llm_patch["api_key"] = llm_key
    if jev_key:
        jev_patch["api_key"] = jev_key
    if not llm_key and not jev_key:
        return False
    save_model_config(llm_patch, jev_patch)
    audit("admin", "models_import_env", {"path": str(path), "llm": bool(llm_key), "jev": bool(jev_key)})
    return True


def create_licenses(*, count: int, note: str, max_devices: int, valid_days: int) -> list[dict[str, Any]]:
    count = max(1, min(200, int(count)))
    max_devices = max(1, min(20, int(max_devices)))
    valid_days = max(1, min(3650, int(valid_days)))
    created: list[dict[str, Any]] = []
    with connect() as conn:
        with conn.cursor() as cur:
            for _ in range(count):
                for _try in range(8):
                    key = generate_license_key()
                    try:
                        cur.execute(
                            """INSERT INTO licenses (license_key, note, max_devices, valid_days, status, created_at)
                               VALUES (%s,%s,%s,%s,'unused',%s)""",
                            (key, note or "", max_devices, valid_days, _now_s()),
                        )
                        created.append(
                            {
                                "license_key": key,
                                "note": note or "",
                                "max_devices": max_devices,
                                "valid_days": valid_days,
                                "status": "unused",
                            }
                        )
                        break
                    except pymysql.err.IntegrityError:
                        continue
    audit("admin", "license_create", {"count": len(created), "note": note})
    return created


def list_licenses(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM licenses ORDER BY id DESC LIMIT %s",
                (max(1, min(1000, limit)),),
            )
            rows = cur.fetchall() or []
    return [_license_row(r) for r in rows]


def revoke_license(license_id: int) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE licenses SET status='revoked' WHERE id=%s", (license_id,))
            cur.execute(
                "UPDATE activations SET status='revoked' WHERE license_id=%s",
                (license_id,),
            )
    audit("admin", "license_revoke", {"id": license_id})


def list_activations(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.*, l.license_key, l.note, l.expires_at AS lic_expires
                   FROM activations a JOIN licenses l ON l.id=a.license_id
                   ORDER BY a.last_heartbeat DESC LIMIT %s""",
                (max(1, min(1000, limit)),),
            )
            rows = cur.fetchall() or []
    out = []
    for r in rows:
        key = r.get("license_key") or ""
        out.append(
            {
                "id": r["id"],
                "license_id": r["license_id"],
                "license_key_mask": _mask_key(key),
                "note": r.get("note") or "",
                "device_id": r["device_id"],
                "machine_name": r.get("machine_name") or "",
                "ip": r.get("ip") or "",
                "status": r["status"],
                "activated_at": _fmt(r.get("activated_at")),
                "last_heartbeat": _fmt(r.get("last_heartbeat")),
                "expires_at": _fmt(r.get("lic_expires")),
            }
        )
    return out


def revoke_activation(activation_id: int) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE activations SET status='revoked' WHERE id=%s", (activation_id,))
    audit("admin", "activation_revoke", {"id": activation_id})


def activate_client(
    *,
    license_key: str,
    device_id: str,
    machine_name: str,
    ip: str,
) -> dict[str, Any]:
    key = (license_key or "").strip().upper()
    device_id = (device_id or "").strip()
    if not key or not device_id:
        raise ValueError("卡密与设备 ID 必填")
    settings = get_settings()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM licenses WHERE license_key=%s FOR UPDATE", (key,))
            lic = cur.fetchone()
            if not lic:
                audit("client", "activate_fail", {"reason": "not_found", "ip": ip})
                raise PermissionError("卡密无效")
            if lic["status"] == "revoked":
                raise PermissionError("卡密已吊销")
            if lic["status"] == "expired":
                raise PermissionError("卡密已过期")

            now = _now()
            expires = lic.get("expires_at")
            if expires and _as_dt(expires) < now:
                cur.execute("UPDATE licenses SET status='expired' WHERE id=%s", (lic["id"],))
                raise PermissionError("卡密已过期")

            cur.execute(
                "SELECT * FROM activations WHERE license_id=%s AND status='active'",
                (lic["id"],),
            )
            actives = cur.fetchall() or []
            existing = next((a for a in actives if a["device_id"] == device_id), None)
            if not existing and len(actives) >= int(lic["max_devices"]):
                raise PermissionError("已达设备绑定上限")

            session = new_session_token()
            sh = hash_token(session)
            if lic["status"] == "unused":
                exp = now + timedelta(days=int(lic["valid_days"]))
                cur.execute(
                    """UPDATE licenses SET status='active', activated_at=%s, expires_at=%s WHERE id=%s""",
                    (_now_s(), exp.strftime("%Y-%m-%d %H:%M:%S"), lic["id"]),
                )
                expires = exp

            if existing:
                cur.execute(
                    """UPDATE activations SET session_hash=%s, machine_name=%s, ip=%s,
                       status='active', last_heartbeat=%s, license_key_cache=%s WHERE id=%s""",
                    (sh, machine_name or "", ip, _now_s(), key, existing["id"]),
                )
            else:
                cur.execute(
                    """INSERT INTO activations
                       (license_id, device_id, machine_name, session_hash, license_key_cache, ip, status, activated_at, last_heartbeat)
                       VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s)""",
                    (lic["id"], device_id, machine_name or "", sh, key, ip, _now_s(), _now_s()),
                )

            cur.execute("SELECT expires_at FROM licenses WHERE id=%s", (lic["id"],))
            expires = cur.fetchone()["expires_at"]

    blob = _build_config_blob(key, device_id)
    audit("client", "activate_ok", {"device_id": device_id, "ip": ip, "key": _mask_key(key)})
    return {
        "session_token": session,
        "expires_at": _fmt(expires),
        "heartbeat_sec": settings["heartbeat_sec"],
        "announcement": settings["announcement"],
        "config_blob": blob,
        "license_key": key,
        "crypto_salt": current_crypto_salt(),
    }


def heartbeat_client(*, session_token: str, device_id: str, ip: str) -> dict[str, Any]:
    sh = hash_token(session_token)
    settings = get_settings()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.*, l.license_key, l.status AS lic_status, l.expires_at
                   FROM activations a JOIN licenses l ON l.id=a.license_id
                   WHERE a.session_hash=%s AND a.device_id=%s FOR UPDATE""",
                (sh, device_id),
            )
            row = cur.fetchone()
            if not row or row["status"] != "active":
                raise PermissionError("会话无效，请重新激活")
            if row["lic_status"] in {"revoked", "expired"}:
                raise PermissionError("卡密已失效")
            if row.get("expires_at") and _as_dt(row["expires_at"]) < _now():
                cur.execute("UPDATE licenses SET status='expired' WHERE id=%s", (row["license_id"],))
                raise PermissionError("卡密已过期")
            cur.execute(
                "UPDATE activations SET last_heartbeat=%s, ip=%s WHERE id=%s",
                (_now_s(), ip, row["id"]),
            )
            key = row.get("license_key_cache") or row.get("license_key") or ""
    blob = _build_config_blob(key, device_id)
    return {
        "ok": True,
        "expires_at": _fmt(row.get("expires_at")),
        "heartbeat_sec": settings["heartbeat_sec"],
        "announcement": settings["announcement"],
        "config_blob": blob,
        "license_key": key,
        "crypto_salt": current_crypto_salt(),
    }


def deactivate_client(*, session_token: str, device_id: str) -> None:
    sh = hash_token(session_token)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE activations SET status='revoked' WHERE session_hash=%s AND device_id=%s",
                (sh, device_id),
            )
    audit("client", "deactivate", {"device_id": device_id})


def list_audit(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM server_audit ORDER BY id DESC LIMIT %s",
                (max(1, min(500, limit)),),
            )
            rows = cur.fetchall() or []
    return [
        {
            "id": r["id"],
            "ts": _fmt(r.get("ts")),
            "actor": r["actor"],
            "action": r["action"],
            "detail": json.loads(r.get("detail_json") or "{}"),
        }
        for r in rows
    ]


def list_releases() -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM releases ORDER BY id DESC LIMIT 50")
            rows = cur.fetchall() or []
    return [_release_row(r) for r in rows]


def get_latest_release() -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM releases WHERE is_latest=1 ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT * FROM releases ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
    return _release_row(row) if row else None


def publish_release(
    *,
    version: str,
    filename: str,
    sha256: str,
    size_bytes: int,
    notes: str,
    force_update: bool,
    set_latest: bool = True,
) -> dict[str, Any]:
    version = _normalize_version(version)
    with connect() as conn:
        with conn.cursor() as cur:
            if set_latest:
                cur.execute("UPDATE releases SET is_latest=0")
            cur.execute(
                """INSERT INTO releases (version, filename, sha256, size_bytes, notes, force_update, is_latest, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE filename=VALUES(filename), sha256=VALUES(sha256),
                     size_bytes=VALUES(size_bytes), notes=VALUES(notes), force_update=VALUES(force_update),
                     is_latest=VALUES(is_latest)""",
                (
                    version,
                    filename,
                    sha256,
                    size_bytes,
                    notes or "",
                    1 if force_update else 0,
                    1 if set_latest else 0,
                    _now_s(),
                ),
            )
    audit("admin", "release_publish", {"version": version, "force": force_update})
    latest = get_latest_release()
    assert latest
    return latest


def _build_config_blob(license_key: str, device_id: str) -> str:
    cfg = get_model_config(include_secrets=True)
    settings = get_settings()
    payload = {
        "llm": cfg["llm"],
        "jev": cfg["jev"],
        "heartbeat_sec": settings["heartbeat_sec"],
        "announcement": settings["announcement"],
        "issued_at": _now_s(),
    }
    return encrypt_config(payload, license_key=license_key, device_id=device_id)


def _license_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "license_key": r["license_key"],
        "note": r.get("note") or "",
        "max_devices": r["max_devices"],
        "valid_days": r["valid_days"],
        "status": r["status"],
        "created_at": _fmt(r.get("created_at")),
        "activated_at": _fmt(r.get("activated_at")),
        "expires_at": _fmt(r.get("expires_at")),
    }


def _release_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "version": r["version"],
        "filename": r["filename"],
        "sha256": r["sha256"],
        "size_bytes": int(r.get("size_bytes") or 0),
        "notes": r.get("notes") or "",
        "force_update": bool(r.get("force_update")),
        "is_latest": bool(r.get("is_latest")),
        "created_at": _fmt(r.get("created_at")),
    }


def _mask_key(key: str) -> str:
    k = key or ""
    if len(k) < 8:
        return "****"
    return k[:3] + "****" + k[-4:]


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return v.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def _as_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=TZ)
        return v
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)


def _normalize_version(v: str) -> str:
    v = (v or "").strip().lstrip("vV")
    if not re.match(r"^\d+\.\d+(\.\d+)?$", v):
        raise ValueError("版本号格式应为 x.y.z")
    return v


def compare_version(a: str, b: str) -> int:
    """比较版本：a>b → 1；a=b → 0；a<b → -1。"""
    def parts(x: str) -> list[int]:
        x = _normalize_version(x) if re.match(r"^\d", x or "") else "0.0.0"
        nums = [int(p) for p in x.split(".")]
        while len(nums) < 3:
            nums.append(0)
        return nums
    pa, pb = parts(a), parts(b)
    return (pa > pb) - (pa < pb)
