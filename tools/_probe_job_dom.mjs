/**
 * 探测职位管理页 DOM（含 iframe、加长等待）。
 */
import { pathToFileURL } from "node:url";
import path from "node:path";

const cliRoot = path.resolve("d:/node/node_cache/node_modules/@joohw/boss-cli");
const { withBossSessionPage } = await import(
  pathToFileURL(path.join(cliRoot, "dist/common/boss_session_page.js")).href
);
const { clickBossSidebarMenuToPath } = await import(
  pathToFileURL(path.join(cliRoot, "dist/common/boss_sidebar_nav.js")).href
);
const { sleepRandom } = await import(
  pathToFileURL(path.join(cliRoot, "dist/browser/index.js")).href
);

function scanScript() {
  return `(() => {
    const norm = (v) => (v ?? "").replace(/\\s+/g, " ").trim();
    const oldRows = document.querySelectorAll(".job-jobInfo-warp").length;
    const nameNodes = Array.from(document.querySelectorAll(".job-name"));
    const samples = nameNodes.slice(0, 2).map((nameEl) => {
      let card = nameEl.parentElement;
      let picked = null;
      for (let i = 0; i < 12 && card; i++) {
        const n = card.querySelectorAll(".job-name").length;
        const t = card.innerText || "";
        const hasStatus = /开放中|待开放|已关闭|审核未通过/.test(t);
        if (n === 1 && hasStatus) {
          const statusEl =
            card.querySelector(".status-box, .job-status, .status-text, [class*='status']") || null;
          const nums = Array.from(card.querySelectorAll(".num, [class*='num']"))
            .map((x) => norm(x.textContent))
            .filter(Boolean)
            .slice(0, 8);
          picked = {
            depth: i + 1,
            tag: card.tagName,
            class: typeof card.className === "string" ? card.className.slice(0, 160) : "",
            dataId: card.getAttribute("data-id") || "",
            statusGuess: norm(statusEl?.textContent || "").slice(0, 40),
            nums,
            htmlHead: card.outerHTML.slice(0, 1200),
          };
          break;
        }
        card = card.parentElement;
      }
      return { title: norm(nameEl.textContent), picked };
    });
    // 候选 class 摸底
    const classHits = {};
    for (const sel of [
      ".job-jobInfo-warp",
      ".job-name",
      ".job-list-item",
      ".job-card",
      ".job-item",
      ".job-info",
      ".position-item",
      ".total-num",
      "[class*='jobInfo']",
      "[class*='job-info']",
      "[class*='JobInfo']",
    ]) {
      try { classHits[sel] = document.querySelectorAll(sel).length; } catch { classHits[sel] = -1; }
    }
    return {
      url: location.href,
      title: document.title,
      bodyHead: norm(document.body?.innerText).slice(0, 200),
      oldRows,
      jobNameCount: nameNodes.length,
      totalText: norm(document.querySelector(".total-num")?.textContent),
      classHits,
      samples,
    };
  })()`;
}

const report = await withBossSessionPage(async (page) => {
  // 强制再进一次职位管理，保证列表渲染
  await clickBossSidebarMenuToPath(page, "职位管理", "/web/chat/job/list");
  await sleepRandom(4000, 6000);

  const frames = page.frames();
  const out = { frameCount: frames.length, frames: [] };
  for (const frame of frames) {
    try {
      const data = await frame.evaluate(scanScript());
      out.frames.push({
        url: frame.url(),
        name: frame.name(),
        isMain: frame === page.mainFrame(),
        data,
      });
    } catch (e) {
      out.frames.push({
        url: frame.url(),
        err: e instanceof Error ? e.message : String(e),
      });
    }
  }
  return out;
});

console.log(JSON.stringify(report, null, 2));
