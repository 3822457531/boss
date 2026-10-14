"""启动本地管理台：仅 127.0.0.1（支持 PyInstaller exe）。"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _prepare_env() -> Path:
    if getattr(sys, "frozen", False):
        # 冻结后把项目根加入 path，并切到可写目录
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        if str(meipass) not in sys.path:
            sys.path.insert(0, str(meipass))
        os.environ.setdefault("BOSS_ADMIN_APPDATA", "1")
        from admin.paths import ROOT, ensure_runtime_dirs

        ensure_runtime_dirs()
        os.chdir(ROOT)
        return ROOT

    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)
    return root


def main() -> None:
    _prepare_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--license-server", default="", help="默认总控台地址（可写入激活页）")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("安全约束：只允许绑定 127.0.0.1 / localhost")

    if args.license_server.strip():
        os.environ["LICENSE_SERVER_DEFAULT"] = args.license_server.strip().rstrip("/")

    import uvicorn
    from admin.version import CLIENT_VERSION, PRODUCT_TITLE

    url = f"http://{args.host}:{args.port}/"
    print(f"{PRODUCT_TITLE} {CLIENT_VERSION}：{url}")

    if not args.no_browser:
        def _open() -> None:
            time.sleep(1.0)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    # 冻结时用对象导入，避免字符串模块路径问题
    if getattr(sys, "frozen", False):
        from admin.app import app as fastapi_app

        uvicorn.run(fastapi_app, host=args.host, port=args.port, reload=False)
    else:
        uvicorn.run("admin.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
