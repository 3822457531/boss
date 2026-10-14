/**
 * 备用：在当前 Boss 会话页点击「约面试」。
 * 正常请用：boss action interview
 * （已打进本地 @joohw/boss-cli；npm 重装后若失效可再跑本脚本，或重打补丁）
 *
 * 用法（需已打开候选人聊天）：
 *   node tools/boss_action_interview.mjs
 */
import { pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

function resolveCliRoot() {
  const env = (process.env.BOSS_CLI_ROOT || "").trim();
  if (env && fs.existsSync(path.join(env, "dist/toolset/action.js"))) {
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
    if (c && fs.existsSync(path.join(c, "dist/toolset/action.js"))) {
      return c;
    }
  }
  throw new Error("找不到 @joohw/boss-cli，请设置 BOSS_CLI_ROOT");
}

const cliRoot = resolveCliRoot();
const { withBossSessionPage } = await import(
  pathToFileURL(path.join(cliRoot, "dist/common/boss_session_page.js")).href
);
const actionMod = await import(
  pathToFileURL(path.join(cliRoot, "dist/toolset/action.js")).href
);

if (typeof actionMod.runInviteInterview !== "function") {
  console.error(
    "当前 boss-cli 尚无 runInviteInterview。请确认已打补丁，或执行：boss action interview",
  );
  process.exit(1);
}

const result = await withBossSessionPage(async (page) => {
  return actionMod.runInviteInterview(page);
});
console.log(typeof result === "string" ? result : JSON.stringify(result));
