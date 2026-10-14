"""自动化入口（mock / 单轮）。默认 draft_only，不自动对平台发消息。"""

from __future__ import annotations

import argparse
import json

from src.policy import load_env, load_policy
from src.reply import generate_reply


def main() -> None:
    parser = argparse.ArgumentParser(description="Boss 自动回复（默认只出草稿）")
    parser.add_argument("--mock", action="store_true", help="不调 boss，只测配置与生成")
    parser.add_argument("--question", default="请问这个岗位双休吗？五险一金怎么交？")
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--job", default=None)
    args = parser.parse_args()

    load_env()
    policy = load_policy(args.policy)
    print(f"send_mode={policy.get('send_mode')}")
    print(f"intent_confidence_min={(policy.get('thresholds') or {}).get('intent_confidence_min')}")
    print(f"actions.not-fit={ (policy.get('actions') or {}).get('not-fit') } (必须为 False)")
    print(f"actions.wechat={ (policy.get('actions') or {}).get('wechat') } (true=允许 Jev 自动换微信)")
    print(
        f"action_confidence_min={(policy.get('thresholds') or {}).get('action_confidence_min')} "
        "(求简历/约面/要电话等)"
    )

    result = generate_reply(args.question, job_id=args.job, policy_path=args.policy)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.mock:
        print("提示：未加 --mock 时也不会自动发送；请在管理台草稿审核后再发。")


if __name__ == "__main__":
    main()
