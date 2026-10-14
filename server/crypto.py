"""卡密派生 AES-GCM；模型配置加密封装。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_license_key() -> str:
    """BS-XXXX-XXXX-XXXX"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "BS-" + "-".join(parts)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


DEFAULT_CRYPTO_SALT = "boss-style-kb-v1"


def current_crypto_salt() -> str:
    """总控与客户机必须使用同一 salt；激活/心跳会把该值下发给客户机。"""
    return (os.environ.get("LICENSE_CRYPTO_SALT") or DEFAULT_CRYPTO_SALT).strip() or DEFAULT_CRYPTO_SALT


def _derive_key(license_key: str, device_id: str, *, salt: str | None = None) -> bytes:
    material = f"{license_key.strip().upper()}|{device_id.strip()}".encode("utf-8")
    salt_b = (salt or current_crypto_salt()).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", material, salt_b, 120_000, dklen=32)


def encrypt_config(
    payload: dict[str, Any],
    *,
    license_key: str,
    device_id: str,
    salt: str | None = None,
) -> str:
    key = _derive_key(license_key, device_id, salt=salt)
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = aes.encrypt(nonce, raw, None)
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_config(
    blob: str,
    *,
    license_key: str,
    device_id: str,
    salt: str | None = None,
) -> dict[str, Any]:
    data = base64.urlsafe_b64decode(blob.encode("ascii"))
    nonce, ct = data[:12], data[12:]
    key = _derive_key(license_key, device_id, salt=salt)
    aes = AESGCM(key)
    try:
        raw = aes.decrypt(nonce, ct, None)
    except Exception as exc:
        # cryptography.InvalidTag 的 str() 为空，必须换成可读错误
        raise ValueError(
            "模型配置解密失败：总控台与客户机加密盐不一致或配置包已损坏"
        ) from exc
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("配置包格式错误")
    return obj


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, hx = stored.split("$", 2)
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180_000)
        return secrets.compare_digest(digest.hex(), hx)
    except Exception:
        return False
