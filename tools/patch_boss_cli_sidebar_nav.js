/**
 * 修复 @joohw/boss-cli 侧栏导航：Puppeteer 24 下字符串 evaluate/waitForFunction 传参失效。
 * npm 升级 boss-cli 后请重新执行：node tools/patch_boss_cli_sidebar_nav.js
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const source = path.join(
  root,
  "runtime",
  "node_modules",
  "@joohw",
  "boss-cli",
  "dist",
  "common",
  "boss_sidebar_nav.js",
);
if (!fs.existsSync(source)) {
  throw new Error(`缺少已修复源文件: ${source}`);
}
const body = fs.readFileSync(source, "utf8");
if (!body.includes("Puppeteer 24") || body.includes("page.evaluate(`")) {
  throw new Error("runtime 内 boss_sidebar_nav.js 看起来不是已修复版本，请先恢复修复内容");
}

const targets = [
  source,
  path.join("D:", "node", "node_cache", "node_modules", "@joohw", "boss-cli", "dist", "common", "boss_sidebar_nav.js"),
];
try {
  targets.push(
    path.join(path.dirname(require.resolve("@joohw/boss-cli/package.json")), "dist", "common", "boss_sidebar_nav.js"),
  );
} catch {
  /* ignore */
}

const seen = new Set();
let n = 0;
for (const file of targets) {
  const abs = path.resolve(file);
  if (seen.has(abs) || !fs.existsSync(abs)) continue;
  seen.add(abs);
  fs.writeFileSync(abs, body, "utf8");
  console.log("patched", abs);
  n += 1;
}
if (!n) {
  throw new Error("找不到可写入的 boss_sidebar_nav.js");
}
console.log(`done: ${n} file(s)`);
