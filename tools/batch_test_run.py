#!/usr/bin/env python3
"""命令行批量测试话术。

用法：
  python tools/batch_test_run.py
  python tools/batch_test_run.py --file config/batch_test_phrases.txt
  python tools/batch_test_run.py --limit 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.batch_test import (  # noqa: E402
    RESULTS_DIR,
    load_phrases_file,
    load_phrases_text,
    run_one,
)
from src.judge import Judge  # noqa: E402
from admin.config_io import load_env_into_os, read_policy  # noqa: E402
from src.policy import get_job  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="批量话术测试（不调 boss-cli）")
    ap.add_argument("--file", type=Path, default=None, help="话术文件，默认 config/batch_test_phrases.txt")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条，0=全部")
    ap.add_argument("--job-title", type=str, default="", help="沟通职位标题")
    ap.add_argument("--out", type=Path, default=None, help="结果 JSON 路径")
    args = ap.parse_args()

    load_env_into_os()
    if args.file and args.file.exists():
        phrases = load_phrases_text(args.file.read_text(encoding="utf-8"))
    else:
        phrases = load_phrases_file(args.file)
    if args.limit and args.limit > 0:
        phrases = phrases[: args.limit]
    if not phrases:
        print("话术为空", file=sys.stderr)
        return 1

    policy = read_policy()
    job = get_job(policy, None)
    judge = Judge()
    results = []
    print(f"共 {len(phrases)} 条，开始…")
    try:
        for i, q in enumerate(phrases, start=1):
            print(f"[{i}/{len(phrases)}] Q: {q}")
            row = run_one(
                q,
                policy=policy,
                job=job,
                judge=judge,
                job_title=args.job_title or None,
            )
            row["index"] = i
            results.append(row)
            a = row.get("answer") or "(无正文)"
            print(f"         A: {a}")
            print(
                f"         outcome={row.get('outcome')} intent={row.get('intent')} "
                f"action={row.get('action') or row.get('next_action')} "
                f"{row.get('elapsed_ms')}ms"
            )
            print()
    finally:
        judge.close()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or (RESULTS_DIR / "cli_latest.json")
    payload = {"total": len(results), "results": results}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
