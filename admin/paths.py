"""项目根路径与常用文件路径（支持 PyInstaller 冻结）。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from admin.version import PRODUCT_NAME


def _bundle_root() -> Path:
    """只读资源根：开发=仓库根；冻结=_MEIPASS。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def _writable_root() -> Path:
    """可写数据根：开发=仓库根；冻结=%LOCALAPPDATA%\\ZhiLiaoXiaoMi。"""
    if getattr(sys, "frozen", False) or os.environ.get("BOSS_ADMIN_APPDATA"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / PRODUCT_NAME
    return Path(__file__).resolve().parents[1]


BUNDLE_ROOT = _bundle_root()
ROOT = _writable_root()

ENV_PATH = ROOT / ".env"
POLICY_PATH = ROOT / "config" / "policy.yaml"
FAQ_PATH = ROOT / "config" / "knowledge" / "faq.md"
STYLE_PAIRS_PATH = ROOT / "data" / "style_pairs.jsonl"
RAW_HISTORY_PATH = ROOT / "data" / "raw_history.jsonl"
BACKUP_DIR = ROOT / "data" / "backups"
AUDIT_LOG = ROOT / "data" / "audit.log"
BOSS_LOG = ROOT / "data" / "boss_runs.jsonl"
MODEL_LOG = ROOT / "data" / "model_runs.jsonl"
DRAFTS_PATH = ROOT / "data" / "drafts.jsonl"
TASKS_DIR = ROOT / "data" / "tasks"


def ensure_runtime_dirs() -> None:
    """创建可写目录；首次从安装包拷贝默认 config。"""
    for d in (
        BACKUP_DIR,
        TASKS_DIR,
        ROOT / "data",
        ROOT / "config" / "knowledge",
        ROOT / "config" / "knowledge" / "positions",
    ):
        d.mkdir(parents=True, exist_ok=True)

    bundled_cfg = BUNDLE_ROOT / "config"
    # 缺啥补啥：升级后即使 policy 已存在，也要把新增的 synonyms.yaml 等拷入可写目录
    if bundled_cfg.exists():
        for src in bundled_cfg.rglob("*"):
            if src.is_file():
                rel = src.relative_to(bundled_cfg)
                dest = ROOT / "config" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy2(src, dest)

    if not ENV_PATH.exists():
        example = BUNDLE_ROOT / ".env.example"
        if example.exists():
            shutil.copy2(example, ENV_PATH)
        else:
            ENV_PATH.write_text("", encoding="utf-8")


ensure_runtime_dirs()
