"""总控台路径。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = Path(__file__).resolve().parent
STATIC_DIR = SERVER_DIR / "static"
DATA_DIR = ROOT / "data"
RELEASES_DIR = DATA_DIR / "releases"
SERVER_ENV = ROOT / ".env.server"

for _d in (DATA_DIR, RELEASES_DIR, STATIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)
