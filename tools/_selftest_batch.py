"""连发多条 → 合并统一回复 的自测。

不联网、不调用 LLM、不碰浏览器：只验证结构判定与多话题匹配这两处易错逻辑。
运行：python tools/_selftest_batch.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:  # 控制台默认 GBK，中文断言信息会乱码
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.auto_loop import _collect_pending_incoming  # noqa: E402
from src.judge import Judge  # noqa: E402
from src.policy import load_policy  # noqa: E402
import src.reply as R  # noqa: E402

_CHECKED: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _CHECKED.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")


def main() -> int:
    sections = R.parse_faq_sections(R.load_faq_text("config/knowledge/faq.md"))
    titles = [s["title"] for s in sections]
    print(f"— 事实库栏目：{titles}\n")

    # 1) 结构收集：我方上一条回复之后的全部候选人正文
    msgs = [
        {"role": "system", "text": "沟通的职位-PHP"},
        {"role": "recruiter", "text": "在的 还在招"},
        {"role": "candidate", "text": "工资如何"},
        {"role": "candidate", "text": "可以调休不？"},
    ]
    got = _collect_pending_incoming(msgs)
    check("1  连发两条被整批收集", got == ["工资如何", "可以调休不？"], str(got))

    msgs2 = [
        {"role": "candidate", "text": "旧的问题"},
        {"role": "recruiter", "text": "我方的回复"},
        {"role": "candidate", "text": "C"},
        {"role": "candidate", "text": "D"},
    ]
    got2 = _collect_pending_incoming(msgs2)
    check("1b 遇到我方回复即清空，只取最后一批", got2 == ["C", "D"], str(got2))

    check("1c 空会话返回空列表", _collect_pending_incoming([]) == [], "[]")

    # 2) 逐条命中：关键断言 —— 两个话题都要命中（天真拼接只会命中一个）
    t1 = R._heuristic_faq_topic("工资如何", titles, sections)
    t2 = R._heuristic_faq_topic("可以调休不？", titles, sections)
    check("2a 「工资如何」→ 薪资范围", t1[0] == "薪资范围", str(t1))
    check("2b 「可以调休不？」→ 休假", t2[0] == "休假", str(t2))

    # 3) 逐条匹配（stub 掉可能联网的 Jev 选题，只验逐条与去重逻辑）
    orig = R._pick_faq_topic
    R._pick_faq_topic = lambda q, secs: ("other", 0.0, "stub")  # type: ignore[assignment]
    try:
        matches = R._match_faq_topics(["工资如何", "可以调休不？"], sections)
        order = [m[1] for m in matches]
        check("3  逐条匹配、顺序与入参一致", order == ["薪资范围", "休假"], str(order))
        check("3b 去重保序", R._ordered_titles(matches) == ["薪资范围", "休假"], str(R._ordered_titles(matches)))
        dup = R._match_faq_topics(["工资多少", "底薪多少"], sections)
        check("3c 同一话题重复提问只保留一个栏目", R._ordered_titles(dup) == ["薪资范围"], str(R._ordered_titles(dup)))
    finally:
        R._pick_faq_topic = orig  # type: ignore[assignment]

    # 4) 待答清单渲染
    blk = R._render_ask_block(["工资如何", "可以调休不？"], "可以调休不？")
    check("4  清单列全两条", "1) 工资如何" in blk and "2) 可以调休不？" in blk, blk.replace("\n", " / "))

    # 5) 历史去重：批次不能既在 history 又在清单里
    hist = [
        {"role": "recruiter", "text": "在的 还在招"},
        {"role": "candidate", "text": "工资如何"},
        {"role": "candidate", "text": "可以调休不？"},
    ]
    turns = R._history_turns(hist, "可以调休不？", drop_tail=["工资如何", "可以调休不？"])
    check("5  传 drop_tail 时整批从历史剔除", turns == [("assistant", "在的 还在招")], str(turns))
    turns2 = R._history_turns(hist, "可以调休不？")
    check(
        "5b 不传 drop_tail 时维持原行为（只去末条）",
        turns2 == [("assistant", "在的 还在招"), ("user", "工资如何")],
        str(turns2),
    )

    # 6) 多话题事实块
    fb = R._facts_for_questions(["工资如何", "可以调休不？"], sections, "")
    check("6  事实块同时含两个栏目", "薪资范围" in fb and "休假" in fb, fb.replace("\n", " | ")[:110])

    # 7) judge 整批判断（关掉 Jev → 纯规则，不联网）
    policy = load_policy("config/policy.yaml")
    judge = Judge()
    judge.enabled = False

    jm = judge.evaluate_incoming(
        "可以调休不？", policy=policy, job={"title": "PHP"}, history=hist,
        batch=["工资如何", "可以调休不？"],
    )
    check("7  连发多条不走寒暄快路径", jm.get("engine") != "rule_opener", str(jm.get("engine")))

    jmix = judge.evaluate_incoming(
        "工资多少", policy=policy, job={"title": "PHP"}, history=hist,
        batch=["好的", "工资多少"],
    )
    check(
        "7b「好的」+提问 不被判成纯收悉",
        jmix.get("engine") != "rule_ack" and jmix.get("need_reply") is not False,
        f"engine={jmix.get('engine')} need_reply={jmix.get('need_reply')}",
    )

    msgs_batch_wechat = "加个微信吧"
    jw = judge.evaluate_incoming(
        "工资多少", policy=policy, job={"title": "PHP"}, history=hist,
        batch=["工资多少", msgs_batch_wechat],
    )
    check("7c 整批中任一条要微信即命中 wechat", jw.get("next_action") == "wechat", str(jw.get("next_action")))

    # 8) 单条路径零回归：不传 questions 时行为不变
    j1 = judge.evaluate_incoming("好的", policy=policy, job={"title": "PHP"}, history=hist)
    check("8  单条「好的」仍走纯收悉规则", j1.get("engine") == "rule_ack", str(j1.get("engine")))

    # 9) 集成：generate_reply 真的走「多话题」分支（stub 掉 LLM，走 light 兜底，不联网）
    orig_chat = R._llm_chat

    def _boom(*_a, **_k):
        raise RuntimeError("selftest: LLM disabled")

    R._llm_chat = _boom  # type: ignore[assignment]
    try:
        gen = R.generate_reply(
            "可以调休不？",
            job_id=None,
            policy_path="config/policy.yaml",
            intent="other",
            history=hist,
            questions=["工资如何", "可以调休不？"],
        )
        gen_single = R.generate_reply(
            "可以调休不？",
            job_id=None,
            policy_path="config/policy.yaml",
            intent="other",
            history=hist,
        )
    finally:
        R._llm_chat = orig_chat  # type: ignore[assignment]

    reason = str(gen.get("reason") or "")
    check("9  多话题路径被命中", reason.startswith("faq_multi:"), reason)
    check("9b 多话题草稿非空", bool((gen.get("draft") or "").strip()), (gen.get("draft") or "")[:60])
    single_reason = str(gen_single.get("reason") or "")
    check("9c 不传 questions 时仍走原单栏目路径", not single_reason.startswith("faq_multi:"), single_reason)

    print()
    bad = [n for n, ok in _CHECKED if not ok]
    print(f"结果：{len(_CHECKED) - len(bad)}/{len(_CHECKED)} 通过")
    if bad:
        print("未通过：" + "；".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
