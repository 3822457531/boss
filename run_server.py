"""启动卡密总控台（可绑定公网）。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="直聊小蜜总控台")
    parser.add_argument("--host", default=os.environ.get("LICENSE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LICENSE_PORT", "8787")))
    args = parser.parse_args()

    import uvicorn

    print(f"直聊小蜜总控：http://{args.host}:{args.port}/")
    print("请配置 LICENSE_MYSQL_DSN / LICENSE_ADMIN_PASSWORD（见 .env.server.example）")
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
