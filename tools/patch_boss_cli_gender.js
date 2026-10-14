/**
 * 给 @joohw/boss-cli 的 recommend 补上性别字段。
 *
 * Boss 推荐卡片性别不在文本里，在头像角标：
 * - CSS class：man / woman（或 gender-man / gender-woman）
 * - 或 SVG use：#icon-icon-man / #icon-icon-woman
 * 官方 CLI 文本输出不含性别。npm 升级 boss-cli 后请重新执行本脚本。
 *
 * 用法: node tools/patch_boss_cli_gender.js
 */
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");

// 已打补丁特征：用 classList 局部变量判断 woman（不是 classList.contains 字面量）
const GENDER_MARKER = "cl.contains(\"woman\")";
const GENDER_OUT = "`性别:${m.gender}`";

const GENDER_BLOCK = `const baseInfo = Array.from(item.querySelectorAll(".base-info span"))
        .map((el) => norm(el.textContent))
        .filter(Boolean)
        .join(" / ");
      // 性别：CSS class man/woman，或头像角标 SVG #icon-icon-man|#icon-icon-woman
      const genderEl = item.querySelector(
        ".avatar-wrap .gender, .avatar-wrap svg.gender, svg.svg-icon.gender, .gender"
      );
      let gender = "";
      if (genderEl) {
        const cl = genderEl.classList;
        if (cl.contains("woman") || cl.contains("gender-woman")) gender = "女";
        else if (cl.contains("man") || cl.contains("gender-man")) gender = "男";
      }
      if (!gender) {
        const genderUse = item.querySelector(
          ".avatar-wrap svg.gender use, svg.svg-icon.gender use, .avatar-wrap svg use"
        );
        const genderHref =
          (genderUse &&
            (genderUse.getAttribute("href") ||
              genderUse.getAttributeNS("http://www.w3.org/1999/xlink", "href") ||
              "")) ||
          "";
        gender = /icon-icon-woman|icon-woman/i.test(genderHref)
          ? "女"
          : /icon-icon-man|icon-man/i.test(genderHref)
            ? "男"
            : "";
      }
`;

const OLD_BASE = `const baseInfo = Array.from(item.querySelectorAll(".base-info span"))
        .map((el) => norm(el.textContent))
        .filter(Boolean)
        .join(" / ");
      const expect =`;

/** 旧版仅 SVG 的注入块 → 换成本版（含 CSS class） */
const OLD_SVG_ONLY = /const baseInfo = Array\.from\(item\.querySelectorAll\("\.base-info span"\)\)\s*\n\s*\.map\(\(el\) => norm\(el\.textContent\)\)\s*\n\s*\.filter\(Boolean\)\s*\n\s*\.join\(" \/ "\);\s*\n\s*\/\/[^\n]*\n\s*const genderUse =[\s\S]*?const gender = \/icon-icon-woman[\s\S]*?:\s*"";\s*\n\s*const expect =/;

function collectTargets() {
  const list = [
    path.join(root, "runtime", "node_modules", "@joohw", "boss-cli", "dist", "toolset", "recommend.js"),
    path.join(
      root,
      "dist",
      "ZhiLiaoXiaoMi",
      "runtime",
      "node_modules",
      "@joohw",
      "boss-cli",
      "dist",
      "toolset",
      "recommend.js",
    ),
    path.join("D:", "node", "node_cache", "node_modules", "@joohw", "boss-cli", "dist", "toolset", "recommend.js"),
  ];
  try {
    list.push(require.resolve("@joohw/boss-cli/dist/toolset/recommend.js"));
  } catch {
    /* ignore */
  }
  const seen = new Set();
  const out = [];
  for (const p of list) {
    const abs = path.resolve(p);
    if (seen.has(abs) || !fs.existsSync(abs)) continue;
    seen.add(abs);
    out.push(abs);
  }
  return out;
}

function patchOne(file) {
  let src = fs.readFileSync(file, "utf8");
  const before = src;

  if (!src.includes('const baseInfo = Array.from(item.querySelectorAll(".base-info span"))')) {
    throw new Error(`结构已变，无法打补丁: ${file}`);
  }

  if (src.includes(GENDER_MARKER) && src.includes(GENDER_OUT)) {
    return "skip";
  }

  // 升级旧 SVG-only 补丁
  if (OLD_SVG_ONLY.test(src)) {
    src = src.replace(OLD_SVG_ONLY, `${GENDER_BLOCK}\n      const expect =`);
  } else if (src.includes(OLD_BASE) && !src.includes("genderHref") && !src.includes(GENDER_MARKER)) {
    src = src.replace(OLD_BASE, `${GENDER_BLOCK}\n      const expect =`);
  } else if (!src.includes(GENDER_MARKER) && !src.includes("let gender =") && !src.includes("const gender =")) {
    throw new Error(`未找到可替换的 baseInfo 块: ${file}`);
  }

  if (!/geekId,\s*\n\s*name,\s*\n\s*salary,\s*\n\s*baseInfo,\s*\n\s*gender,/.test(src)) {
    src = src.replace(
      /geekId,\s*\n(\s*)name,\s*\n\1salary,\s*\n\1baseInfo,\s*\n\1expect,/,
      "geekId,\n$1name,\n$1salary,\n$1baseInfo,\n$1gender,\n$1expect,",
    );
  }

  if (!src.includes("m.gender ? `性别:${m.gender}`") && !src.includes("m.gender ? '性别:'")) {
    src = src.replace(
      "m.baseInfo ? `信息:${m.baseInfo}` : '',\n                m.expect ?",
      "m.baseInfo ? `信息:${m.baseInfo}` : '',\n                m.gender ? `性别:${m.gender}` : '',\n                m.expect ?",
    );
  }

  if (!src.includes(GENDER_OUT) && !src.includes("性别:${m.gender}")) {
    throw new Error(`补丁未完全应用: ${file}`);
  }
  if (!src.includes(GENDER_MARKER)) {
    throw new Error(`未写入 CSS class 判断: ${file}`);
  }

  if (src === before) return "skip";
  fs.writeFileSync(file, src);
  return "patched";
}

const targets = collectTargets();
if (!targets.length) {
  throw new Error("找不到 @joohw/boss-cli/dist/toolset/recommend.js");
}

let patched = 0;
let skipped = 0;
for (const file of targets) {
  const r = patchOne(file);
  console.log(`${r}: ${file}`);
  if (r === "patched") patched += 1;
  else skipped += 1;
}
console.log(`done: patched=${patched} skipped=${skipped}`);
