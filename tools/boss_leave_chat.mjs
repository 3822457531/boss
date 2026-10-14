/**
 * 离开 /web/chat/index：
 * --prefer=search（默认）：搜索 → 互动
 * --prefer=search-only：只搜索
 * --prefer=interaction：互动 → 搜索
 * 输出一行 JSON：{ ok, left_via, url }
 */
import { pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

function resolveCliRoot() {
  const env = (process.env.BOSS_CLI_ROOT || "").trim();
  if (env && fs.existsSync(path.join(env, "dist/common/boss_session_page.js"))) {
    return env;
  }
  const candidates = [
    path.resolve("d:/node/node_cache/node_modules/@joohw/boss-cli"),
    path.resolve(process.env.APPDATA || "", "npm/node_modules/@joohw/boss-cli"),
  ];
  try {
    const pkg = require.resolve("@joohw/boss-cli/package.json");
    candidates.unshift(path.dirname(pkg));
  } catch {
    /* ignore */
  }
  for (const c of candidates) {
    if (c && fs.existsSync(path.join(c, "dist/common/boss_session_page.js"))) {
      return c;
    }
  }
  throw new Error("找不到 @joohw/boss-cli，请设置 BOSS_CLI_ROOT");
}

const cliRoot = resolveCliRoot();
const { withBossSessionPage } = await import(
  pathToFileURL(path.join(cliRoot, "dist/common/boss_session_page.js")).href
);
const { clickBossSidebarMenuToPath } = await import(
  pathToFileURL(path.join(cliRoot, "dist/common/boss_sidebar_nav.js")).href
);
const { sleepRandom } = await import(
  pathToFileURL(path.join(cliRoot, "dist/browser/index.js")).href
);

const TARGETS_ALL = [
  {
    via: "interaction",
    path: "/web/chat/interaction",
    url: "https://www.zhipin.com/web/chat/interaction",
    label: "互动",
  },
  {
    via: "search",
    path: "/web/chat/search",
    url: "https://www.zhipin.com/web/chat/search",
    label: "搜索",
  },
];

const preferArg = (process.argv.slice(2).find((a) => a.startsWith("--prefer=")) || "")
  .slice("--prefer=".length)
  .trim()
  .toLowerCase();
const TARGETS =
  preferArg === "search" || preferArg === "search-only"
    ? preferArg === "search-only"
      ? [TARGETS_ALL[1]]
      : [TARGETS_ALL[1], TARGETS_ALL[0]]
    : preferArg === "interaction"
      ? TARGETS_ALL
      : [TARGETS_ALL[1], TARGETS_ALL[0]]; // 默认 search 优先

function normPath(u) {
  try {
    const p = new URL(u).pathname.replace(/\/+$/, "") || "/";
    return p;
  } catch {
    return "";
  }
}

async function goTarget(page, t) {
  // 已在目标页则直接成功
  if (normPath(page.url()) === t.path) {
    return true;
  }
  // 先点侧栏（更像人工），失败再 goto
  try {
    await clickBossSidebarMenuToPath(page, t.label, t.path);
  } catch {
    await page.goto(t.url, { waitUntil: "load", timeout: 60_000 });
  }
  await sleepRandom(400, 800);
  return normPath(page.url()) === t.path;
}

const result = await withBossSessionPage(async (page) => {
  const errors = [];
  for (const t of TARGETS) {
    try {
      const ok = await goTarget(page, t);
      if (ok) {
        return { ok: true, left_via: t.via, url: page.url() };
      }
      errors.push(`${t.via}: landed=${normPath(page.url())}`);
    } catch (e) {
      errors.push(`${t.via}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return {
    ok: false,
    left_via: null,
    url: page.url(),
    error: errors.join(" | ") || "leave failed",
  };
});

console.log(JSON.stringify(result));
process.exit(result.ok ? 0 : 1);
