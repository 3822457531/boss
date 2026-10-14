/**
 * 强制重新进入推荐页（硬刷新），便于拉取新一批牛人。
 * 输出一行 JSON：{ ok, url }
 */
import { pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const RECOMMEND_URL = "https://www.zhipin.com/web/chat/recommend";

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
const { sleepRandom } = await import(
  pathToFileURL(path.join(cliRoot, "dist/browser/index.js")).href
);

try {
  const out = await withBossSessionPage(async (page) => {
    await page.goto(RECOMMEND_URL, { waitUntil: "load", timeout: 60_000 });
    await sleepRandom(800, 1400);
    // 等推荐 iframe 出现
    try {
      await page.waitForSelector('iframe[name="recommendFrame"]', { timeout: 18_000 });
    } catch {
      /* 仍返回当前 url，后续 recommend 会再等 */
    }
    return { ok: true, url: page.url() };
  });
  console.log(JSON.stringify(out));
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: e instanceof Error ? e.message : String(e) }));
  process.exit(1);
}
