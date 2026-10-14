"""侧车 runtime（便携 Node + boss-cli）与本机 Chrome 探测。"""

from __future__ import annotations

import os
import shutil
import sys
import webbrowser
from pathlib import Path
from typing import Any

from admin.paths import BUNDLE_ROOT, ROOT

# 国内用户优先 .cn；失败时可改国际站
CHROME_DOWNLOAD_URL = "https://www.google.cn/chrome/"
CHROME_DOWNLOAD_URL_INTL = "https://www.google.com/chrome/"


def app_install_dir() -> Path:
    """安装目录：冻结=exe 同级；开发=仓库根。侧车 runtime 放这里。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def runtime_dir_candidates() -> list[Path]:
    """按优先级查找 runtime 目录。"""
    env = (os.environ.get("BOSS_RUNTIME_DIR") or "").strip()
    out: list[Path] = []
    if env:
        out.append(Path(env))
    install = app_install_dir()
    out.extend(
        [
            install / "runtime",
            BUNDLE_ROOT / "runtime",
            ROOT / "runtime",
            Path(__file__).resolve().parents[1] / "runtime",
        ]
    )
    # 去重保序
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def find_bundled_boss() -> tuple[Path, Path] | None:
    """返回 (node.exe, boss cli index.js)；找不到则 None。"""
    for base in runtime_dir_candidates():
        node = base / "node.exe"
        if not node.is_file():
            # 兼容 node 解压多一层目录
            nested = list(base.glob("node-v*-win-x64/node.exe"))
            if nested:
                node = nested[0]
                base = node.parent
        cli = base / "node_modules" / "@joohw" / "boss-cli" / "dist" / "cli" / "index.js"
        if node.is_file() and cli.is_file():
            return node.resolve(), cli.resolve()
    return None


def _chrome_candidates() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or "")
    pf = Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
    pf86 = Path(os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)")
    return [
        pf / "Google" / "Chrome" / "Application" / "chrome.exe",
        pf86 / "Google" / "Chrome" / "Application" / "chrome.exe",
        local / "Google" / "Chrome" / "Application" / "chrome.exe",
        pf / "Chromium" / "Application" / "chrome.exe",
    ]


def _chrome_from_registry() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore
    except ImportError:
        return None
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet\Google Chrome\shell\open\command"),
    ]
    for root, sub in keys:
        try:
            with winreg.OpenKey(root, sub) as key:
                val, _ = winreg.QueryValueEx(key, None)
                raw = str(val or "").strip().strip('"')
                # command 可能是 "C:\...\chrome.exe" -- %1
                if "chrome.exe" in raw.lower():
                    path = Path(raw.split(".exe")[0] + ".exe")
                    if path.is_file():
                        return path
        except OSError:
            continue
    return None


def find_chrome() -> Path | None:
    env = (os.environ.get("PUPPETEER_EXECUTABLE_PATH") or os.environ.get("CHROME_PATH") or "").strip()
    if env and Path(env).is_file():
        return Path(env)
    for p in _chrome_candidates():
        if p.is_file():
            return p
    return _chrome_from_registry()


def ensure_chrome_env() -> Path | None:
    """若找到 Chrome，写入 PUPPETEER_EXECUTABLE_PATH 供 boss-cli 使用。"""
    chrome = find_chrome()
    if chrome:
        os.environ.setdefault("PUPPETEER_EXECUTABLE_PATH", str(chrome))
        os.environ.setdefault("CHROME_PATH", str(chrome))
    return chrome


def probe_chrome() -> dict[str, Any]:
    chrome = find_chrome()
    return {
        "installed": chrome is not None,
        "path": str(chrome) if chrome else "",
        "download_url": CHROME_DOWNLOAD_URL,
        "download_url_intl": CHROME_DOWNLOAD_URL_INTL,
        "hint": "已检测到 Google Chrome" if chrome else "未检测到 Google Chrome，自动化需要 Chrome 才能打开招聘网页",
    }


def probe_boss_runtime() -> dict[str, Any]:
    bundled = find_bundled_boss()
    if bundled:
        node, cli = bundled
        return {
            "ok": True,
            "source": "bundled",
            "node": str(node),
            "cli": str(cli),
            "hint": "使用安装包内置自动化引擎（侧车 runtime）",
        }
    which = shutil.which("boss") or shutil.which("boss.cmd")
    if which:
        return {
            "ok": True,
            "source": "path",
            "node": "",
            "cli": which,
            "hint": f"使用系统 PATH 中的自动化引擎：{which}",
        }
    return {
        "ok": False,
        "source": "none",
        "node": "",
        "cli": "",
        "hint": "未找到内置 runtime。请重新安装直聊小蜜客户端，或设置 BOSS_RUNTIME_DIR。",
    }


def open_chrome_download(*, intl: bool = False) -> dict[str, Any]:
    url = CHROME_DOWNLOAD_URL_INTL if intl else CHROME_DOWNLOAD_URL
    webbrowser.open(url)
    return {"ok": True, "url": url, "human": "已打开 Chrome 下载页，安装完成后请重启直聊小蜜或刷新概览"}


def deps_status() -> dict[str, Any]:
    chrome = probe_chrome()
    boss = probe_boss_runtime()
    ready = bool(chrome.get("installed") and boss.get("ok"))
    return {
        "ready": ready,
        "chrome": chrome,
        "boss_cli": boss,
        "human": "依赖就绪" if ready else "请安装 Chrome，并确认内置自动化引擎可用",
    }
