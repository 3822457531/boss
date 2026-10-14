"""演示前体检（只读）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
issues: list[str] = []
warns: list[str] = []


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def bad(msg: str) -> None:
    issues.append(msg)
    print(f" FAIL {msg}")


def warn(msg: str) -> None:
    warns.append(msg)
    print(f" WARN {msg}")


print("== 客户机剥离 ==")
idx = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
appjs = (ROOT / "admin/static/app.js").read_text(encoding="utf-8")
admin_py = (ROOT / "admin/app.py").read_text(encoding="utf-8")
if 'data-page="sim"' in idx or 'data-page="batch"' in idx:
    bad("客户机侧栏仍有模拟/批量")
else:
    ok("客户机侧栏无模拟/批量")
if 'data-goto="sim"' in appjs or 'data-goto="batch"' in appjs:
    bad("客户机仍有 data-goto 到模拟/批量")
else:
    ok("客户机无 sim/batch 快捷入口")
if re.search(r'/api/sim|/api/batch-test', admin_py):
    bad("admin/app.py 仍含 sim/batch 路由")
else:
    ok("客户机 API 已下线")
if "sim: render" in appjs or "batch: render" in appjs:
    bad("客户机 pages 仍注册 sim/batch")
else:
    ok("客户机 pages 无 sim/batch")
if "simAutoPersona:" in appjs or "function renderSim" in appjs:
    bad("客户机仍残留 renderSim/simAuto 代码")
else:
    ok("客户机静态残留已清理")

print("\n== 总控能力 ==")
sjs = (ROOT / "server/static/app.js").read_text(encoding="utf-8")
spy = (ROOT / "server/app.py").read_text(encoding="utf-8")
scss = (ROOT / "server/static/app.css").read_text(encoding="utf-8")
for need in ['data-page="sim"', 'data-page="batch"', "pageSim", "pageBatch"]:
    if need not in sjs:
        bad(f"总控前端缺 {need}")
    else:
        ok(f"总控前端有 {need}")
if "_apply_model_env_from_db" not in spy:
    bad("缺 _apply_model_env_from_db")
else:
    ok("有模型环境注入")
if ".sim-phone" not in scss or ".batch-qa" not in scss:
    bad("总控 CSS 缺 sim/batch 样式")
else:
    ok("总控 CSS 含 sim/batch")

from server.app import app
from admin.app import app as aapp

s_paths = {getattr(r, "path", None) for r in app.routes}
a_paths = {getattr(r, "path", None) for r in aapp.routes}
for p in (
    "/api/admin/sim/session",
    "/api/admin/sim/auto",
    "/api/admin/batch-test/start",
    "/api/admin/batch-test/status",
):
    if p not in s_paths:
        bad(f"总控缺路由 {p}")
    else:
        ok(f"总控路由 {p}")
for p in ("/api/sim/session", "/api/batch-test/status"):
    if p in a_paths:
        bad(f"客户机仍有 {p}")
    else:
        ok(f"客户机无 {p}")

print("\n== 引擎与配置 ==")
from src.sim_chat import PERSONAS, STORE  # noqa: F401
from src.batch_test import RUNNER  # noqa: F401

ok(f"personas={len(PERSONAS)}")
if not (ROOT / "config/policy.yaml").exists():
    bad("缺 policy.yaml")
else:
    ok("policy.yaml 存在")
if not (ROOT / "config/knowledge/faq.md").exists():
    warn("缺 faq.md，模拟回复可能偏空")
else:
    ok("faq.md 存在")

# load_env 是否会盖掉 DB 注入
from admin.config_io import load_env_into_os
import inspect

src = inspect.getsource(load_env_into_os)
if "os.environ[key] = value" in src and "startswith" in src:
    warn("sim/batch 内部仍会 load_env_into_os()，可能覆盖总控 DB 密钥（若 .env 与总控不一致）")

print("\n== 打包产物 ==")
dist_idx = ROOT / "dist/BossAdmin/_internal/admin/static/index.html"
if dist_idx.exists() and 'data-page="sim"' in dist_idx.read_text(encoding="utf-8"):
    warn("dist/BossAdmin 仍是旧包（含模拟聊天入口）；演示请用源码 run_admin，或重新打包")
else:
    ok("dist 无旧入口或未使用 dist")

print("\n== 模型密钥（总控 DB） ==")
try:
    from server import db

    db.init_db()
    cfg = db.get_model_config(include_secrets=True)
    llm_ok = bool((cfg.get("llm") or {}).get("api_key"))
    jev_ok = bool((cfg.get("jev") or {}).get("api_key"))
    if llm_ok:
        ok("LLM key 已配置")
    else:
        bad("总控未配置 LLM key — 演示模拟会失败")
    if jev_ok:
        ok("Jev key 已配置")
    else:
        warn("总控未配置 Jev key — 会降级启发式，演示效果变差")
except Exception as exc:
    warn(f"无法读总控模型配置：{exc}")

print("\n==== 汇总 ====")
print(f"阻断问题: {len(issues)}")
for i in issues:
    print(" -", i)
print(f"提醒: {len(warns)}")
for w in warns:
    print(" -", w)
if issues:
    raise SystemExit(1)
print("可以演示（请按下方清单走一遍冒烟）。")
