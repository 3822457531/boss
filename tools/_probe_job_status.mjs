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

const report = await withBossSessionPage(async (page) => {
  await clickBossSidebarMenuToPath(page, "职位管理", "/web/chat/job/list");
  await sleepRandom(3500, 5000);
  for (const frame of page.frames()) {
    if (!frame.url().includes("job_v2/list")) continue;
    return frame.evaluate(() => {
      const card = document.querySelector(".item-content-wrap");
      if (!card) return { err: "no card" };
      const dump = (root) =>
        Array.from(root.querySelectorAll("*"))
          .filter((el) => {
            const t = (el.textContent || "").trim();
            return /开放中|待开放|已关闭|看过我|编辑/.test(t) && t.length < 40;
          })
          .slice(0, 40)
          .map((el) => ({
            tag: el.tagName,
            class: String(el.className || "").slice(0, 100),
            text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 50),
          }));
      return {
        cardClass: card.className,
        parentClass: card.parentElement?.className || "",
        grandClass: card.parentElement?.parentElement?.className || "",
        statusNodes: dump(card),
        outerHtmlTail: card.outerHTML.slice(-1500),
      };
    });
  }
  return { err: "no job_v2 frame" };
});

console.log(JSON.stringify(report, null, 2));
