"""从历史聊天记录构建风格层知识库（L2）。

两个子命令：

    # 1. 采集：遍历会话，把原始沟通记录 dump 成 JSONL
    python -m tools.collect_history collect --limit 100

    # 2. 构建：挖配对 → Jev 质量闸门 → Jev 意图打标 → 去重 → style_pairs.jsonl
    python -m tools.collect_history build

⚠️ 已按 @joohw/boss-cli 源码校准（见下方 FIELD CALIBRATION）。
   真正的「双方聊天正文」来自 `boss chat <姓名>`，不是 `boss action history`。

字段校准结论（源码 chat.js / list.js）：
  - list 行：`{name, job, time, message, unreadCount}` → CLI 打印成
    `1. 姓名｜职位｜未读:N｜时间:…｜消息:…`
  - chat 消息内部：`{text, time, from}`，其中 from ∈ {friend, myself, system}
  - CLI 把 from 渲染成标签：friend→[candidate]，myself→[you]，system→[system]
    行格式：`[candidate] <time?> <text>`
  - `boss action history` 只读「同事沟通/我的沟通」弹窗的 action/operat，
    **不是**聊天消息，不能用来建风格库。

采集不到字段时会**直接抛错**，不会静默产出空知识库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.judge import Judge  # noqa: E402
from src.knowledge import QAPair, dedupe, extract_pairs, save_pairs  # noqa: E402
from src.policy import get_job, load_env, load_policy  # noqa: E402

# ---------------------------------------------------------------------------
# FIELD CALIBRATION — 已对齐 boss-cli dist/toolset/{chat,list}.js
# ---------------------------------------------------------------------------
# 内部 scrape 字段（若将来 CLI 提供 JSON）：
_FROM_KEYS = ("from",)  # friend | myself | system
_TEXT_KEYS = ("text",)
_TIME_KEYS = ("time",)
_NAME_KEYS = ("name",)  # list 行 / 人才摘要

# CLI 文本标签 → 风格库角色
_TAG_TO_ROLE = {
    "candidate": "candidate",  # from=friend
    "you": "recruiter",  # from=myself
    "system": "system",
    "unknown": "system",
}
_JSON_FROM_TO_ROLE = {
    "friend": "candidate",
    "myself": "recruiter",
    "system": "system",
}

# boss list 行：`1. 姓名｜职位｜…`
_LIST_ROW_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$")
# boss chat 消息行：`[candidate] 10:30 你好` / `[you] 昨天 15:20 您好`
_MSG_LINE_RE = re.compile(r"^\[(candidate|you|system|unknown)\]\s+(.*)$")
# 尽量剥离行首时间（Boss DOM `.message-time .time` 常见形态）
_TIME_PREFIX_RE = re.compile(
    r"^(?:"
    r"刚刚|"
    r"前天(?:\s+\d{1,2}:\d{2})?|"
    r"昨天(?:\s+\d{1,2}:\d{2})?|"
    r"\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?|"
    r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?|"
    r"\d{1,2}:\d{2}"
    r")\s+"
)


def _pick(row: dict[str, Any], keys: tuple[str, ...], what: str) -> Any:
    """按候选键取值，取不到直接报错。"""
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    raise KeyError(f"找不到{what}：候选键 {keys} 都不在 {sorted(row)} 里。请用 collect --dump-raw 校准。")


def _npm_global_prefixes() -> list[Path]:
    """猜测 npm 全局前缀（Windows 常见 prefix/node_cache）。"""
    prefixes: list[Path] = []
    env_prefix = (os.environ.get("NPM_CONFIG_PREFIX") or os.environ.get("npm_config_prefix") or "").strip()
    if env_prefix:
        prefixes.append(Path(env_prefix))
    # 用户机器上 boss 实际装在 D:\\node\\node_cache
    prefixes.extend(
        [
            Path(r"D:\node\node_cache"),
            Path.home() / "AppData" / "Roaming" / "npm",
            Path(r"C:\Program Files\nodejs"),
        ]
    )
    which_npm = shutil.which("npm")
    if which_npm:
        prefixes.append(Path(which_npm).resolve().parent)
    # 去重保序
    seen: set[str] = set()
    out: list[Path] = []
    for p in prefixes:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


@lru_cache(maxsize=8)
def _resolve_boss_argv(boss_bin: str) -> tuple[str, ...]:
    """解析 boss 可执行入口，返回可直接传给 subprocess 的 argv 前缀。

    Windows 上 npm 全局命令是 ``boss.cmd``，``CreateProcess`` 直接找 ``boss`` 会
    WinError 2；因此优先落到 ``node …/boss-cli/dist/cli/index.js``，其次 ``cmd /c boss.cmd``。
    """
    override = (os.environ.get("BOSS_BIN") or "").strip()
    raw = override or boss_bin or "boss"
    path = Path(raw)

    def from_file(file_path: Path) -> tuple[str, ...]:
        suffix = file_path.suffix.lower()
        if suffix == ".js":
            node = shutil.which("node") or "node"
            return (node, str(file_path))
        if suffix in {".cmd", ".bat"}:
            return ("cmd", "/c", str(file_path))
        return (str(file_path),)

    if path.is_file():
        return from_file(path.resolve())

    # PATH：boss / boss.cmd / boss.exe
    for name in (raw, f"{raw}.cmd", f"{raw}.exe", f"{raw}.bat"):
        found = shutil.which(name)
        if found:
            return from_file(Path(found))

    # 直接找 @joohw/boss-cli 入口脚本（最稳）
    for prefix in _npm_global_prefixes():
        cli_js = prefix / "node_modules" / "@joohw" / "boss-cli" / "dist" / "cli" / "index.js"
        if cli_js.is_file():
            node = shutil.which("node") or "node"
            return (node, str(cli_js))
        shim = prefix / "boss.cmd"
        if shim.is_file():
            return from_file(shim)

    raise FileNotFoundError(
        "找不到 boss-cli 可执行文件。请任选其一：\n"
        "  1) 把 npm 全局 bin 加进 PATH（本机常见：D:\\node\\node_cache）\n"
        "  2) 设置环境变量 BOSS_BIN=D:\\node\\node_cache\\boss.cmd\n"
        "  3) 传参 --boss-bin D:\\node\\node_cache\\boss.cmd\n"
        "  4) npm i -g @joohw/boss-cli"
    )


def _boss(args: list[str], boss_bin: str = "boss", timeout: float = 180.0) -> str:
    """执行一条 boss 命令，返回 stdout。"""
    argv = [*_resolve_boss_argv(boss_bin), *args]
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()[:400]
        raise RuntimeError(f"{' '.join(argv)} 退出码 {completed.returncode}：{err}")
    return completed.stdout


def parse_list_names(raw: str) -> list[str]:
    """从 `boss list` 文本解析候选人姓名（去重、保序）。"""
    names: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        match = _LIST_ROW_RE.match(line.strip())
        if not match:
            continue
        body = match.group(2).strip()
        # 第一段是姓名，后面用全角/半角竖线分隔职位与元数据
        name = re.split(r"[｜|]", body, maxsplit=1)[0].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def parse_list_header_count(raw: str) -> int | None:
    """解析 list 抬头人数，如「未读筛选：共 40 人」或「沟通列表共 40 人」。"""
    text = raw or ""
    for pat in (
        r"未读筛选[：:]\s*共\s*(\d+)\s*人",
        r"沟通列表共\s*(\d+)\s*人",
        r"共\s*(\d+)\s*人（已切换页面「未读」筛选）",
    ):
        m = re.search(pat, text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def is_bogus_full_page_unread(
    raw: str,
    names: list[str],
    *,
    page_cap: int = 40,
) -> bool:
    """未读刚好顶满一页（常见恰好 40）时，多半是筛选失效扫到的沟通列表，不可信。"""
    cap = max(1, int(page_cap or 40))
    header_n = parse_list_header_count(raw)
    # Boss 沟通列表一页通常 40：抬头或解析人数刚好等于上限 → 假未读
    if header_n is not None and header_n == cap:
        return True
    if len(names) == cap:
        return True
    return False


def parse_chat_meta(raw: str) -> dict[str, str]:
    """从 `boss chat` 输出解析人才摘要字段（沟通职位、简历获取状态等）。"""
    meta: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for key, label in (
            ("communication_position", "沟通职位"),
            ("name", "姓名"),
            ("active", "活跃状态"),
            ("expectation", "期望"),
            ("resume_status", "简历获取状态"),
        ):
            prefix = f"{label}:"
            prefix2 = f"{label}："
            if line.startswith(prefix) or line.startswith(prefix2):
                val = line.split(":", 1)[-1] if ":" in line else line.split("：", 1)[-1]
                meta[key] = val.strip()
                break
    return meta


def _split_time_and_text(rest: str) -> tuple[str, str]:
    """从 `[tag] <rest>` 的 rest 里尽量剥时间；剥不掉则整段当正文。"""
    rest = rest.strip()
    match = _TIME_PREFIX_RE.match(rest)
    if not match:
        return "", rest
    return match.group(0).strip(), rest[match.end() :].strip()


def parse_chat_messages(raw: str) -> list[dict[str, str]]:
    """解析 `boss chat` 输出里的「完整聊天消息」段落。

    优先走 CLI 文本标签；若整段是 JSON（将来扩展），走 from/text/time。
    """
    raw = raw.strip()
    if not raw:
        return []

    # 兼容：若 CLI 某天开始直接吐 JSON 数组
    if raw.startswith("[") or raw.startswith("{"):
        try:
            payload = json.loads(raw)
            rows: list[dict[str, Any]]
            if isinstance(payload, list):
                rows = [r for r in payload if isinstance(r, dict)]
            elif isinstance(payload, dict):
                nested = payload.get("messages")
                rows = nested if isinstance(nested, list) else []
            else:
                rows = []
            if rows and any(k in rows[0] for k in _FROM_KEYS):
                return _normalize_json_rows(rows)
        except json.JSONDecodeError:
            pass

    # 定位「完整聊天消息：」之后；找不到则扫描全文带标签的行
    section = raw
    marker = "完整聊天消息："
    idx = raw.find(marker)
    if idx >= 0:
        section = raw[idx + len(marker) :]

    messages: list[dict[str, str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line or line == "(暂无)":
            continue
        match = _MSG_LINE_RE.match(line)
        if not match:
            continue
        tag, rest = match.group(1), match.group(2)
        role = _TAG_TO_ROLE.get(tag)
        if role is None:
            raise ValueError(f"无法识别的消息标签 [{tag}]，请更新 _TAG_TO_ROLE。")
        time_str, text = _split_time_and_text(rest)
        if not text:
            continue
        item = {"role": role, "text": text}
        if time_str:
            item["time"] = time_str
        messages.append(item)

    if not messages and marker in raw:
        # 明确有段落但无消息：允许空列表（上层决定 skip）
        return []
    if not messages:
        raise ValueError(
            "无法从 boss chat 输出解析到 [candidate]/[you] 消息行。"
            "请用 collect --dump-raw 查看真实格式。"
        )
    return messages


def _normalize_json_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """把 scrape JSON `{from,text,time}` 规整成 {role,text[,time]}。"""
    messages: list[dict[str, str]] = []
    for row in rows:
        raw_from = str(_pick(row, _FROM_KEYS, "消息来源 from")).strip().lower()
        if raw_from not in _JSON_FROM_TO_ROLE:
            raise ValueError(
                f"无法识别的 from={raw_from!r}，期望 {sorted(_JSON_FROM_TO_ROLE)}。"
                "请用 collect --dump-raw 校准。"
            )
        text = str(_pick(row, _TEXT_KEYS, "消息正文 text")).strip()
        if not text:
            continue
        item = {"role": _JSON_FROM_TO_ROLE[raw_from], "text": text}
        for key in _TIME_KEYS:
            if key in row and row[key] not in (None, ""):
                item["time"] = str(row[key]).strip()
                break
        messages.append(item)
    return messages


def collect(args: argparse.Namespace) -> None:
    """采集子命令：遍历会话，把聊天正文写入 JSONL。"""
    if args.dump_raw:
        listing = _boss(["list"], args.boss_bin)
        print("=== boss list（原始）===")
        print(listing[:4000])
        names = parse_list_names(listing)
        print("\n=== 解析到的姓名 ===")
        print(json.dumps(names[:20], ensure_ascii=False, indent=2))

        print("\n" + "=" * 72)
        print(
            "说明：`boss action history` 读的是「同事/我的沟通」活动日志"
            "（action/operat），不是聊天正文；风格库请用 `boss chat`。\n"
        )
        try:
            history_raw = _boss(["action", "history"], args.boss_bin)
            print("=== boss action history（对照，勿当聊天）===")
            print(history_raw[:3000])
        except Exception as exc:
            print(f"（跳过 action history：{exc}）")

        sample_name = names[0] if names else None
        if sample_name:
            print("\n" + "=" * 72)
            print(f"=== boss chat {sample_name!r}（原始）===")
            chat_raw = _boss(["chat", sample_name], args.boss_bin)
            print(chat_raw[:8000])
            print("\n=== 解析后的 messages（校准用）===")
            try:
                parsed = parse_chat_messages(chat_raw)
                print(json.dumps(parsed[:30], ensure_ascii=False, indent=2))
                print(f"\n共解析 {len(parsed)} 条；角色映射：friend→candidate, myself→recruiter")
            except Exception as exc:
                print(f"解析失败：{exc}")
        else:
            print("\nlist 为空，无法 dump chat。请先 boss login 并打开沟通列表页。")
        return

    listing = _boss(["list"], args.boss_bin)
    names = parse_list_names(listing)
    if not names:
        raise SystemExit("boss list 没有解析到任何会话。请确认已登录且处于聊天列表页。")

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped: list[str] = []
    with open(target, "a", encoding="utf-8") as handle:
        for name in names[: args.limit]:
            try:
                chat_raw = _boss(["chat", name], args.boss_bin)
                rows = parse_chat_messages(chat_raw)
                if not rows:
                    skipped.append(name)
                    print(f"  · {name}：无消息，跳过")
                    continue
                # 过滤 system，风格配对只关心 candidate/recruiter；保留原序列供审计
                handle.write(
                    json.dumps(
                        {
                            "candidate": name,
                            "date": date.today().isoformat(),
                            "messages": rows,
                            "raw_preview": chat_raw[:500],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1
                print(f"  ✓ {name}（{len(rows)} 条消息）")
            except Exception as exc:  # 单个会话失败不阻塞整轮采集
                skipped.append(f"{name}: {exc}")
                print(f"  ✗ {name}：{exc}")
    print(f"\n采集完成：写入 {written} 个会话 → {target}")
    if skipped:
        print(f"跳过 {len(skipped)} 个：")
        for item in skipped[:10]:
            print(f"  · {item}")


def build(args: argparse.Namespace) -> None:
    """构建子命令：挖配对 → 质量闸门 → 意图打标 → 去重 → 落盘。"""
    load_env()
    policy = load_policy(args.policy)
    job = get_job(policy, args.job)
    source = Path(args.source)
    if not source.exists():
        raise SystemExit(f"原始历史文件不存在：{source}。先跑 collect 子命令。")

    raw_conversations = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("{")
    ]

    pairs: list[QAPair] = []
    for conversation in raw_conversations:
        # extract_pairs 会忽略非 candidate/recruiter；system 会打断配对
        pairs.extend(
            extract_pairs(
                conversation.get("messages", []),
                source=conversation.get("candidate", ""),
                date=conversation.get("date", ""),
            )
        )
    print(f"挖出配对 {len(pairs)} 条（已做长度与红线过滤）")

    if not pairs:
        raise SystemExit("没有挖到任何配对。检查原始数据格式，或降低 MIN_QUESTION_CHARS / MIN_REPLY_CHARS。")

    judge = Judge()
    if not judge.enabled:
        print("⚠️  未配置 TYPESAFE_API_KEY / OPENAI_API_KEY：跳过模型闸门，改用启发式质量分。")
        print("   强烈建议配置后再构建 —— 质量闸门是防止把离职 HR 的旧话术学进去的关键一步。")
        for pair in pairs:
            pair.quality = judge.score_pair_quality(pair.question, pair.reply, policy)
            pair.intent, _ = judge.label_intent(pair.question, job)
    else:
        print(f"用模型给 {len(pairs)} 条配对打质量分与意图标签…")
        for index, pair in enumerate(pairs, start=1):
            pair.quality = judge.score_pair_quality(pair.question, pair.reply, policy)
            pair.intent, _ = judge.label_intent(pair.question, job)
            if index % 50 == 0:
                print(f"  … {index}/{len(pairs)}")
        judge.close()

    kept = [pair for pair in pairs if pair.quality >= args.min_quality]
    print(f"质量闸门（>= {args.min_quality}）后剩 {len(kept)} 条")
    final = dedupe(kept)
    print(f"去重后剩 {len(final)} 条")

    count = save_pairs(final, args.out)
    by_intent: dict[str, int] = {}
    for pair in final:
        by_intent[pair.intent] = by_intent.get(pair.intent, 0) + 1
    print(f"\n已写入 {count} 条 → {args.out}")
    print("意图分布：" + "，".join(f"{k}={v}" for k, v in sorted(by_intent.items())))
    print("\n建议人工抽查 20-30 条，确认语气和口径没问题再启用。")


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="构建风格层知识库（历史对话配对）")
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--boss-bin", default="boss")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="采集历史沟通记录")
    p_collect.add_argument("--limit", type=int, default=50, help="最多采集多少个会话")
    p_collect.add_argument("--out", default="data/raw_history.jsonl")
    p_collect.add_argument(
        "--dump-raw",
        action="store_true",
        help="打印 list/chat 原始输出与解析结果，用于校准字段映射",
    )
    p_collect.set_defaults(func=collect)

    p_build = sub.add_parser("build", help="从原始记录构建风格知识库")
    p_build.add_argument("--source", default="data/raw_history.jsonl")
    p_build.add_argument("--out", default="data/style_pairs.jsonl")
    p_build.add_argument("--job", default=None, help="岗位 id，默认取 policy 里的第一个")
    p_build.add_argument("--min-quality", type=float, default=1.5)
    p_build.set_defaults(func=build)

    args = parser.parse_args()
    if getattr(args, "job", None) is None and args.command == "build":
        args.job = load_policy(args.policy)["jobs"][0]["id"]
    args.func(args)


if __name__ == "__main__":
    main()
