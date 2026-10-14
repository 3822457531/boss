/**
 * 探测 BOSS 聊天页「已读」标记的 DOM 选择器。
 *
 * 背景：boss-cli 抓聊天时只读 .message-item / .text span / .message-time .time，
 * 没有抓取我方消息的送达/已读状态（全仓库 grep「已读/回执/送达」零命中）。
 * 追聊功能若要求「对方确已读」才催，就必须先知道这个标记长什么样。
 *
 * 用法（必须在项目根目录执行，且 Chrome 已登录 BOSS）：
 *   runtime\node.exe tools\_probe_read_status.mjs <候选人姓名>
 *
 * 输出 JSON 含三部分：
 *   - lastMine：我方最后 3 条消息的 HTML 片段与全部子节点 class/text（用于人工定位标记）
 *   - hits    ：全页扫描到的「已读/未读/送达」文本节点及其 class（直接给出候选选择器）
 *   - 计数     ：我方消息条数 / 消息总数
 *
 * 注意：打开会话本身会把对方消息标为已读，这是平台的既成行为。
 */
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..");
const cliRoot = path.join(repo, "runtime", "node_modules", "@joohw", "boss-cli");

const { withBossSessionPage } = await import(
  pathToFileURL(path.join(cliRoot, "dist/common/boss_session_page.js")).href
);
const { runOpenCandidateChat } = await import(
  pathToFileURL(path.join(cliRoot, "dist/toolset/chat.js")).href
);
const { sleepRandom } = await import(
  pathToFileURL(path.join(cliRoot, "dist/browser/index.js")).href
);

// 按 boss-cli 的约定使用字符串脚本，避免 evaluate 回调被打包注入 __name 报错
function scanScript() {
  return `(() => {
    const norm = (v) => (v ?? "").replace(/\\s+/g, " ").trim();
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const lists = Array.from(document.querySelectorAll(".chat-message-list")).filter(visible);
    const list = lists[lists.length - 1];
    if (!list) return { error: "未找到可见的 .chat-message-list" };

    const items = Array.from(list.querySelectorAll(".message-item"));
    const mine = items.filter((it) => it.querySelector(".item-myself"));

    const lastMine = mine.slice(-3).map((it, i) => {
      const body =
        it.querySelector(".item-myself .text span") || it.querySelector(".item-myself .text");
      const nodes = Array.from(it.querySelectorAll("*"))
        .map((el) => ({
          tag: el.tagName,
          cls: typeof el.className === "string" ? el.className.slice(0, 120) : "",
          text: norm(el.textContent).slice(0, 40),
        }))
        .filter((n) => n.text || n.cls);
      return {
        seq: i,
        text: norm(body && body.textContent).slice(0, 60),
        time: norm(it.querySelector(".message-time .time")?.textContent),
        htmlHead: it.outerHTML.slice(0, 900),
        nodes: nodes.slice(0, 45),
      };
    });

    const hits = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      const t = norm(el.textContent);
      if (!t || t.length > 12) continue;
      if (/已读|未读|送达/.test(t)) {
        hits.push({
          tag: el.tagName,
          cls: typeof el.className === "string" ? el.className.slice(0, 120) : "",
          text: t,
          inMine: !!el.closest(".item-myself"),
        });
      }
      if (hits.length > 30) break;
    }

    return {
      url: location.href,
      totalCount: items.length,
      myCount: mine.length,
      lastMine,
      hits,
    };
  })()`;
}

const name = (process.argv[2] || "").trim();
if (!name) {
  console.error("用法: node tools/_probe_read_status.mjs <候选人姓名>");
  process.exit(1);
}

const report = await withBossSessionPage(async (page) => {
  await runOpenCandidateChat(page, name, true);
  await sleepRandom(2500, 4000);
  return await page.evaluate(scanScript());
});

console.log(JSON.stringify(report, null, 2));
