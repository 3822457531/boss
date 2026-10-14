/* 本地管理台前端：无框架 */
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const state = {
  page: "overview",
  presets: null,
  modelLogKind: "",
  lastTests: { llm: null, jev: null },
  modelsTab: "llm",
  posId: "",
  posTab: "jobs",
  faqFilter: "",
};

/** 打招呼 / 自动回复页运行中的 1s 轮询（倒计时） */
let _livePollTimer = null;
let _livePollPage = "";

function stopLivePoll() {
  if (_livePollTimer) {
    clearInterval(_livePollTimer);
    _livePollTimer = null;
  }
  _livePollPage = "";
}

function startLivePoll(page, tick) {
  stopLivePoll();
  _livePollPage = page;
  const run = () => {
    if (state.page !== _livePollPage) {
      stopLivePoll();
      return;
    }
    Promise.resolve(tick()).catch(() => {});
  };
  run();
  _livePollTimer = setInterval(run, 1000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (res.status === 403 && (data.code === "license_required" || data.code === "license_locked")) {
    renderLicenseGate(state.license || {}, data.detail || "需要激活");
    throw new Error(data.detail || "未激活");
  }
  if (!res.ok) throw new Error(data.detail || data.error || text || res.statusText);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function updateProgressHtml(pct, human) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return `
    <div class="ov-upd-progress">
      <div class="ov-progress"><span style="width:${p}%"></span></div>
      <p class="ov-hint" style="margin:.45rem 0 0">${esc(human || "下载中…")} · ${p}%</p>
    </div>`;
}

async function applyClientUpdateWithProgress(mountEl, { onDone } = {}) {
  if (!mountEl) return;
  mountEl.innerHTML = updateProgressHtml(0, "准备下载…");
  let timer = null;
  const poll = async () => {
    try {
      const p = await api("/api/license/update-progress");
      const pct = p.status === "done" || p.status === "launching" || p.status === "downloaded"
        ? Math.max(Number(p.pct) || 0, p.status === "done" ? 100 : Number(p.pct) || 0)
        : (Number(p.pct) || 0);
      mountEl.innerHTML = updateProgressHtml(pct, p.human || "下载中…");
    } catch { /* ignore poll errors */ }
  };
  timer = setInterval(poll, 400);
  poll();
  try {
    const r = await api("/api/license/apply-update", { method: "POST", body: "{}" });
    clearInterval(timer);
    await poll();
    if (onDone) onDone(null, r);
    else {
      mountEl.innerHTML = `<div class="ov-alert ov-alert-ok">${esc(r.human || "已启动安装")}</div>`;
    }
    return r;
  } catch (e) {
    clearInterval(timer);
    if (onDone) onDone(e, null);
    else {
      mountEl.innerHTML = `<div class="ov-alert ov-alert-err">${esc(e.message || e)}</div>`;
    }
    throw e;
  }
}

function hmToMin(hm) {
  const t = String(hm || "").trim();
  if (!t) return null;
  const [h, m] = t.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  return h * 60 + m;
}

function minToHm(mins) {
  if (mins == null || Number.isNaN(mins)) return "";
  let m = ((Math.round(mins / 15) * 15) % 1440 + 1440) % 1440;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${String(h).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

function windowSegs(start, end) {
  const s = hmToMin(start);
  const e = hmToMin(end);
  if (s == null || e == null || s === e) return [];
  if (s < e) return [[s, e]];
  return [[s, 1440], [0, e]];
}

function windowsOverlap(gs, ge, rs, re) {
  const a = windowSegs(gs, ge);
  const b = windowSegs(rs, re);
  if (!a.length || !b.length) return false;
  for (const [a0, a1] of a) {
    for (const [b0, b1] of b) {
      if (a0 < b1 && b0 < a1) return true;
    }
  }
  return false;
}

function getScheduleValues() {
  return {
    greet_start: $("#sch-gs")?.value || "",
    greet_end: $("#sch-ge")?.value || "",
    reply_start: $("#sch-rs")?.value || "",
    reply_end: $("#sch-re")?.value || "",
  };
}

function setScheduleValues(sch, { repaint = true } = {}) {
  const map = {
    "sch-gs": sch.greet_start || "",
    "sch-ge": sch.greet_end || "",
    "sch-rs": sch.reply_start || "",
    "sch-re": sch.reply_end || "",
  };
  Object.entries(map).forEach(([id, v]) => {
    const el = document.getElementById(id);
    if (el) el.value = v;
  });
  if (repaint) {
    paintScheduleUI();
  } else {
    // 拖拽中只更新色块与胶囊，不重建 DOM
    $$(".time-chip").forEach(btn => {
      const id = btn.dataset.timeFor;
      const v = document.getElementById(id)?.value || "";
      const valEl = btn.querySelector(".tc-value");
      if (valEl) valEl.textContent = v || "选择";
      btn.classList.toggle("has", !!v);
    });
    const tip = $("#schedule-tip");
    const cur = getScheduleValues();
    if (tip) {
      if (windowsOverlap(cur.greet_start, cur.greet_end, cur.reply_start, cur.reply_end)) {
        tip.className = "schedule-warn";
        tip.textContent = "两段时间有重叠，无法保存";
      } else {
        tip.className = "schedule-ok";
        tip.textContent = "时段状态正常，保存后生效";
      }
    }
  }
}

function applyLaneBar(lane, start, end) {
  const track = document.querySelector(`.tl-lane-${lane} .tl-lane-track`);
  if (!track) return;
  const s = hmToMin(start);
  const e = hmToMin(end);
  if (s == null || e == null || s === e || s > e) {
    paintScheduleUI();
    bindScheduleDrag($("#day-board"));
    return;
  }
  let bar = track.querySelector(".tl-draggable");
  if (!bar) {
    paintScheduleUI();
    bindScheduleDrag($("#day-board"));
    return;
  }
  const left = (s / 1440) * 100;
  const width = Math.max(2, ((e - s) / 1440) * 100);
  bar.style.left = `${left}%`;
  bar.style.width = `${width}%`;
  const text = bar.querySelector(".tl-range-text");
  if (text) text.textContent = `${start}–${end}`;
}

function timeChipHtml(id, label, value) {
  const show = value || "选择";
  return `<button type="button" class="time-chip ${value ? "has" : ""}" data-time-for="${id}" aria-label="${esc(label)}">
    <span class="tc-label">${esc(label)}</span>
    <span class="tc-value">${esc(show)}</span>
  </button>`;
}

const SCHEDULE_PRESETS = [
  { id: "am-greet", name: "上午打招呼 / 下午回消息", greet_start: "09:00", greet_end: "12:00", reply_start: "13:00", reply_end: "22:00" },
  { id: "pm-greet", name: "下午打招呼 / 晚上回消息", greet_start: "14:00", greet_end: "18:00", reply_start: "18:30", reply_end: "22:00" },
  { id: "eve-greet", name: "晚间打招呼 / 白天回消息", greet_start: "19:00", greet_end: "22:00", reply_start: "09:00", reply_end: "18:00" },
  { id: "clear", name: "清空时段", greet_start: "", greet_end: "", reply_start: "", reply_end: "" },
];

function paintScheduleUI() {
  const sch = getScheduleValues();
  const box = $("#schedule-preview");
  if (box) box.innerHTML = renderScheduleBoard(sch);

  $$(".time-chip").forEach(btn => {
    const id = btn.dataset.timeFor;
    const v = document.getElementById(id)?.value || "";
    const valEl = btn.querySelector(".tc-value");
    if (valEl) valEl.textContent = v || "选择";
    btn.classList.toggle("has", !!v);
  });

  $$(".sch-preset").forEach(btn => {
    const p = SCHEDULE_PRESETS.find(x => x.id === btn.dataset.preset);
    if (!p || p.id === "clear") {
      btn.classList.remove("on");
      return;
    }
    const on = p.greet_start === sch.greet_start && p.greet_end === sch.greet_end
      && p.reply_start === sch.reply_start && p.reply_end === sch.reply_end;
    btn.classList.toggle("on", on);
  });

  const tip = $("#schedule-tip");
  if (tip) {
    if (windowsOverlap(sch.greet_start, sch.greet_end, sch.reply_start, sch.reply_end)) {
      tip.className = "schedule-warn";
      tip.textContent = "两段时间有重叠，无法保存";
    } else if ((sch.greet_start && sch.greet_end) || (sch.reply_start && sch.reply_end)) {
      tip.className = "schedule-ok";
      tip.textContent = "状态正常,，保存后生效";
    } else {
      tip.className = "hint";
      tip.textContent = "点快捷方案一键填好，或拖时间轴色块；两段不要重叠。";
    }
  }
  bindScheduleDrag($("#day-board"));
}

function renderScheduleBoard(sch) {
  const gs = sch.greet_start || "";
  const ge = sch.greet_end || "";
  const rs = sch.reply_start || "";
  const re = sch.reply_end || "";
  const now = new Date();
  const nowPct = ((now.getHours() * 60 + now.getMinutes()) / 1440) * 100;
  const hours = [0, 3, 6, 9, 12, 15, 18, 21, 24].map(h => `<span>${h}</span>`).join("");

  const lane = (kind, start, end, title) => {
    const segs = windowSegs(start, end);
    if (!segs.length) {
      return `<div class="tl-lane tl-lane-${kind}" data-lane="${kind}">
        <div class="tl-lane-label">${title}</div>
        <div class="tl-lane-track"><div class="tl-empty">点击或拖拽设置时段</div></div>
      </div>`;
    }
    // 跨午夜只编辑主视觉段：用起止分钟画一条（跨夜拆两段不可拖，回退为非跨夜编辑）
    const s = hmToMin(start);
    const e = hmToMin(end);
    const cross = s != null && e != null && s > e;
    if (cross) {
      const bars = segs.map(([a, b]) => {
        const left = (a / 1440) * 100;
        const width = Math.max(2, ((b - a) / 1440) * 100);
        return `<div class="tl-range tl-${kind}" style="left:${left}%;width:${width}%">${title} ${esc(start)}–${esc(end)}</div>`;
      }).join("");
      return `<div class="tl-lane tl-lane-${kind}" data-lane="${kind}">
        <div class="tl-lane-label">${title}</div>
        <div class="tl-lane-track">${bars}<div class="tl-hint">跨午夜请用下方时间胶囊微调</div></div>
      </div>`;
    }
    const left = (s / 1440) * 100;
    const width = Math.max(2, ((e - s) / 1440) * 100);
    return `<div class="tl-lane tl-lane-${kind}" data-lane="${kind}">
      <div class="tl-lane-label">${title}</div>
      <div class="tl-lane-track">
        <div class="tl-range tl-${kind} tl-draggable" data-lane="${kind}" style="left:${left}%;width:${width}%">
          <i class="tl-handle tl-h-start" data-edge="start"></i>
          <span class="tl-range-text">${esc(start)}–${esc(end)}</span>
          <i class="tl-handle tl-h-end" data-edge="end"></i>
        </div>
      </div>
    </div>`;
  };

  return `
    <div class="day-board" id="day-board">
      ${lane("greet", gs, ge, "打招呼")}
      ${lane("reply", rs, re, "回消息")}
      <div class="tl-now" style="left:calc(56px + (100% - 64px) * ${nowPct / 100})"></div>
      <div class="tl-hours"><span class="tl-hours-pad"></span><div class="tl-hours-row">${hours}</div></div>
    </div>`;
}

function closeTimePopover() {
  const pop = $("#time-popover");
  if (pop) pop.remove();
  document.removeEventListener("click", _timePopOutside, true);
}

function _timePopOutside(e) {
  const pop = $("#time-popover");
  if (!pop) return;
  if (pop.contains(e.target) || e.target.closest?.(".time-chip")) return;
  closeTimePopover();
}

function openTimePopover(anchor, fieldId) {
  closeTimePopover();
  const cur = document.getElementById(fieldId)?.value || "09:00";
  let [hh, mm] = (cur || "09:00").split(":").map(Number);
  if (Number.isNaN(hh)) hh = 9;
  if (Number.isNaN(mm)) mm = 0;
  mm = [0, 15, 30, 45].includes(mm) ? mm : Math.round(mm / 15) * 15 % 60;

  const hours = Array.from({ length: 24 }, (_, i) => {
    const v = String(i).padStart(2, "0");
    return `<button type="button" class="tp-cell ${i === hh ? "on" : ""}" data-h="${v}">${v}</button>`;
  }).join("");
  const mins = [0, 15, 30, 45].map(m => {
    const v = String(m).padStart(2, "0");
    return `<button type="button" class="tp-cell ${m === mm ? "on" : ""}" data-m="${v}">${v}</button>`;
  }).join("");

  const pop = document.createElement("div");
  pop.id = "time-popover";
  pop.className = "time-popover";
  pop.innerHTML = `
    <div class="tp-head">选择时间</div>
    <div class="tp-cols">
      <div><div class="tp-cap">时</div><div class="tp-grid tp-hours">${hours}</div></div>
      <div><div class="tp-cap">分</div><div class="tp-grid tp-mins">${mins}</div></div>
    </div>
    <div class="tp-actions">
      <button type="button" class="ghost" data-tp="clear">清空</button>
      <button type="button" data-tp="ok">确定</button>
    </div>`;
  document.body.appendChild(pop);

  const rect = anchor.getBoundingClientRect();
  const top = Math.min(window.innerHeight - 320, rect.bottom + 6);
  let left = rect.left;
  pop.style.top = `${Math.max(8, top)}px`;
  pop.style.left = `${Math.max(8, left)}px`;
  requestAnimationFrame(() => {
    const pr = pop.getBoundingClientRect();
    if (pr.right > window.innerWidth - 8) {
      pop.style.left = `${Math.max(8, window.innerWidth - pr.width - 8)}px`;
    }
  });

  let pickH = String(hh).padStart(2, "0");
  let pickM = String(mm).padStart(2, "0");
  pop.onclick = (e) => {
    const hBtn = e.target.closest("[data-h]");
    const mBtn = e.target.closest("[data-m]");
    const act = e.target.closest("[data-tp]");
    if (hBtn) {
      pickH = hBtn.dataset.h;
      pop.querySelectorAll("[data-h]").forEach(b => b.classList.toggle("on", b === hBtn));
    }
    if (mBtn) {
      pickM = mBtn.dataset.m;
      pop.querySelectorAll("[data-m]").forEach(b => b.classList.toggle("on", b === mBtn));
    }
    if (act?.dataset.tp === "clear") {
      const el = document.getElementById(fieldId);
      if (el) el.value = "";
      closeTimePopover();
      paintScheduleUI();
    }
    if (act?.dataset.tp === "ok") {
      const el = document.getElementById(fieldId);
      if (el) el.value = `${pickH}:${pickM}`;
      closeTimePopover();
      paintScheduleUI();
    }
  };
  setTimeout(() => document.addEventListener("click", _timePopOutside, true), 0);
}

function bindScheduleDrag(board) {
  if (!board) return;
  const xToMin = (clientX, track) => {
    const r = track.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    return Math.round((ratio * 1440) / 15) * 15;
  };

  board.querySelectorAll(".tl-lane-track").forEach(track => {
    const lane = track.closest("[data-lane]")?.dataset.lane;
    if (!lane) return;
    const startKey = lane === "greet" ? "greet_start" : "reply_start";
    const endKey = lane === "greet" ? "greet_end" : "reply_end";

    track.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".tl-handle") || e.target.closest(".tl-draggable")) return;
      if (e.button !== 0) return;
      e.preventDefault();
      const startMin = xToMin(e.clientX, track);
      const endMin = Math.min(1440, startMin + 120);
      const sch = getScheduleValues();
      sch[startKey] = minToHm(startMin);
      sch[endKey] = endMin >= 1440 ? "23:45" : minToHm(endMin);
      setScheduleValues(sch);
    });
  });

  board.querySelectorAll(".tl-draggable").forEach(bar => {
    const lane = bar.dataset.lane;
    const track = bar.parentElement;
    const startKey = lane === "greet" ? "greet_start" : "reply_start";
    const endKey = lane === "greet" ? "greet_end" : "reply_end";

    bar.querySelectorAll(".tl-handle").forEach(handle => {
      handle.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const edge = handle.dataset.edge;
        const move = (ev) => {
          const sch = getScheduleValues();
          let s = hmToMin(sch[startKey]) ?? 0;
          let en = hmToMin(sch[endKey]) ?? s + 60;
          const m = xToMin(ev.clientX, track);
          if (edge === "start") s = Math.min(Math.max(0, m), en - 30);
          else en = Math.max(Math.min(1440, m), s + 30);
          sch[startKey] = minToHm(s);
          sch[endKey] = en >= 1440 ? "23:45" : minToHm(en);
          setScheduleValues(sch, { repaint: false });
          applyLaneBar(lane, sch[startKey], sch[endKey]);
        };
        const up = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      });
    });
  });
}

function bindSchedulePreview() {
  paintScheduleUI();

  $$(".sch-preset").forEach(btn => {
    btn.onclick = () => {
      const p = SCHEDULE_PRESETS.find(x => x.id === btn.dataset.preset);
      if (!p) return;
      setScheduleValues(p);
    };
  });

  $$(".time-chip").forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      openTimePopover(btn, btn.dataset.timeFor);
    };
  });
}

function maskKeyBlock(keyInfo, inputId) {
  const configured = keyInfo?.configured;
  const mask = keyInfo?.mask || "";
  return `
    <label>API Key</label>
    <div class="row gap">
      <input id="${inputId}" type="password" placeholder="${configured ? "已保存 " + esc(mask) + "（输入新值以替换）" : "粘贴密钥，仅保存在本机"}" autocomplete="off" />
      ${configured ? `<button type="button" class="ghost" data-clear-key="${inputId}">清除密钥</button>` : ""}
    </div>
    <p class="hint">已保存的密钥不会回显完整值；仅显示末四位掩码。</p>`;
}

function testBox(last) {
  if (!last) return `<p class="muted">尚未测试</p>`;
  if (last.ok) {
    return `<div class="ok-box">成功 · ${esc(last.elapsed_ms)}ms · ${esc(last.preview || last.noul || "")}
      ${last.warn ? `<div class="hint">${esc(last.warn)}</div>` : ""}
      <details><summary>原始片段</summary><pre class="raw">${esc(last.raw_snippet || "")}</pre></details>
    </div>`;
  }
  return `<div class="error-box">${esc(last.human || "失败")}
    <details open><summary>原始错误</summary><pre class="raw">${esc(last.error || "")}</pre></details>
  </div>`;
}

/* ---------- pages ---------- */

async function renderOverview() {
  const d = await api("/api/overview");
  const chromeOk = !!d.chrome?.installed;
  const bossOk = !!d.boss?.cli_ok;
  const llmOk = !!(d.llm?.api_key?.configured && d.llm?.enabled);
  const llmCfg = !!d.llm?.api_key?.configured;
  const jevOk = !!(d.jev?.api_key?.configured && d.jev?.enabled);
  const jevCfg = !!d.jev?.api_key?.configured;
  const autoRun = !!d.auto?.running;
  const bossSrc = d.boss?.deps?.boss_cli?.source || d.boss?.source || "";
  const srcLabel = bossSrc === "bundled" ? "安装包内置 runtime"
    : bossSrc === "path" ? "系统 PATH" : (bossSrc || "—");
  const ver = state.license?.version || "";
  const exp = state.license?.expires_at || "—";
  const issues = d.issues || [];
  const allOk = issues.length === 0;
  const llmLabel = llmCfg ? (d.llm.enabled ? "已启用" : "已配置") : "未下发";
  const jevLabel = jevCfg ? (d.jev.enabled ? "已启用" : "已配置") : "未下发";
  const issueHtml = issues.map(i =>
    `<div class="ov-issue ${i.level === "error" ? "is-err" : "is-warn"}">
      <span>${esc(i.text)}</span>
      ${i.action === "chrome_download"
        ? `<button type="button" class="ov-link-btn" data-chrome-dl>下载 Chrome</button>`
        : `<a class="link" data-goto="${esc(i.goto)}">去修复</a>`}
    </div>`
  ).join("");

  $("#main").innerHTML = `
    <div class="ov">
      <header class="ov-hero">
        <div class="ov-hero-text">
          <h1>自动化智能挂机系统</h1>
    
          <div class="ov-meta">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            版本 ${esc(ver || "—")}${state.license?.activated ? `　|　卡密有效期至 ${esc(exp)}` : ""}
          </div>
        </div>
        <div class="ov-hero-actions">
          <button type="button" class="ov-btn ov-btn-primary" id="ov-check-upd">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a9 9 0 11-2.6-6.2M21 4v6h-6"/></svg>
            检查更新
          </button>
          <button type="button" class="ov-btn ov-btn-ghost" id="ov-deact">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/></svg>
            解除激活
          </button>
        </div>
        <div class="ov-hero-art" aria-hidden="true">
          <svg viewBox="0 0 200 160" fill="none">
            <ellipse cx="100" cy="142" rx="54" ry="12" fill="#3b82f6" opacity=".12"/>
            <circle cx="100" cy="118" r="36" fill="url(#ovg)" opacity=".35"/>
            <rect x="68" y="78" width="64" height="52" rx="18" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <circle cx="100" cy="52" r="28" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <circle cx="90" cy="50" r="3.2" fill="#3b82f6"/><circle cx="110" cy="50" r="3.2" fill="#3b82f6"/>
            <path d="M90 61c3.2 3.2 7.8 3.2 11 0" stroke="#3b82f6" stroke-width="2.2" stroke-linecap="round"/>
            <rect x="78" y="96" width="44" height="26" rx="6" fill="#eff6ff" stroke="#93c5fd" stroke-width="1.5"/>
            <defs><radialGradient id="ovg" cx=".5" cy=".5" r=".5"><stop stop-color="#93c5fd"/><stop offset="1" stop-color="#93c5fd" stop-opacity="0"/></radialGradient></defs>
          </svg>
        </div>
      </header>
      <div id="ov-upd"></div>

      ${!chromeOk ? `<div class="ov-alert ov-alert-err">
        <strong>缺少 Google Chrome</strong>
        <p>自动化通过 Chrome 打开招聘网页。请先安装 Chrome，完成后刷新本页。</p>
        <div class="ov-alert-actions">
          <button type="button" class="ov-btn ov-btn-primary" id="ov-chrome-dl">下载 Chrome（推荐）</button>
          <button type="button" class="ov-btn ov-btn-ghost" id="ov-chrome-dl-intl">国际站下载</button>
          <button type="button" class="ov-btn ov-btn-ghost" id="ov-chrome-recheck">我已安装，重新检测</button>
        </div>
        <p class="ov-hint" id="ov-chrome-msg"></p>
      </div>` : ""}
      ${d.onboarding ? `<div class="ov-alert ov-alert-warn"><strong>首次使用：</strong>① 确认总控已下发模型 → ② 扫码登录招聘平台 → ③ 职位与知识库 → ④ 启动自动回复</div>` : ""}

      <section class="ov-card ov-login">
        <div class="ov-login-body">
          <div class="ov-login-title">
            <span class="ov-ico ov-ico-blue">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8M12 18v3"/></svg>
            </span>
            <h2>扫码登录</h2>
          </div>
          <p class="ov-desc">自动化依赖已登录的招聘网页会话。点击下方按钮会打开登录页，请用手机 App 扫码。</p>
          <div class="ov-login-row">
            <button type="button" class="ov-btn ov-btn-primary ov-btn-lg" id="ov-boss-login" ${chromeOk ? "" : "disabled"}>
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8M12 18v3"/></svg>
              打开扫码登录
            </button>
            <span class="ov-hint" id="ov-boss-login-msg">${chromeOk ? "" : "请先安装 Chrome"}</span>
          </div>
        </div>
        <div class="ov-login-art" aria-hidden="true">
          <svg viewBox="0 0 160 140" width="140" height="120" fill="none">
            <rect x="48" y="12" width="64" height="110" rx="12" fill="#fff" stroke="#93c5fd" stroke-width="2"/>
            <rect x="56" y="28" width="48" height="48" rx="6" fill="#eff6ff" stroke="#60a5fa" stroke-width="1.5"/>
            <path d="M64 40h8v8h-8zM76 40h8v8h-8zM88 40h8v8h-8zM64 52h8v8h-8zM76 52h8v8h-8zM88 52h8v8h-8zM64 64h8v8h-8zM88 64h8v8h-8z" fill="#3b82f6" opacity=".55"/>
            <path d="M18 70c18-28 40-20 40-20" stroke="#60a5fa" stroke-width="2" stroke-linecap="round" fill="none"/>
            <path d="M50 48l8 2-6 6" stroke="#60a5fa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <text x="8" y="98" fill="#64748b" font-size="11" font-family="Segoe UI, PingFang SC, sans-serif">手机扫码</text>
            <text x="8" y="114" fill="#94a3b8" font-size="10" font-family="Segoe UI, PingFang SC, sans-serif">快速登录</text>
          </svg>
        </div>
      </section>

      <div class="ov-grid3">
        <article class="ov-card ov-mod">
          <span class="ov-dot ${bossOk ? "ok" : "bad"}"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-green">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">自动化引擎</div>
              <div class="ov-mod-stat ${bossOk ? "ok" : "bad"}">${bossOk ? "可用" : "异常"}</div>
            </div>
          </div>
          <p class="ov-desc">${esc(d.boss?.hint || (bossOk ? "已检测到boss-ai引擎" : "引擎不可用"))}</p>
          <p class="ov-hint">来源：${esc(srcLabel)}</p>
          
        </article>

        <article class="ov-card ov-mod">
          <span class="ov-dot ${llmOk ? "ok" : llmCfg ? "warn" : "bad"}"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-blue">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">大模型（总控下发）</div>
              <div class="ov-mod-stat ${llmOk ? "ok" : ""}">${esc(llmLabel)}</div>
            </div>
          </div>
          <p class="ov-desc">${esc(d.llm.provider || "—")} · ${esc(d.llm.model || "—")} · ${esc(d.llm.api_key.mask || "无密钥")}</p>
          <p class="ov-hint">密钥在总控台配置，本机不可改</p>
        </article>

        <article class="ov-card ov-mod">
          <span class="ov-dot ${jevOk ? "ok" : jevCfg ? "warn" : "bad"}"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-violet">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">Jev 判断（总控下发）</div>
              <div class="ov-mod-stat ${jevOk ? "ok" : ""}">${esc(jevLabel)}</div>
            </div>
          </div>
          <p class="ov-desc">${esc(d.jev.provider || "—")} · ${esc(d.jev.model || "—")}</p>
          <p class="ov-hint">Chrome：${chromeOk ? "已安装" : "未安装"}</p>
        </article>
      </div>

      <div class="ov-grid2">
        <article class="ov-card ov-tile" data-goto="positions">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
          </span>
          <div class="ov-tile-body">
            <div class="ov-mod-label">职位 · 公共 FAQ</div>
            <div class="ov-mod-stat">${d.positions?.total ?? 0} 个职位</div>
            <p class="ov-hint">开放 ${d.positions?.open ?? 0} · 待开放 ${d.positions?.pending ?? 0} · 已关闭 ${d.positions?.closed ?? 0} · FAQ ${d.faq_count} 条</p>
            <a class="link" data-goto="positions">管理职位与知识库 →</a>
          </div>
          <span class="ov-chev">›</span>
        </article>
        <article class="ov-card ov-tile">
          <span class="ov-ico ov-ico-green">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg>
          </span>
          <div class="ov-tile-body">
            <div class="ov-mod-label">今日 · 发送模式</div>
            <div class="ov-mod-stat ok">全自动</div>
            <p class="ov-hint">自动发送 ${d.today_auto_sent} / 上限 ${d.daily_max ?? "—"} · 转人工 ${d.today_handoff}</p>
            <p class="ov-tags">
              <a class="link" data-goto="auto">自动循环</a>
              <a class="link" data-goto="rate">频控</a>
            </p>
          </div>
          <span class="ov-chev">›</span>
        </article>
      </div>

      <article class="ov-card ov-tile ov-auto">
        <span class="ov-ico ov-ico-orange">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0114.1-3.4L23 10M1 14l5.4 4.4A9 9 0 0020.5 15"/></svg>
        </span>
        <div class="ov-tile-body">
          <div class="ov-mod-label">自动循环</div>
          <div class="ov-mod-stat ${autoRun ? "ok" : "muted"}">${autoRun ? "运行中" : "已停止"} <span class="ov-sub">${esc(d.auto?.last_message || (autoRun ? "" : "未启动"))}</span></div>
          ${d.auto?.last_skip_reason ? `<p class="ov-hint ov-warn">跳过：${esc(d.auto.last_skip_reason)}</p>` : ""}
          <p class="ov-hint">处理 ${d.auto?.processed || 0} · 发送 ${d.auto?.sent || 0} · 转人工 ${d.auto?.handoff || 0}</p>
        </div>
        <a class="link" data-goto="auto">进入 →</a>
      </article>

      ${issues.length ? `<section class="ov-card"><div class="ov-mod-label" style="margin-bottom:.55rem">待处理</div>${issueHtml}</section>` : ""}

      <footer class="ov-foot ${allOk ? "ok" : "warn"}">
        <span>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          ${allOk ? "各模块状态正常" : `有 ${issues.length} 项待处理`}
        </span>
        <span><i class="ov-dot-inline ${allOk ? "ok" : "warn"}"></i>${allOk ? "系统运行稳定" : "请按提示修复"}</span>
      </footer>
    </div>
  `;

  async function openChromeDownload(intl) {
    const msg = $("#ov-chrome-msg");
    try {
      const q = intl ? "?intl=true" : "";
      const r = await api("/api/deps/chrome/open-download" + q, { method: "POST", body: "{}" });
      if (msg) msg.textContent = r.human || "已打开下载页";
    } catch (e) {
      if (msg) msg.textContent = String(e.message || e);
    }
  }
  const chromeDl = $("#ov-chrome-dl");
  if (chromeDl) chromeDl.onclick = () => openChromeDownload(false);
  const chromeIntl = $("#ov-chrome-dl-intl");
  if (chromeIntl) chromeIntl.onclick = () => openChromeDownload(true);
  const chromeRecheck = $("#ov-chrome-recheck");
  if (chromeRecheck) chromeRecheck.onclick = () => renderOverview();
  $$("[data-chrome-dl]").forEach(b => b.onclick = () => openChromeDownload(false));
  const updBtn = $("#ov-check-upd");
  if (updBtn) {
    updBtn.onclick = async () => {
      const box = $("#ov-upd");
      box.innerHTML = `<p class="ov-hint">检查中…</p>`;
      try {
        const u = await api("/api/license/check-update");
        if (!u.has_update) {
          box.innerHTML = `<div class="ov-alert ov-alert-ok">已是最新（${esc(u.current || u.latest || "")}）${u.error ? " · " + esc(u.error) : ""}</div>`;
          return;
        }
        box.innerHTML = `<div class="ov-alert ov-alert-warn">发现新版本 ${esc(u.latest)}（当前 ${esc(u.current)}）
          <p class="ov-hint">${esc(u.notes || "")}</p>
          <button class="ov-btn ov-btn-primary mt" id="ov-do-upd" type="button">下载并安装</button></div>`;
        $("#ov-do-upd").onclick = async () => {
          try {
            await applyClientUpdateWithProgress(box);
          } catch { /* UI already shows error */ }
        };
      } catch (e) {
        box.innerHTML = `<div class="ov-alert ov-alert-err">${esc(e.message || e)}</div>`;
      }
    };
  }
  const bossLogin = $("#ov-boss-login");
  if (bossLogin) {
    bossLogin.onclick = async () => {
      const msg = $("#ov-boss-login-msg");
      if (msg) msg.textContent = "正在打开…";
      bossLogin.disabled = true;
      try {
        const r = await openBossLogin({ silent: true });
        if (r.need_chrome) {
          if (msg) msg.textContent = r.human || "请先安装 Chrome";
          await api("/api/deps/chrome/open-download", { method: "POST", body: "{}" });
        } else if (msg) {
          msg.textContent = r.human || (r.ok ? "已打开登录页" : "打开失败");
        }
      } finally {
        bossLogin.disabled = false;
      }
    };
  }
  const deact = $("#ov-deact");
  if (deact) {
    deact.onclick = async () => {
      if (!confirm("解除本机激活？需重新输入卡密才能使用。")) return;
      await api("/api/license/deactivate", { method: "POST", body: "{}" });
      location.reload();
    };
  }
}

async function renderLlm() {
  if (!state.presets) state.presets = await api("/api/presets");
  const d = await api("/api/llm");
  const opts = state.presets.llm.map(p =>
    `<option value="${esc(p.id)}" ${p.id === d.provider ? "selected" : ""}>${esc(p.name)}</option>`
  ).join("");
  $("#main").innerHTML = `
    <h1>大模型设置</h1>
    <p class="lead">负责<strong>生成回复文本</strong>。调用境外模型会把对话内容发出去，请确认公司合规要求。</p>
    <div class="card">
      <div class="grid two">
        <div><label>厂商</label><select id="llm-provider">${opts}</select></div>
        <div><label>是否启用</label>
          <select id="llm-enabled"><option value="true" ${d.enabled ? "selected" : ""}>启用</option>
          <option value="false" ${!d.enabled ? "selected" : ""}>关闭</option></select></div>
        <div><label>API 地址</label><input id="llm-base" value="${esc(d.base_url)}" /></div>
        <div><label>模型名</label><input id="llm-model" value="${esc(d.model)}" /></div>
        <div><label>超时（秒）</label><input id="llm-timeout" type="number" value="${esc(d.timeout)}" /></div>
        <div><label>最大输出长度</label><input id="llm-max" type="number" min="64" value="${esc(d.max_tokens)}" />
          <p class="hint">deepseek-flash 等推理模型会先把额度花在「思考」上；建议 ≥1600。设太小会出现 content 为空、finish=length，思考残片可能被误发。</p></div>
        <div><label>采样温度 0~1</label><input id="llm-temp" type="number" step="0.1" min="0" max="1" value="${esc(d.temperature)}" /></div>
        <div>${maskKeyBlock(d.api_key, "llm-key")}</div>
      </div>
      <div class="row gap mt">
        <button id="llm-save">保存</button>
        <button class="ghost" id="llm-test">测试连接</button>
      </div>
      <div class="mt" id="llm-test-result">${testBox(state.lastTests.llm)}</div>
    </div>`;

  $("#llm-provider").onchange = () => {
    const p = state.presets.llm.find(x => x.id === $("#llm-provider").value);
    if (p) { $("#llm-base").value = p.base_url; $("#llm-model").value = p.model; }
  };
  $("#llm-save").onclick = async () => {
    const body = {
      enabled: $("#llm-enabled").value === "true",
      provider: $("#llm-provider").value,
      base_url: $("#llm-base").value.trim(),
      model: $("#llm-model").value.trim(),
      timeout: +$("#llm-timeout").value,
      max_tokens: +$("#llm-max").value,
      temperature: +$("#llm-temp").value,
      api_key: $("#llm-key").value.trim() || null,
    };
    await api("/api/llm", { method: "PUT", body: JSON.stringify(body) });
    alert("已保存到 .env");
    renderLlm();
  };
  $("#llm-test").onclick = async () => {
    $("#llm-test-result").innerHTML = `<p class="muted">测试中…</p>`;
    try {
      state.lastTests.llm = await api("/api/llm/test", { method: "POST", body: "{}" });
    } catch (e) {
      state.lastTests.llm = { ok: false, human: "请求失败", error: String(e.message || e) };
    }
    $("#llm-test-result").innerHTML = testBox(state.lastTests.llm);
  };
  bindClearKey("llm-key", "/api/llm/key", renderLlm);
}

async function renderJev() {
  if (!state.presets) state.presets = await api("/api/presets");
  const d = await api("/api/jev");
  const opts = state.presets.jev.map(p =>
    `<option value="${esc(p.id)}" ${p.id === d.provider ? "selected" : ""}>${esc(p.name)}</option>`
  ).join("");
  $("#main").innerHTML = `
    <h1>Jev 判断模型</h1>
    <p class="lead">Jev <strong>只做判断不做生成</strong>。回复文本由「大模型」负责。别名 <code>jev-latest</code> 会随版本移动，稳定复现请固定版本号。</p>
    ${!d.enabled ? `<div class="warn-box">Jev 关闭时系统降级为关键词规则，判断质量会显著下降。</div>` : ""}
    <div class="card">
      <div class="grid two">
        <div><label>接入方式</label><select id="jev-provider">${opts}</select></div>
        <div><label>是否启用</label>
          <select id="jev-enabled"><option value="true" ${d.enabled ? "selected" : ""}>启用</option>
          <option value="false" ${!d.enabled ? "selected" : ""}>关闭</option></select></div>
        <div><label>API 地址</label><input id="jev-base" value="${esc(d.base_url)}" /></div>
        <div><label>模型名</label><input id="jev-model" value="${esc(d.model)}" /></div>
        <div>${maskKeyBlock(d.api_key, "jev-key")}</div>
      </div>
      <div class="row gap mt">
        <button id="jev-save">保存</button>
        <button class="ghost" id="jev-test">测试连接（校验 System One）</button>
      </div>
      <div class="mt" id="jev-test-result">${testBox(state.lastTests.jev)}</div>
    </div>`;

  $("#jev-provider").onchange = () => {
    const p = state.presets.jev.find(x => x.id === $("#jev-provider").value);
    if (p) { $("#jev-base").value = p.base_url; $("#jev-model").value = p.model; }
  };
  $("#jev-save").onclick = async () => {
    await api("/api/jev", {
      method: "PUT",
      body: JSON.stringify({
        enabled: $("#jev-enabled").value === "true",
        provider: $("#jev-provider").value,
        base_url: $("#jev-base").value.trim(),
        model: $("#jev-model").value.trim(),
        api_key: $("#jev-key").value.trim() || null,
      }),
    });
    alert("已保存");
    renderJev();
  };
  $("#jev-test").onclick = async () => {
    $("#jev-test-result").innerHTML = `<p class="muted">测试中…</p>`;
    try {
      state.lastTests.jev = await api("/api/jev/test", { method: "POST", body: "{}" });
    } catch (e) {
      state.lastTests.jev = { ok: false, human: "请求失败", error: String(e.message || e) };
    }
    $("#jev-test-result").innerHTML = testBox(state.lastTests.jev);
  };
  bindClearKey("jev-key", "/api/jev/key", renderJev);
}

function bindClearKey(inputId, endpoint, reload) {
  const btn = document.querySelector(`[data-clear-key="${inputId}"]`);
  if (!btn) return;
  btn.onclick = async () => {
    if (!confirm("确认清除已保存的密钥？")) return;
    await api(endpoint, { method: "DELETE" });
    reload();
  };
}

async function renderFaq() {
  const d = await api("/api/faq");
  const rows = d.items.map((it, i) => `
    <tr data-i="${i}">
      <td><input value="${esc(it.question)}" data-f="question" /></td>
      <td><textarea data-f="answer">${esc(it.answer)}</textarea></td>
      <td><button class="ghost" data-del="${i}">删</button></td>
    </tr>`).join("");
  $("#main").innerHTML = `
    <h1>事实层知识库（L1）</h1>
    <div class="warn-box">事实库是口径依据。发送节奏里「FAQ 回答自由度」&gt;0 时，会按事实改写成口语再发出（同问意思相同、完整措辞不同）；设为 0 才原文直出。</div>
    <div class="card">
      <div class="row gap">
        <button id="faq-add">新增</button>
        <button id="faq-save">保存当前列表</button>
        <label class="ghost btn" style="display:inline-block;padding:.45rem .85rem;cursor:pointer">
          导入文件<input id="faq-file" type="file" accept=".md,.txt,.csv,.xlsx,.docx" hidden />
        </label>
        <button class="ghost" id="faq-paste-btn">粘贴导入</button>
      </div>
      <p class="hint mt">导入必须先预览再激活，不会上传即生效。路径：${esc(d.path)}</p>
      <table class="mt"><thead><tr><th>问题</th><th>标准答案</th><th></th></tr></thead>
        <tbody id="faq-body">${rows || `<tr><td colspan="3" class="muted">暂无条目</td></tr>`}</tbody></table>
    </div>
    <div id="faq-preview" class="card hidden"></div>`;

  const collect = () => $$("#faq-body tr[data-i]").map(tr => ({
    question: $("[data-f=question]", tr).value.trim(),
    answer: $("[data-f=answer]", tr).value.trim(),
  })).filter(x => x.question);

  $("#faq-add").onclick = () => {
    const tb = $("#faq-body");
    if (tb.querySelector(".muted")) tb.innerHTML = "";
    const i = $$("#faq-body tr").length;
    tb.insertAdjacentHTML("beforeend", `<tr data-i="${i}"><td><input data-f="question" /></td><td><textarea data-f="answer"></textarea></td><td><button class="ghost" data-del="${i}">删</button></td></tr>`);
  };
  $("#faq-body").onclick = (e) => {
    const b = e.target.closest("[data-del]");
    if (b) b.closest("tr").remove();
  };
  $("#faq-save").onclick = async () => {
    await api("/api/faq", { method: "PUT", body: JSON.stringify({ items: collect() }) });
    alert("已写入 faq.md（已自动备份旧版）");
    renderFaq();
  };
  $("#faq-file").onchange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/faq/preview-upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) return alert(data.detail || "解析失败");
    showFaqPreview(data.items);
  };
  $("#faq-paste-btn").onclick = () => {
    const text = prompt("粘贴 Markdown（## 问题 + 正文）");
    if (!text) return;
    api("/api/faq/preview-paste", { method: "POST", body: JSON.stringify({ text }) })
      .then(d => showFaqPreview(d.items));
  };
}

function showFaqPreview(items) {
  const box = $("#faq-preview");
  box.classList.remove("hidden");
  box.innerHTML = `
    <h2>导入预览（确认后才写入）</h2>
    <p class="hint">可取消勾选不需要的条目，或就地编辑。</p>
    <table><thead><tr><th></th><th>问题</th><th>答案</th></tr></thead><tbody>
      ${items.map((it, i) => `<tr>
        <td><input type="checkbox" checked data-keep="${i}" /></td>
        <td><input data-pq="${i}" value="${esc(it.question)}" /></td>
        <td><textarea data-pa="${i}">${esc(it.answer)}</textarea></td>
      </tr>`).join("")}
    </tbody></table>
    <div class="row gap mt">
      <button id="faq-activate">确认激活</button>
      <button class="ghost" id="faq-cancel-preview">取消</button>
    </div>`;
  $("#faq-cancel-preview").onclick = () => box.classList.add("hidden");
  $("#faq-activate").onclick = async () => {
    const next = [];
    items.forEach((_, i) => {
      if (!$(`[data-keep="${i}"]`).checked) return;
      next.push({ question: $(`[data-pq="${i}"]`).value.trim(), answer: $(`[data-pa="${i}"]`).value.trim() });
    });
    await api("/api/faq", { method: "PUT", body: JSON.stringify({ items: next }) });
    alert("已激活事实库");
    renderFaq();
  };
}

async function renderPolicy() {
  const p = await api("/api/policy");
  const actions = p.actions || {};
  const locked = new Set(["not-fit"]);
  const labels = {
    resume: "在线简历（耗权益）",
    history: "沟通历史（只读）",
    remark: "备注",
    "agree-resume": "同意收简历",
    "request-attachment-resume": "求简历（会发默认话术）",
    "not-fit": "不合适（不可逆 · 仅人工）",
    wechat: "换微信（boss action wechat：对方发起同意/我方主动发起均可；需开白名单）",
  };
  $("#main").innerHTML = `
    <h1>红线与动作白名单</h1>
    <div class="card">
      <h2 style="margin-top:0">发送模式</h2>
      <p class="ok-box" style="margin:0">已固定为<strong>全自动发送</strong>（与模拟聊天一致）。通过阈值后直接回复，不再使用人工审草稿。</p>
      <input type="hidden" id="send-mode" value="auto_send" />
      <h2>动作白名单</h2>
      <p class="hint">Jev 会建议：继续沟通 / 换微信 / 求简历 / 同意收简历 / 邀请面试（话术）/ 换电话（话术）。下列开关控制「平台按钮」是否允许系统自动点；约面与要电话默认走文字模板。</p>
      ${Object.keys(labels).map(k => {
        const lock = locked.has(k);
        return `<label class="check ${lock ? "locked" : ""}">
          <input type="checkbox" data-act="${k}" ${actions[k] ? "checked" : ""} ${lock ? "disabled" : ""} />
          ${esc(labels[k])}${lock ? " 🔒" : ""}</label>`;
      }).join("<br/>")}
      <h2>红线（L3）</h2>
      <textarea id="redlines">${esc((p.redlines || []).join("\n"))}</textarea>
      <p class="hint">命中红线的草稿会被拦截，不发送。</p>
      <h2>敏感词（命中转人工）</h2>
      <textarea id="sensitive">${esc((p.sensitive_words || []).join("\n"))}</textarea>
      <p class="hint mt">职位与专属知识库请到「职位与知识库」页管理。</p>
      <button class="mt" id="policy-save">保存</button>
    </div>
    <div class="card" id="syn-card"><p class="muted">加载多义词…</p></div>`;

  $("#policy-save").onclick = async () => {
    if ($("#send-mode").value === "auto_send" && p.send_mode !== "auto_send") {
      const ok = await confirmModal("切换到自动发送", "确认启用全自动发送？");
      if (!ok) return;
    }
    const next = { ...p, send_mode: "auto_send" };
    next.redlines = $("#redlines").value.split("\n").map(s => s.trim()).filter(Boolean);
    next.sensitive_words = $("#sensitive").value.split("\n").map(s => s.trim()).filter(Boolean);
    next.actions = { ...(p.actions || {}) };
    $$("[data-act]").forEach(el => {
      if (el.disabled) return;
      next.actions[el.dataset.act] = el.checked;
    });
    next.actions["not-fit"] = false;
    await api("/api/policy", { method: "PUT", body: JSON.stringify({ policy: next }) });
    alert("已写入 policy.yaml");
    renderPolicy();
  };

  try {
    const syn = await api("/api/synonyms");
    const synText = Object.entries(syn.concepts || {}).map(([id, block]) => {
      const terms = (block.terms || []).join(", ");
      const canon = block.canonical || id;
      return `${id} | ${canon} | ${terms}`;
    }).join("\n");
    $("#syn-card").innerHTML = `
      <h2 style="margin-top:0">多义词 / 口语别名</h2>
      <p class="hint">每行：概念id | 规范名 | 词1, 词2　例：exchange_wechat | 交换微信 | 微信, vx, 绿泡泡</p>
      <textarea id="syn-text" rows="10">${esc(synText)}</textarea>
      <button class="mt" id="syn-save">保存多义词</button>`;
    $("#syn-save").onclick = async () => {
      const concepts = {};
      ($("#syn-text").value || "").split("\n").forEach(line => {
        const parts = line.split("|").map(s => s.trim());
        if (parts.length < 2 || !parts[0]) return;
        const id = parts[0];
        const canonical = parts[1] || id;
        const terms = (parts[2] || "").split(/[,，]/).map(s => s.trim()).filter(Boolean);
        concepts[id] = { canonical, terms };
      });
      await api("/api/synonyms", { method: "PUT", body: JSON.stringify({ concepts }) });
      alert("多义词已保存");
    };
  } catch (e) {
    $("#syn-card").innerHTML = `<div class="error-box">多义词加载失败：${esc(e.message || e)}</div>`;
  }
}

function _typingPreset(r) {
  if (r.typing_delay_enabled === false) return "off";
  const lo = Number(r.typing_delay_min_sec ?? 1.5);
  const hi = Number(r.typing_delay_max_sec ?? 12);
  if (hi >= 15 || lo >= 2.5) return "slow";
  return "natural";
}

function _typingFromPreset(preset) {
  if (preset === "off") {
    return { typing_delay_enabled: false, typing_delay_min_sec: 1.5, typing_delay_max_sec: 12 };
  }
  if (preset === "slow") {
    return { typing_delay_enabled: true, typing_delay_min_sec: 3, typing_delay_max_sec: 18 };
  }
  return { typing_delay_enabled: true, typing_delay_min_sec: 1.5, typing_delay_max_sec: 10 };
}

async function renderRate() {
  const p = await api("/api/policy");
  const t = p.thresholds || {};
  const r = p.rate_limit || {};
  const rr = p.resume_review || {};
  const reply = p.reply || {};
  const today = (p.stats || {}).today_auto_sent || 0;
  const daily = r.daily_max ?? 40;
  const typing = _typingPreset(r);
  const freedom = Math.max(0, Math.min(3, Number(reply.faq_freedom ?? 2)));
  const usagePct = daily > 0 ? Math.min(100, Math.round((today / daily) * 100)) : 0;

  $("#main").innerHTML = `
    <div class="ov">
      <header class="ov-hero">
        <div class="ov-hero-text">
          <h1>发送节奏</h1>
          <p>管三件事：发得有多快、一天最多几条、晚上几点别发。自动回复与打招呼共用间隔。</p>
          <div class="ov-meta">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            今日已发 ${esc(today)} / ${esc(daily)}　|　遇验证码自动停
          </div>
        </div>
        <div class="ov-hero-actions">
          <button type="button" class="ov-btn ov-btn-primary" id="rate-save">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>
            保存设置
          </button>
        </div>
        <div class="ov-hero-art" aria-hidden="true">
          <svg viewBox="0 0 200 160" fill="none">
            <ellipse cx="100" cy="142" rx="54" ry="12" fill="#3b82f6" opacity=".12"/>
            <circle cx="100" cy="78" r="42" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <path d="M100 52v28l18 10" stroke="#3b82f6" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="100" cy="78" r="4" fill="#3b82f6"/>
          </svg>
        </div>
      </header>

      <div class="ov-grid2">
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-green">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-6"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">今日已自动发送</div>
              <div class="ov-mod-stat ok">${esc(today)} <span class="ov-sub">/ ${esc(daily)}</span></div>
            </div>
          </div>
          <div class="ov-progress"><span style="width:${usagePct}%"></span></div>
          <p class="ov-hint">用量 ${usagePct}%</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-orange">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">风控</div>
              <div class="ov-mod-stat" style="font-size:1.05rem">验证码自动停</div>
            </div>
          </div>
          <p class="ov-desc">处理后回到「自动回复 / 打招呼」重新启动即可。</p>
        </article>
      </div>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">发消息节奏</h2>
            <p class="ov-desc">两道闸：发出前停顿 + 换人歇一歇，不叠第三套钟。</p>
          </div>
        </div>
        <div class="ov-flow" aria-hidden="true">
          <span class="ov-flow-step">处理此人</span>
          <span class="ov-flow-arrow">→</span>
          <span class="ov-flow-gate">发出前停顿</span>
          <span class="ov-flow-arrow">→</span>
          <span class="ov-flow-step">发送</span>
          <span class="ov-flow-arrow">→</span>
          <span class="ov-flow-gate">换人间隔</span>
          <span class="ov-flow-arrow">→</span>
          <span class="ov-flow-step">下一位</span>
        </div>
        <div class="ov-form-grid two mt">
          <div>
            <label>发出前停顿（像在打字）</label>
            <select id="rl-typing">
              <option value="off" ${typing === "off" ? "selected" : ""}>关闭 · 立刻发出</option>
              <option value="natural" ${typing === "natural" ? "selected" : ""}>自然 · 推荐</option>
              <option value="slow" ${typing === "slow" ? "selected" : ""}>偏慢 · 更像真人</option>
            </select>
            <p class="ov-hint">只在真正发文字时生效；跳过或只点按钮不计入。</p>
          </div>
          <div>
            <label>换下一个人前（秒）</label>
            <div class="ov-time-pair">
              <input id="rl-min" type="number" min="1" value="${esc(r.send_interval_min_sec ?? 20)}" />
              <span class="sep">～</span>
              <input id="rl-max" type="number" min="1" value="${esc(r.send_interval_max_sec ?? 50)}" />
            </div>
            <p class="ov-hint">发过消息 / 换微信 / 打过招呼后，在此区间随机歇。</p>
          </div>
          <div>
            <label>没有未读时，隔多久再看（秒）</label>
            <div class="ov-time-pair">
              <input id="rl-idle-min" type="number" min="5" step="5" value="${esc(r.idle_poll_min_sec ?? r.idle_poll_sec ?? 60)}" />
              <span class="sep">～</span>
              <input id="rl-idle-max" type="number" min="5" step="5" value="${esc(r.idle_poll_max_sec ?? r.idle_poll_sec ?? 120)}" />
            </div>
            <p class="ov-hint">空闲轮询，与「换人间隔」无关。建议不要太短。</p>
          </div>
        </div>
      </section>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-violet">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">上限与静默</h2>
            <p class="ov-desc">静默时段不处理未读；已约好的简历跟进仍会发。</p>
          </div>
        </div>
        <div class="ov-form-grid two">
          <div>
            <label>每小时最多发几条</label>
            <input id="rl-h" type="number" min="1" value="${esc(r.hourly_max ?? 20)}" />
          </div>
          <div>
            <label>每天最多发几条</label>
            <input id="rl-d" type="number" min="1" value="${esc(r.daily_max ?? 40)}" />
          </div>
          <div>
            <label>静默开始</label>
            <input id="rl-qs" type="time" value="${esc(r.quiet_start || "22:00")}" />
          </div>
          <div>
            <label>静默结束（可跨零点）</label>
            <input id="rl-qe" type="time" value="${esc(r.quiet_end || "08:00")}" />
          </div>
        </div>
        <p class="ov-hint mt">白天「打招呼 / 回消息」分工请到「自动打招呼 → 工作日程」设置。</p>
      </section>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">回复风格</h2>
            <p class="ov-desc">FAQ 怎么说、语气是否偏口语。</p>
          </div>
        </div>
        <div class="ov-form-grid two">
          <div>
            <label>FAQ 怎么说</label>
            <select id="rp-faq-freedom">
              <option value="0" ${freedom === 0 ? "selected" : ""}>照搬原文</option>
              <option value="1" ${freedom === 1 ? "selected" : ""}>轻度改写</option>
              <option value="2" ${freedom === 2 ? "selected" : ""}>自然口语 · 推荐</option>
              <option value="3" ${freedom === 3 ? "selected" : ""}>更随意</option>
            </select>

          </div>
          <div>
            <label>语气</label>
            <div class="ov-checks" style="margin-top:.35rem">
              <label class="check"><input id="rp-casual" type="checkbox" ${reply.casual_style !== false ? "checked" : ""} />
                聊天口语风（少用公文句号）</label>
            </div>
           
          </div>
        </div>
      </section>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-green">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">收到简历后</h2>
            <p class="ov-desc">同意收简历 → 先回一句 → 稍后再约面。等待期间不耽误回复其他人。</p>
          </div>
        </div>
        <div class="ov-checks" style="margin-top:0">
          <label class="check"><input id="rr-on" type="checkbox" ${rr.enabled !== false ? "checked" : ""} />
            启用「假装看简历」排队</label>
        </div>
        <div class="ov-form-grid two mt">
          <div>
           
            <div class="ov-time-pair">
              <input id="rr-min-min" type="number" min="1" step="1" value="${esc(Math.max(1, Math.round((rr.read_min_sec ?? 240) / 60)))}" />
               <label>分钟</label>
              <span class="sep">～</span>
              <input id="rr-max-min" type="number" min="1" step="1" value="${esc(Math.max(1, Math.round((rr.read_max_sec ?? 360) / 60)))}" />
              <label>分钟</label>
            </div>
          </div>
        </div>
        <div class="ov-form-grid mt">
          <div>
            <label>同意后立刻发</label>
            <textarea id="rr-ack" rows="2">${esc(rr.ack_template || "收到您的简历了，我先看看匹配度，稍后给您反馈～")}</textarea>
          </div>
          <div>
            <label>看完后约面</label>
            <textarea id="rr-invite" rows="3">${esc(rr.invite_template || "简历看过了，整体还不错。方便的话想邀您面试聊聊，您这周哪个时间段比较空？")}</textarea>
          </div>
        </div>
      </section>

      <div class="ov-actions">
        <button type="button" class="ov-btn ov-btn-primary ov-btn-lg" id="rate-save-2">保存设置</button>
      </div>
    </div>`;

  const saveRate = async () => {
    const next = { ...p };
    next.send_mode = "auto_send";
    next.thresholds = {
      ...(p.thresholds || {}),
      intent_confidence_min: 0,
      handoff_prob_max: 0.95,
      redline_hit_prob_max: t.redline_hit_prob_max ?? 0.35,
      wechat_prob_min: t.wechat_prob_min ?? 0.75,
      action_confidence_min: t.action_confidence_min ?? 0.7,
    };
    delete next.thresholds.draft_quality_min;

    const typingCfg = _typingFromPreset($("#rl-typing").value || "natural");
    let gapMin = Math.max(1, +$("#rl-min").value || 20);
    let gapMax = Math.max(1, +$("#rl-max").value || 50);
    if (gapMax < gapMin) gapMax = gapMin;
    let idleMin = Math.max(5, +$("#rl-idle-min").value || 60);
    let idleMax = Math.max(5, +$("#rl-idle-max").value || 120);
    if (idleMax < idleMin) idleMax = idleMin;

    next.rate_limit = {
      ...r,
      send_interval_min_sec: gapMin,
      send_interval_max_sec: gapMax,
      skip_gap_sec: Math.max(1, r.skip_gap_sec ?? 3),
      hourly_max: Math.max(1, +$("#rl-h").value || 20),
      daily_max: Math.max(1, +$("#rl-d").value || 40),
      quiet_start: $("#rl-qs").value || "22:00",
      quiet_end: $("#rl-qe").value || "08:00",
      stop_on_captcha: true,
      bounce_mode: "smart",
      bounce_chat_page: true,
      ...typingCfg,
      same_chat_watch: false,
      same_chat_watch_sec: r.same_chat_watch_sec ?? 12,
      same_chat_watch_poll_sec: r.same_chat_watch_poll_sec ?? 2.5,
      same_chat_watch_max_rounds: r.same_chat_watch_max_rounds ?? 4,
      same_chat_recheck: false,
      same_chat_recheck_ttl_sec: r.same_chat_recheck_ttl_sec ?? 180,
      idle_poll_min_sec: idleMin,
      idle_poll_max_sec: idleMax,
    };
    delete next.rate_limit.idle_poll_sec;

    next.reply = {
      ...(p.reply || {}),
      faq_freedom: Math.max(0, Math.min(3, +$("#rp-faq-freedom").value || 0)),
      casual_style: $("#rp-casual").checked,
    };

    let rmin = Math.round((+$("#rr-min-min").value || 4) * 60);
    let rmax = Math.round((+$("#rr-max-min").value || 6) * 60);
    if (rmin < 30) rmin = 30;
    if (rmax < rmin) rmax = rmin;
    next.resume_review = {
      enabled: $("#rr-on").checked,
      read_min_sec: rmin,
      read_max_sec: rmax,
      ack_template: $("#rr-ack").value.trim(),
      invite_template: $("#rr-invite").value.trim(),
    };

    await api("/api/policy", { method: "PUT", body: JSON.stringify({ policy: next }) });
    alert("已保存");
    renderRate();
  };
  $("#rate-save").onclick = saveRate;
  $("#rate-save-2").onclick = saveRate;
}

function scrubEngineText(text) {
  return String(text ?? "")
    .replace(/@joohw[/\\]+boss-cli/gi, "操作引擎")
    .replace(/[A-Za-z]:\\[^\s"'`]*boss-cli[^\s"'`]*/gi, "操作引擎")
    .replace(/\/[^\s"'`]*boss-cli[^\s"'`]*/gi, "操作引擎")
    .replace(/\bboss-cli\b/gi, "操作引擎");
}

function isVersionArgv(argv) {
  return (argv || []).some(a => String(a).trim().toLowerCase() === "version");
}

/** 列表最新在前：查询版本只保留最早一条，其余隐藏 */
function filterBossLogItems(items) {
  const list = items || [];
  let oldestVersionIdx = -1;
  for (let i = list.length - 1; i >= 0; i--) {
    if (isVersionArgv(list[i].argv)) {
      oldestVersionIdx = i;
      break;
    }
  }
  return list.filter((it, i) => !isVersionArgv(it.argv) || i === oldestVersionIdx);
}

function humanizeBossArgv(argv) {
  const args = (argv || []).map(String).filter(Boolean);
  const skip = (a) => {
    const low = a.toLowerCase();
    return low.includes("boss-cli")
      || /(?:^|[\\/])node(?:\.exe)?$/i.test(a)
      || /(?:^|[\\/])npx(?:\.cmd)?$/i.test(a)
      || low === "node"
      || low === "npx";
  };
  const meaningful = args.filter(a => !skip(a));
  const cmd = (meaningful[0] || "").toLowerCase();
  const labels = {
    list: "拉取会话列表",
    version: "查询版本",
    send: "发送消息",
    recommend: "刷新推荐",
    greeter: "打招呼",
    greet: "打招呼",
    open: "打开页面",
    login: "登录",
  };
  const title = labels[cmd] || (cmd ? cmd : "执行任务");
  const extras = meaningful.slice(1).map((a, i, arr) => {
    if (a === "--text" && arr[i + 1]) return null;
    if (arr[i - 1] === "--text") {
      const t = a.length > 24 ? `${a.slice(0, 24)}…` : a;
      return `「${t}」`;
    }
    if (a.startsWith("--")) return a.slice(2);
    return a;
  }).filter(Boolean);
  const tail = extras.length ? ` · ${extras.join(" ")}` : "";
  return scrubEngineText(`操作引擎 · ${title}${tail}`);
}

async function renderLogs() {
  const boss = await api("/api/logs/boss");
  const audit = await api("/api/logs/audit");
  const bossItems = filterBossLogItems(boss.items || []);
  const auditItems = audit.items || [];
  const failCount = bossItems.filter(it => Number(it.exit_code) !== 0).length;

  $("#main").innerHTML = `
    <div class="ov">
      <header class="ov-hero">
        <div class="ov-hero-text">
          <h1>运行日志</h1>
          <p>操作引擎执行记录与配置审计，便于排查异常与人工操作轨迹。</p>
          <div class="ov-meta">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h10M4 18h14"/></svg>
            引擎 ${bossItems.length} 条　|　审计 ${auditItems.length} 条
            ${failCount ? `　|　异常 ${failCount}` : ""}
          </div>
        </div>
        <div class="ov-hero-actions">
          <button type="button" class="ov-btn ov-btn-ghost" id="logs-refresh">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a9 9 0 11-2.6-6.2M21 4v6h-6"/></svg>
            刷新
          </button>
        </div>
        <div class="ov-hero-art" aria-hidden="true">
          <svg viewBox="0 0 200 160" fill="none">
            <ellipse cx="100" cy="142" rx="54" ry="12" fill="#3b82f6" opacity=".12"/>
            <rect x="44" y="34" width="112" height="84" rx="12" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <rect x="58" y="50" width="64" height="6" rx="3" fill="#93c5fd"/>
            <rect x="58" y="64" width="84" height="6" rx="3" fill="#bfdbfe"/>
            <rect x="58" y="78" width="52" height="6" rx="3" fill="#dbeafe"/>
            <rect x="58" y="92" width="72" height="6" rx="3" fill="#e0e7ff"/>
          </svg>
        </div>
      </header>

      <div class="ov-grid3">
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-blue">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">引擎调用</div>
              <div class="ov-mod-stat">${bossItems.length}</div>
            </div>
          </div>
          <p class="ov-hint">最近操作引擎执行记录</p>
        </article>
        <article class="ov-card ov-mod">
          <span class="ov-dot ${failCount ? "bad" : "ok"}"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ${failCount ? "ov-ico-orange" : "ov-ico-green"}">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">异常退出</div>
              <div class="ov-mod-stat ${failCount ? "bad" : "ok"}">${failCount}</div>
            </div>
          </div>
          <p class="ov-hint">${failCount ? "请展开下方记录查看详情" : "当前列表未见异常退出"}</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-violet">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">操作审计</div>
              <div class="ov-mod-stat">${auditItems.length}</div>
            </div>
          </div>
          <p class="ov-hint">配置变更与关键操作</p>
        </article>
      </div>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">操作引擎记录</h2>
            <p class="ov-desc">自动回复、打招呼等任务触发的引擎执行流水</p>
          </div>
        </div>
        <div class="ov-log-list">
          ${bossItems.map(it => {
            const bad = Number(it.exit_code) !== 0;
            const out = scrubEngineText([it.stdout, it.stderr].filter(Boolean).join("\n") || "（无输出）");
            return `<details class="ov-log-item ${bad ? "is-bad" : ""}">
              <summary>
                <span class="pill ${bad ? "bad" : "ok"}">${bad ? "失败" : "成功"}</span>
                <span class="ov-log-main">${esc(it.ts)} · ${esc(humanizeBossArgv(it.argv))}</span>
                <span class="ov-log-meta">${esc(it.elapsed_ms)}ms</span>
              </summary>
              <pre class="raw ov-log">${esc(out)}</pre>
            </details>`;
          }).join("") || `<p class="ov-hint">暂无引擎调用记录</p>`}
        </div>
      </section>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-violet">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">操作审计</h2>
            <p class="ov-desc">本机关键配置与操作流水</p>
          </div>
        </div>
        <div class="ov-audit-list">
          ${auditItems.map(it => `
            <div class="ov-audit-row">
              <span class="ov-audit-ts">${esc(it.ts)}</span>
              <span class="ov-audit-act">${esc(scrubEngineText(it.action))}</span>
              <span class="ov-audit-detail">${esc(scrubEngineText(JSON.stringify(it.detail)))}</span>
            </div>`).join("") || `<p class="ov-hint">暂无审计记录</p>`}
        </div>
      </section>
    </div>`;

  $("#logs-refresh").onclick = renderLogs;
}

function confirmModal(title, body) {
  return new Promise(resolve => {
    const m = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-body").textContent = body;
    m.classList.remove("hidden");
    const done = (v) => {
      m.classList.add("hidden");
      $("#modal-ok").onclick = null;
      $("#modal-cancel").onclick = null;
      resolve(v);
    };
    $("#modal-ok").onclick = () => done(true);
    $("#modal-cancel").onclick = () => done(false);
  });
}

function waitSecOf(s) {
  return Math.max(0, Number(s?.wait_remaining_sec) || 0);
}

function formatRemain(sec) {
  const n = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(n / 60);
  const s = n % 60;
  if (m <= 0) return `${s}s`;
  return `${m}分${String(s).padStart(2, "0")}秒`;
}

function remainFromDue(dueAt) {
  const t = Date.parse(String(dueAt || ""));
  if (!Number.isFinite(t)) return 0;
  return Math.max(0, Math.ceil((t - Date.now()) / 1000));
}

function followupListHtml(followups) {
  const list = followups || [];
  if (!list.length) {
    return `<p class="ov-hint" id="auto-follow-empty" style="margin-top:.75rem">暂无待发约面</p>`;
  }
  return `<ul class="ov-list" id="auto-follow-list">${list.map(f => {
    const rem = remainFromDue(f.due_at);
    return `<li data-due="${esc(f.due_at || "")}">
      <strong>${esc(f.candidate)}</strong>
      <span class="ov-hint auto-follow-rem" style="margin:0;color:${rem > 0 ? "#2563eb" : "#059669"};font-weight:650">
        · ${rem > 0 ? `还剩 ${formatRemain(rem)}` : "已到期，即将约面"}
      </span>
      <span class="ov-hint" style="margin:0"> · 预计 ${esc(String(f.due_at || "").replace("T", " ").slice(0, 19))}</span>
    </li>`;
  }).join("")}</ul>`;
}

function patchAutoFollowupsLocal() {
  $$("#auto-follow-list [data-due]").forEach(li => {
    const rem = remainFromDue(li.getAttribute("data-due"));
    const el = li.querySelector(".auto-follow-rem");
    if (!el) return;
    el.style.color = rem > 0 ? "#2563eb" : "#059669";
    el.textContent = rem > 0 ? `· 还剩 ${formatRemain(rem)}` : "· 已到期，即将约面";
  });
}

function patchAutoLive(s) {
  if (!$("#auto-live-run")) return;
  const running = !!s.running;
  const wait = waitSecOf(s);
  const runEl = $("#auto-live-run");
  if (runEl) {
    runEl.textContent = running ? "运行中" : "已停止";
    runEl.classList.toggle("ok", running);
    runEl.classList.toggle("muted", !running);
  }
  const dot = $("#auto-live-dot");
  if (dot) {
    dot.classList.toggle("ok", running);
    dot.classList.toggle("bad", !running);
  }
  const msg = $("#auto-live-msg");
  if (msg) msg.textContent = s.last_message || (running ? "处理中…" : "未启动");
  const skip = $("#auto-live-skip");
  if (skip) {
    if (s.last_skip_reason) {
      skip.textContent = `跳过：${s.last_skip_reason}`;
      skip.classList.remove("hidden");
    } else {
      skip.textContent = "";
      skip.classList.add("hidden");
    }
  }
  const counts = $("#auto-live-counts");
  if (counts) {
    counts.innerHTML = `处理 ${s.processed || 0} · 转人工 ${s.handoff || 0} · 错误 ${s.errors || 0}`;
  }
  const sent = $("#auto-live-sent");
  if (sent) sent.innerHTML = `${s.sent || 0} <span class="ov-sub">已发送</span>`;

  const title = $("#auto-live-wait-title");
  const stat = $("#auto-live-wait-stat");
  const hint = $("#auto-live-wait-hint");
  if (title) title.textContent = wait > 0 ? (s.wait_label || "等待中") : "等待 / 间隔";
  if (stat) {
    stat.classList.toggle("wait-tick", wait > 0);
    stat.textContent = wait > 0 ? formatRemain(wait) : "—";
  }
  if (hint) {
    hint.textContent = wait > 0
      ? `倒计时中${s.wait_label ? " · " + s.wait_label : ""}`
      : (running ? "处理中或空闲轮询间隙" : "启动后显示倒计时");
  }

  const box = $("#auto-follow-box");
  if (box) box.innerHTML = followupListHtml(s.resume_followups || []);

  const logsEl = $("#auto-live-logs");
  if (logsEl) {
    const nearBottom = logsEl.scrollHeight - logsEl.scrollTop - logsEl.clientHeight < 48;
    logsEl.textContent = (s.logs || []).join("\n") || "暂无";
    if (nearBottom) logsEl.scrollTop = logsEl.scrollHeight;
  }

  const startBtn = $("#auto-start");
  const stopBtn = $("#auto-stop");
  const onceBtn = $("#auto-once");
  if (startBtn) startBtn.disabled = running;
  if (stopBtn) stopBtn.disabled = !running;
  if (onceBtn) onceBtn.disabled = running;

  const meta = $("#auto-live-meta");
  if (meta) {
    meta.innerHTML = `${running ? "常驻循环运行中" : "循环已停止"}${s.started_at ? `　|　开始 ${esc(s.started_at)}` : ""}`;
  }
}

async function refreshAutoLive() {
  const s = await api("/api/auto/status");
  patchAutoLive(s);
  patchAutoFollowupsLocal();
  const hasFollow = (s.resume_followups || []).length > 0;
  if (!s.running && waitSecOf(s) <= 0 && !hasFollow) stopLivePoll();
}

async function renderAuto() {
  const s = await api("/api/auto/status");
  const running = !!s.running;
  const readMin = Math.round((s.resume_review?.read_min_sec || 240) / 60);
  const readMax = Math.round((s.resume_review?.read_max_sec || 360) / 60);
  const followups = s.resume_followups || [];
  const wait = waitSecOf(s);
  $("#main").innerHTML = `
    <div class="ov">
      <header class="ov-hero">
        <div class="ov-hero-text">
          <h1>自动回复</h1>
          <p>未读 → Jev → FAQ / 模板 / LLM → 立刻发送。AI直接当HR</p>
          <div class="ov-meta" id="auto-live-meta">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg>
            ${running ? "常驻循环运行中" : "循环已停止"}
            ${s.started_at ? `　|　开始 ${esc(s.started_at)}` : ""}
          </div>
        </div>
        <div class="ov-hero-actions">
          <button type="button" class="ov-btn ov-btn-primary" id="auto-start" ${running ? "disabled" : ""}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M5 3l14 9-14 9V3z"/></svg>
            启动常驻循环
          </button>
          <button type="button" class="ov-btn ov-btn-ghost" id="auto-once" ${running ? "disabled" : ""}>只跑一轮未读</button>
          <button type="button" class="ov-btn ov-btn-danger" id="auto-stop" ${running ? "" : "disabled"}>停止</button>
          <button type="button" class="ov-btn ov-btn-ghost" id="auto-refresh">刷新</button>
        </div>
        <div class="ov-hero-art" aria-hidden="true">
          <svg viewBox="0 0 200 160" fill="none">
            <ellipse cx="100" cy="142" rx="54" ry="12" fill="#3b82f6" opacity=".12"/>
            <rect x="52" y="36" width="96" height="78" rx="14" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <rect x="64" y="52" width="52" height="8" rx="4" fill="#93c5fd"/>
            <rect x="64" y="68" width="72" height="8" rx="4" fill="#bfdbfe"/>
            <rect x="64" y="84" width="40" height="8" rx="4" fill="#dbeafe"/>
            <circle cx="148" cy="96" r="18" fill="#eff6ff" stroke="#3b82f6" stroke-width="2"/>
            <path d="M142 96h12M148 90v12" stroke="#3b82f6" stroke-width="2.2" stroke-linecap="round"/>
          </svg>
        </div>
      </header>

      ${s.risk_stop ? `<div class="ov-alert ov-alert-err"><strong>已因验证码 / 风控停止</strong>，请人工处理后重新启动。</div>` : ""}

      <div class="ov-grid3">
        <article class="ov-card ov-mod">
          <span class="ov-dot ${running ? "ok" : "bad"}" id="auto-live-dot"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-orange">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0114.1-3.4L23 10M1 14l5.4 4.4A9 9 0 0020.5 15"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">循环状态</div>
              <div class="ov-mod-stat ${running ? "ok" : "muted"}" id="auto-live-run">${running ? "运行中" : "已停止"}</div>
            </div>
          </div>
          <p class="ov-desc" id="auto-live-msg">${esc(s.last_message || (running ? "处理中…" : "未启动"))}</p>
          <p class="ov-hint ov-warn ${s.last_skip_reason ? "" : "hidden"}" id="auto-live-skip">${s.last_skip_reason ? `跳过：${esc(s.last_skip_reason)}` : ""}</p>
          <p class="ov-hint">开始：${esc(s.started_at || "—")}</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-green">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-6"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">本轮计数</div>
              <div class="ov-mod-stat" id="auto-live-sent">${s.sent || 0} <span class="ov-sub">已发送</span></div>
            </div>
          </div>
          <p class="ov-hint" id="auto-live-counts">处理 ${s.processed || 0} · 转人工 ${s.handoff || 0} · 错误 ${s.errors || 0}</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-violet">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            </span>
            <div>
              <div class="ov-mod-label" id="auto-live-wait-title">${wait > 0 ? esc(s.wait_label || "等待中") : "等待 / 间隔"}</div>
              <div class="ov-mod-stat${wait > 0 ? " wait-tick" : ""}" id="auto-live-wait-stat">${wait > 0 ? formatRemain(wait) : "—"}</div>
            </div>
          </div>
          <p class="ov-hint" id="auto-live-wait-hint">${wait > 0
            ? `倒计时中${s.wait_label ? " · " + esc(s.wait_label) : ""}`
            : (running ? "处理中或空闲轮询间隙" : "启动后显示倒计时")}</p>
          <p class="ov-tags"><a class="link" data-goto="rate">发送节奏 →</a></p>
        </article>
      </div>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-violet">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">假装看简历排队</h2>
            <p class="ov-desc">同意收简历后等待 ${esc(readMin)}～${esc(readMax)} 分钟再发约面。
              <a class="link" data-goto="rate">调整时长与话术</a></p>
          </div>
        </div>
        <div id="auto-follow-box">${followupListHtml(followups)}</div>
      </section>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h10M4 18h14"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">实时日志</h2>
            <p class="ov-desc">倒计时会原地刷新，无需手动点刷新</p>
          </div>
        </div>
        <pre class="raw ov-log" id="auto-live-logs">${esc((s.logs || []).join("\n") || "暂无")}</pre>
      </section>
    </div>`;

  patchAutoLive(s);
  if (running || wait > 0 || followups.length) startLivePoll("auto", refreshAutoLive);
  else stopLivePoll();

  $("#auto-refresh").onclick = renderAuto;
  $("#auto-start").onclick = async () => {
    const ok = await confirmModal("启动自动循环", "将自动对真实候选人发消息。确认启动？");
    if (!ok) return;
    await api("/api/auto/start", { method: "POST", body: "{}" });
    await renderAuto();
    startLivePoll("auto", refreshAutoLive);
  };
  $("#auto-stop").onclick = async () => {
    await api("/api/auto/stop", { method: "POST", body: "{}" });
    stopLivePoll();
    renderAuto();
  };
  $("#auto-once").onclick = async () => {
    const ok = await confirmModal("只跑一轮", "处理当前未读，可能立刻发消息。确认？");
    if (!ok) return;
    $("#auto-once").disabled = true;
    startLivePoll("auto", refreshAutoLive);
    try {
      await api("/api/auto/once", { method: "POST", body: "{}" });
      alert("本轮已结束，请查看日志");
    } catch (err) {
      alert(err.message || err);
    }
    renderAuto();
  };
}

async function renderModelLogs() {
  const kind = state.modelLogKind || "";
  const qs = new URLSearchParams({ limit: "120" });
  if (kind) qs.set("kind", kind);
  const d = await api(`/api/logs/model?${qs}`);
  const items = d.items || [];
  const okCount = items.filter(it => it.ok).length;
  const failCount = items.length - okCount;
  const llmCount = items.filter(it => it.kind === "llm").length;
  const jevCount = items.filter(it => it.kind === "jev").length;

  $("#main").innerHTML = `
    <div class="ov">
      <header class="ov-hero">
        <div class="ov-hero-text">
          <h1>模型日志</h1>
          <p>大模型与 Jev 的请求 / 回复流水（密钥已脱敏）。用于排查判断与生成是否正常。</p>
          <div class="ov-meta">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h10M4 18h14"/></svg>
            共 ${items.length} 条（最新在上）　|　成功 ${okCount}　|　失败 ${failCount}
          </div>
        </div>
        <div class="ov-hero-actions">
          <button type="button" class="ov-btn ov-btn-ghost" id="ml-refresh">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a9 9 0 11-2.6-6.2M21 4v6h-6"/></svg>
            刷新
          </button>
          <button type="button" class="ov-btn ov-btn-danger" id="ml-clear">清空日志</button>
        </div>
        <div class="ov-hero-art" aria-hidden="true">
          <svg viewBox="0 0 200 160" fill="none">
            <ellipse cx="100" cy="142" rx="54" ry="12" fill="#3b82f6" opacity=".12"/>
            <rect x="56" y="38" width="88" height="70" rx="14" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <circle cx="88" cy="68" r="8" fill="#eff6ff" stroke="#3b82f6" stroke-width="1.8"/>
            <circle cx="112" cy="68" r="8" fill="#eff6ff" stroke="#3b82f6" stroke-width="1.8"/>
            <path d="M82 88c6 8 30 8 36 0" stroke="#3b82f6" stroke-width="2.2" stroke-linecap="round"/>
          </svg>
        </div>
      </header>

      <div class="ov-grid3">
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-blue">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">大模型</div>
              <div class="ov-mod-stat">${llmCount}</div>
            </div>
          </div>
          <p class="ov-hint">当前筛选下的 LLM 调用</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-violet">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">Jev</div>
              <div class="ov-mod-stat">${jevCount}</div>
            </div>
          </div>
          <p class="ov-hint">当前筛选下的判断调用</p>
        </article>
        <article class="ov-card ov-mod">
          <span class="ov-dot ${failCount ? "bad" : "ok"}"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ${failCount ? "ov-ico-orange" : "ov-ico-green"}">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><path d="M22 4L12 14.01l-3-3"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">成功率</div>
              <div class="ov-mod-stat ${failCount ? "" : "ok"}">${items.length ? Math.round((okCount / items.length) * 100) : "—"}%</div>
            </div>
          </div>
          <p class="ov-hint">失败 ${failCount} · 成功 ${okCount}</p>
        </article>
      </div>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h10M4 18h14"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">调用流水</h2>
            <p class="ov-desc">展开可查看请求与回复原文</p>
          </div>
        </div>
        <div class="ov-toolbar">
          <div class="ov-form-grid" style="flex:1;min-width:160px;max-width:220px">
            <div>
              <label>类型筛选</label>
              <select id="ml-kind">
                <option value="" ${!kind ? "selected" : ""}>全部</option>
                <option value="llm" ${kind === "llm" ? "selected" : ""}>仅大模型</option>
                <option value="jev" ${kind === "jev" ? "selected" : ""}>仅 Jev</option>
              </select>
            </div>
          </div>
          <span class="ov-hint" style="margin:0;align-self:flex-end;padding-bottom:.55rem">显示 ${items.length} 条</span>
        </div>
        <div class="ov-log-list">
          ${items.map((it, i) => `
            <details class="ov-log-item ${it.ok ? "" : "is-bad"}" ${i < 3 ? "open" : ""}>
              <summary>
                <span class="pill ${it.ok ? "ok" : "bad"}">${esc(it.kind)}</span>
                <span class="ov-log-main">${esc(it.ts)} · ${esc(it.purpose)} · ${esc(it.model || "")}${it.ok ? "" : " · 失败"}</span>
                <span class="ov-log-meta">${esc(it.elapsed_ms ?? "—")}ms</span>
              </summary>
              <p class="ov-hint">endpoint: ${esc(it.endpoint || "")}</p>
              ${it.error ? `<div class="ov-alert ov-alert-err" style="margin:.5rem 0">${esc(it.error)}</div>` : ""}
              <h3 class="ov-subhead">请求</h3>
              <pre class="raw ov-log">${esc(typeof it.request === "string" ? it.request : JSON.stringify(it.request, null, 2))}</pre>
              <h3 class="ov-subhead">回复</h3>
              <pre class="raw ov-log">${esc(it.response == null ? "(无)" : (typeof it.response === "string" ? it.response : JSON.stringify(it.response, null, 2)))}</pre>
            </details>
          `).join("") || `<div class="ov-empty">
            <strong>暂无模型调用记录</strong>
            <p>去总控台点测试连接，或跑一轮自动回复即可产生。</p>
          </div>`}
        </div>
      </section>
    </div>`;

  $("#ml-kind").onchange = () => {
    state.modelLogKind = $("#ml-kind").value;
    renderModelLogs();
  };
  $("#ml-refresh").onclick = renderModelLogs;
  $("#ml-clear").onclick = async () => {
    if (!confirm("清空全部模型日志？")) return;
    await api("/api/logs/model", { method: "DELETE" });
    renderModelLogs();
  };
}

function greetWaitSec(s) {
  return waitSecOf(s);
}

function patchGreetLive(s) {
  if (!$("#g-live-run")) return;
  const wait = greetWaitSec(s);
  const qTotal = s.queue_total || (s.queue || []).length || 0;
  const cursor = s.cursor || 0;
  const progressLabel = qTotal ? `${Math.min(cursor, qTotal)}/${qTotal}` : "—";
  const nextLabel = s.next_name || (s.running ? "（排队中）" : "—");

  $("#g-live-run").textContent = s.running ? (s.yielding ? "让出中" : "运行中") : "已停止";
  $("#g-live-msg").textContent = s.last_message || "";
  $("#g-live-progress").textContent = progressLabel;
  $("#g-live-next").textContent = `下一位：${nextLabel}`;
  $("#g-live-round").textContent =
    `本轮成功 ${s.greeted || 0} · 跳过 ${s.skipped || 0} · 错误 ${s.errors || 0}`;

  const title = $("#g-live-wait-title");
  const stat = $("#g-live-wait-stat");
  const hint = $("#g-live-wait-hint");
  if (title) title.textContent = wait > 0 ? (s.wait_label || "换人歇一歇") : "今日 / 换人歇一歇";
  if (stat) {
    stat.classList.toggle("wait-tick", wait > 0);
    stat.textContent = wait > 0
      ? formatRemain(wait)
      : `${s.today_greet || 0} / ${s.daily_max || 30}`;
  }
  if (hint) {
    hint.textContent = wait > 0
      ? `倒计时中${s.wait_label ? " · " + s.wait_label : ""} · 今日 ${s.today_greet || 0}/${s.daily_max || 30}`
      : `间隔 ${s.interval_min_sec}–${s.interval_max_sec}s`;
  }

  const logsEl = $("#g-live-logs");
  if (logsEl) {
    const nearBottom = logsEl.scrollHeight - logsEl.scrollTop - logsEl.clientHeight < 48;
    logsEl.textContent = (s.logs || []).join("\n") || "暂无";
    if (nearBottom) logsEl.scrollTop = logsEl.scrollHeight;
  }

  const startBtn = $("#g-start");
  const stopBtn = $("#g-stop");
  const fetchBtn = $("#g-fetch");
  if (startBtn) startBtn.disabled = !!s.running;
  if (stopBtn) stopBtn.disabled = !s.running;
  if (fetchBtn) fetchBtn.disabled = !!s.running;
}

async function refreshGreetLive() {
  const s = await api("/api/greet/status");
  patchGreetLive(s);
  if (!s.running && greetWaitSec(s) <= 0) stopLivePoll();
}

async function renderGreet() {
  const s = await api("/api/greet/status");
  const cands = s.candidates || [];
  const sch = s.schedule || {
    greet_start: s.window_start || "",
    greet_end: s.window_end || "",
    reply_start: "",
    reply_end: "",
  };
  const qTotal = s.queue_total || (s.queue || []).length || 0;
  const cursor = s.cursor || 0;
  const progressLabel = qTotal
    ? `${Math.min(cursor, qTotal)}/${qTotal}`
    : "—";
  const nextLabel = s.next_name || (s.running ? "（排队中）" : "—");
  const greetNow = sch.greet_active_now ? "打招呼中" : "非打招呼时段";
  const replyNow = sch.reply_active_now ? "回消息中" : "非回消息时段";
  const wait = greetWaitSec(s);
  const running = !!s.running;
  const can = cands.filter(c => c.can_greet);
  const matched = can.filter(c => c.filter_ok !== false);
  const filtBits = [];
  if (s.age_min || s.age_max) filtBits.push(`年龄${s.age_min || "?"}–${s.age_max || "?"}`);
  if (s.gender === "female") filtBits.push("女");
  else if (s.gender === "male") filtBits.push("男");
  if (s.keywords) filtBits.push("含:" + s.keywords);
  if (s.keywords_exclude) filtBits.push("排除:" + s.keywords_exclude);
  const filtLabel = filtBits.length ? filtBits.join(" · ") : "未设筛选";

  $("#main").innerHTML = `
    <div class="ov">
      <header class="ov-hero">
        <div class="ov-hero-text">
          <h1>自动打招呼</h1>
     
          <div class="ov-meta">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            当前：${esc(greetNow)} · ${esc(replyNow)}
          </div>
        </div>
        <div class="ov-hero-actions">
          <button type="button" class="ov-btn ov-btn-ghost" id="g-fetch" ${running ? "disabled" : ""}>刷新推荐列表</button>
          <button type="button" class="ov-btn ov-btn-primary" id="g-start" ${running ? "disabled" : ""}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M5 3l14 9-14 9V3z"/></svg>
            开始自动打招呼
          </button>
          <button type="button" class="ov-btn ov-btn-danger" id="g-stop" ${running ? "" : "disabled"}>停止</button>
          <button type="button" class="ov-btn ov-btn-ghost" id="g-refresh">刷新状态</button>
        </div>
        <div class="ov-hero-art" aria-hidden="true">
          <svg viewBox="0 0 200 160" fill="none">
            <ellipse cx="100" cy="142" rx="54" ry="12" fill="#3b82f6" opacity=".12"/>
            <circle cx="78" cy="72" r="22" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <circle cx="122" cy="72" r="22" fill="#fff" stroke="#93c5fd" stroke-width="2.5"/>
            <circle cx="100" cy="98" r="26" fill="#fff" stroke="#3b82f6" stroke-width="2.5"/>
            <path d="M92 98h16M100 90v16" stroke="#3b82f6" stroke-width="2.2" stroke-linecap="round"/>
          </svg>
        </div>
      </header>

      ${s.risk_stop ? `<div class="ov-alert ov-alert-err"><strong>已因验证码 / 风控停止</strong>，请人工处理后重新启动。</div>` : ""}

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">工作日程（互斥）</h2>
          </div>
        </div>
        <div class="sch-presets">
          ${SCHEDULE_PRESETS.map(p => `<button type="button" class="sch-preset ov-btn ov-btn-ghost" data-preset="${esc(p.id)}">${esc(p.name)}</button>`).join("")}
        </div>
        <div class="time-chips">
          ${timeChipHtml("sch-gs", "打招呼开始", sch.greet_start || "")}
          ${timeChipHtml("sch-ge", "打招呼结束", sch.greet_end || "")}
          ${timeChipHtml("sch-rs", "回消息开始", sch.reply_start || "")}
          ${timeChipHtml("sch-re", "回消息结束", sch.reply_end || "")}
        </div>
        <input type="hidden" id="sch-gs" value="${esc(sch.greet_start || "")}" />
        <input type="hidden" id="sch-ge" value="${esc(sch.greet_end || "")}" />
        <input type="hidden" id="sch-rs" value="${esc(sch.reply_start || "")}" />
        <input type="hidden" id="sch-re" value="${esc(sch.reply_end || "")}" />
        <div id="schedule-preview"></div>
        <div id="schedule-tip" class="ov-hint mt"></div>
      </section>

      <div class="ov-grid3">
        <article class="ov-card ov-mod">
          <span class="ov-dot ${running ? "ok" : "bad"}"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-orange">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0114.1-3.4L23 10M1 14l5.4 4.4A9 9 0 0020.5 15"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">任务状态</div>
              <div class="ov-mod-stat ${running ? "ok" : "muted"}" id="g-live-run">${running ? (s.yielding ? "让出中" : "运行中") : "已停止"}</div>
            </div>
          </div>
          <p class="ov-desc" id="g-live-msg">${esc(s.last_message || "")}</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-green">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">队列进度</div>
              <div class="ov-mod-stat" id="g-live-progress">${esc(progressLabel)}</div>
            </div>
          </div>
          <p class="ov-hint" id="g-live-next">下一位：${esc(nextLabel)}</p>
          <p class="ov-hint" id="g-live-round">本轮成功 ${esc(s.greeted || 0)} · 跳过 ${esc(s.skipped || 0)} · 错误 ${esc(s.errors || 0)}</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-violet">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            </span>
            <div>
              <div class="ov-mod-label" id="g-live-wait-title">${wait > 0 ? esc(s.wait_label || "换人歇一歇") : "今日 / 换人歇一歇"}</div>
              <div class="ov-mod-stat${wait > 0 ? " wait-tick" : ""}" id="g-live-wait-stat">${wait > 0 ? formatRemain(wait) : `${esc(s.today_greet || 0)} / ${esc(s.daily_max || 30)}`}</div>
            </div>
          </div>
          <p class="ov-hint" id="g-live-wait-hint">${wait > 0
            ? `倒计时中${s.wait_label ? " · " + esc(s.wait_label) : ""} · 今日 ${esc(s.today_greet || 0)}/${esc(s.daily_max || 30)}`
            : `间隔 ${esc(s.interval_min_sec)}–${esc(s.interval_max_sec)}s`}</p>
          <p class="ov-tags"><a class="link" data-goto="rate">去改发送节奏 →</a></p>
        </article>
      </div>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-blue">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">其它配置</h2>
            <p class="ov-desc">上限、岗位关键字与推荐筛选；保存后刷新列表才生效。</p>
          </div>
        </div>
        <div class="ov-form-grid two">
          <div><label>每日打招呼上限</label>
            <input id="g-daily" type="number" min="0" value="${esc(s.daily_max ?? 30)}" /></div>
          <div><label>岗位关键字（可选）</label>
            <input id="g-job" type="text" value="${esc(s.job_keyword || "")}" placeholder="如：前台" /></div>
        </div>
        <h3 class="ov-subhead">推荐筛选</h3>
        <p class="ov-hint" style="margin-top:0">刷新列表后按此过滤；开局队列只打「匹配」的人。性别从头像角标读取（蓝♂男 / 粉♀女）。</p>
        <div class="ov-form-grid three mt">
          <div><label>年龄下限</label>
            <input id="g-age-min" type="number" min="16" max="65" placeholder="如 18"
              value="${esc(s.age_min ?? "")}" /></div>
          <div><label>年龄上限</label>
            <input id="g-age-max" type="number" min="16" max="65" placeholder="如 40"
              value="${esc(s.age_max ?? "")}" /></div>
          <div><label>性别</label>
            <select id="g-gender">
              <option value="any" ${!s.gender || s.gender === "any" ? "selected" : ""}>不限</option>
              <option value="female" ${s.gender === "female" ? "selected" : ""}>女</option>
              <option value="male" ${s.gender === "male" ? "selected" : ""}>男</option>
            </select></div>
        </div>
        <div class="ov-form-grid two mt">
          <div><label>必须含关键词（逗号分隔）</label>
            <input id="g-kw" type="text" value="${esc(s.keywords || "")}"
              placeholder="如：前台,客服,销售" /></div>
          <div><label>排除关键词（逗号分隔）</label>
            <input id="g-kw-ex" type="text" value="${esc(s.keywords_exclude || "")}"
              placeholder="如：兼职,实习" /></div>
        </div>
        <div class="ov-checks">
          <label class="check"><input id="g-enabled" type="checkbox" ${s.enabled !== false ? "checked" : ""} /> 启用自动打招呼</label>
          <label class="check"><input id="g-only" type="checkbox" ${s.only_can_greet !== false ? "checked" : ""} /> 只打「可打招呼」的人</label>
        </div>
        <div class="ov-actions mt">
          <button type="button" class="ov-btn ov-btn-primary" id="g-save">保存配置</button>
        </div>
      </section>

      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-green">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">推荐候选人（${cands.length}）</h2>
            <p class="ov-desc">筛选：${esc(filtLabel)} · 可打 ${can.length} · 匹配 ${matched.length}</p>
          </div>
        </div>
        <div class="ov-cand-list">
          ${cands.map(c => {
            const ok = c.filter_ok !== false;
            const checked = c.can_greet && ok ? "checked" : "";
            const disabled = c.can_greet && ok ? "" : "disabled";
            const ageLabel = c.age != null ? `${c.age}岁` : "年龄?";
            const genderLabel = c.gender === "female" ? "女" : c.gender === "male" ? "男" : "性别?";
            const matchPill = !c.can_greet
              ? `<span class="pill">已打招呼/不可</span>`
              : ok
                ? `<span class="pill ok">匹配</span>`
                : `<span class="pill bad">不匹配</span>`;
            const meta = [ageLabel, genderLabel, c.expect || ""].filter(Boolean).join(" · ");
            return `<label class="ov-cand ${ok && c.can_greet ? "" : "is-dim"}">
              <input type="checkbox" data-greet-name="${esc(c.name)}" ${checked} ${disabled} />
              <span class="ov-cand-body">
                <span class="ov-cand-name">${esc(c.index)}. ${esc(c.name)} ${matchPill}</span>
                <span class="ov-cand-meta">${esc(meta)}</span>
              </span>
            </label>`;
          }).join("") || `<p class="ov-hint">尚未拉取，点「刷新推荐列表」</p>`}
        </div>
        <p class="ov-hint">请在<strong>打招呼时段内</strong>启动。本批打完且未达今日上限时，会自动刷新推荐页继续；达上限 / 静默 / 风控 / 连续空页才停。回消息请放到另一时段。</p>
        <h3 class="ov-subhead">日志</h3>
        <pre class="raw ov-log" id="g-live-logs">${esc((s.logs || []).join("\n") || "暂无")}</pre>
      </section>
    </div>`;

  bindSchedulePreview();
  patchGreetLive(s);
  if (s.running || greetWaitSec(s) > 0) startLivePoll("greet", refreshGreetLive);
  else stopLivePoll();
  $("#g-refresh").onclick = renderGreet;
  $("#g-save").onclick = async () => {
    const ageMinRaw = $("#g-age-min").value.trim();
    const ageMaxRaw = $("#g-age-max").value.trim();
    const payload = {
      enabled: $("#g-enabled").checked,
      daily_max: +$("#g-daily").value,
      job_keyword: $("#g-job").value.trim(),
      only_can_greet: $("#g-only").checked,
      age_min: ageMinRaw === "" ? null : +ageMinRaw,
      age_max: ageMaxRaw === "" ? null : +ageMaxRaw,
      gender: $("#g-gender").value || "any",
      keywords: $("#g-kw").value.trim(),
      keywords_exclude: $("#g-kw-ex").value.trim(),
      greet_start: $("#sch-gs").value || "",
      greet_end: $("#sch-ge").value || "",
      reply_start: $("#sch-rs").value || "",
      reply_end: $("#sch-re").value || "",
      window_start: $("#sch-gs").value || "",
      window_end: $("#sch-ge").value || "",
      unread_first: false,
      pause_reply_during_greet: false,
      exclusive_window: false,
    };
    if (windowsOverlap(payload.greet_start, payload.greet_end, payload.reply_start, payload.reply_end)) {
      return alert("打招呼与回消息时段有重叠，请先改成互不交叉再保存。");
    }
    try {
      await api("/api/greet/config", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      alert("已保存");
      renderGreet();
    } catch (e) {
      alert(e.message || e);
    }
  };
  $("#g-fetch").onclick = async () => {
    $("#g-fetch").disabled = true;
    try {
      const job = $("#g-job").value.trim();
      await api("/api/greet/recommend", {
        method: "POST",
        body: JSON.stringify({ job_keyword: job || null }),
      });
      renderGreet();
    } catch (e) {
      alert(e.message || e);
      $("#g-fetch").disabled = false;
    }
  };
  $("#g-start").onclick = async () => {
    const names = $$("[data-greet-name]:checked").map(el => el.dataset.greetName).filter(Boolean);
    const tip = names.length
      ? `将按勾选先打一批（${names.length} 人）；打完未达今日上限会刷新推荐继续。今日 ${s.today_greet}/${s.daily_max}。`
      : `将按筛选「匹配」排队列开打；本批打完未达今日上限会刷新推荐继续。今日 ${s.today_greet}/${s.daily_max}。`;
    const ok = await confirmModal(
      "开始自动打招呼",
      tip + "\n请在打招呼时段内运行；回消息请放到另一时段。",
    );
    if (!ok) return;
    try {
      await api("/api/greet/start", {
        method: "POST",
        body: JSON.stringify({ confirmed: true, names: names.length ? names : null }),
      });
      await renderGreet();
      startLivePoll("greet", refreshGreetLive);
    } catch (e) {
      alert(e.message || e);
    }
  };
  $("#g-stop").onclick = async () => {
    await api("/api/greet/stop", { method: "POST", body: "{}" });
    stopLivePoll();
    renderGreet();
  };
}

const pages = {
  overview: renderOverview,
  auto: renderAuto,
  greet: renderGreet,
  // positions 由 _models_positions.js 注册
  faq: renderFaq,
  policy: renderPolicy,
  rate: renderRate,
  modelogs: renderModelLogs,
  positions: renderOverview,
  logs: renderLogs,
};

function go(page) {
  if (page !== "greet" && page !== "auto") stopLivePoll();
  state.page = page;
  $$(".nav button").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  const fn = pages[page];
  if (!fn) {
    $("#main").innerHTML = `<div class="error-box">未知页面：${esc(page)}</div>`;
    return;
  }
  fn().catch(e => {
    $("#main").innerHTML = `<div class="error-box">${esc(e.message || e)}</div>`;
  });
}

$$(".nav button").forEach(b => b.addEventListener("click", () => go(b.dataset.page)));
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-goto]");
  if (a) go(a.dataset.goto);
});

// 首屏：先查卡密状态，未激活只显示激活页
window.__adminReady = async function adminReady() {
  try {
    const st = await api("/api/license/status");
    state.license = st;
    if (!st.activated && !st.bypass) {
      renderLicenseGate(st);
      return;
    }
    if (st.locked && !st.bypass) {
      renderLicenseGate(st, "卡密已失效或心跳失败，请重新激活");
      return;
    }
    showAppShell();
    // 仅激活后自动拉起扫码；日常进概览不再抢 boss 锁（否则 /api/overview 会卡数秒）
    const afterActivate = sessionStorage.getItem("boss_login_after_activate") === "1";
    sessionStorage.removeItem("boss_login_after_activate");
    if (afterActivate) {
      openBossLoginSoon({ force: true });
    }
    // 非阻塞检查更新
    checkClientUpdateQuiet();
    go(state.page || "overview");
  } catch (e) {
    // 兼容旧后端无 license API：直接进主界面
    showAppShell();
    go(state.page || "overview");
  }
};

async function openBossLoginSoon(opts = {}) {
  const force = !!opts.force;
  if (!force && sessionStorage.getItem("boss_login_opened") === "1") return;
  sessionStorage.setItem("boss_login_opened", "1");
  // 等概览先渲染完，再后台拉起登录，避免和 /api/overview 抢锁
  setTimeout(() => { openBossLogin({ silent: !force }); }, force ? 1200 : 1500);
}

async function openBossLogin(opts = {}) {
  const silent = !!opts.silent;
  try {
    const r = await api("/api/boss/open-login", { method: "POST", body: "{}" });
    if (r.need_chrome) {
      if (!silent) alert(r.human || "请先安装 Google Chrome");
      return r;
    }
    if (!silent) {
      alert(r.human || "已打开登录页，请扫码登录");
    } else if (r.ok) {
      console.info("[zlxm]", r.human);
    }
    return r;
  } catch (e) {
    if (!silent) alert("无法打开登录页：" + (e.message || e));
    return { ok: false, human: String(e.message || e) };
  }
}

function showAppShell() {
  document.body.classList.remove("gate-on");
  $("#gate")?.classList.add("hidden");
  $("#app-shell")?.classList.remove("hidden");
}

function authHeroHtml() {
  return `
    <section class="auth-hero">
      <div class="auth-brand">
        <div class="auth-logo" aria-hidden="true">
          <svg viewBox="0 0 48 48" fill="none"><rect width="48" height="48" rx="14" fill="currentColor" opacity="0"/><circle cx="24" cy="20" r="10" stroke="#fff" stroke-width="2.4"/><path d="M14 34c2.8-5 7-7.5 10-7.5S31.2 29 34 34" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><circle cx="20" cy="19" r="1.6" fill="#fff"/><circle cx="28" cy="19" r="1.6" fill="#fff"/></svg>
        </div>
        <div class="auth-brand-text">
          <strong>直聊小蜜</strong>
          <span>智能招聘自动化产品</span>
        </div>
      </div>
      <div class="auth-hero-body">
        <h2>让招聘更简单<br><em>用直聊小蜜</em></h2>
        <p>基于海量人才数据与 AI 能力，提供自动筛选、智能沟通与面试安排，让招聘更高效。</p>
        <div class="auth-feats">
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg></i><b>自动筛选</b><span>高效匹配人才</span></div>
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a8 8 0 01-11.5 7.2L4 20l.9-4.2A8 8 0 1121 12z"/></svg></i><b>智能沟通</b><span>自动发起对话</span></div>
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg></i><b>面试安排</b><span>一站式管理</span></div>
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19V5M4 19h16M8 15v4M12 10v9M16 7v12"/></svg></i><b>数据洞察</b><span>效果可视化</span></div>
        </div>
      </div>
      <div class="auth-art" aria-hidden="true">
        <div class="auth-float af1">候选人 · <strong>匹配 92%</strong><br><span class="muted">3 年经验 · 即时沟通</span></div>
        <div class="auth-float af2">今日跟进 <strong>28</strong><br><span class="muted">已约面 6</span></div>
        <div class="auth-float af3">小蜜：您好，看到您的简历…</div>
        <div class="auth-art-bot">
          <svg viewBox="0 0 160 160" fill="none">
            <ellipse cx="80" cy="138" rx="46" ry="10" fill="#12b7a7" opacity=".18"/>
            <rect x="48" y="78" width="64" height="52" rx="18" fill="#fff" stroke="#12b7a7" stroke-width="3"/>
            <circle cx="80" cy="52" r="28" fill="#fff" stroke="#12b7a7" stroke-width="3"/>
            <circle cx="70" cy="50" r="3.5" fill="#12b7a7"/><circle cx="90" cy="50" r="3.5" fill="#12b7a7"/>
            <path d="M70 62c3 3 7 3 10 0" stroke="#12b7a7" stroke-width="2.5" stroke-linecap="round"/>
            <rect x="58" y="96" width="44" height="28" rx="6" fill="#e7f8f6" stroke="#12b7a7" stroke-width="2"/>
            <rect x="66" y="104" width="18" height="3" rx="1.5" fill="#12b7a7" opacity=".5"/>
            <rect x="66" y="111" width="28" height="3" rx="1.5" fill="#12b7a7" opacity=".35"/>
          </svg>
        </div>
      </div>
    </section>`;
}

function renderLicenseGate(st, errMsg) {
  document.body.classList.add("gate-on");
  $("#app-shell")?.classList.add("hidden");
  const gate = $("#gate");
  if (!gate) return;
  gate.classList.remove("hidden");
  const ver = (st && st.version) || "";
  gate.innerHTML = `
    <div class="auth-split">
      ${authHeroHtml()}
      <section class="auth-panel">
        <div class="auth-card">
          <h1>欢迎登录 <em>直聊小蜜</em></h1>
          <p class="auth-sub">开启智能招聘新体验</p>
          ${errMsg ? `<div class="error-box">${esc(errMsg)}</div>` : ""}
          <div class="auth-field">
            <span class="ico" aria-hidden="true"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V8a5 5 0 0110 0v3"/></svg></span>
            <input id="lic-key" placeholder="请输入卡密" autocomplete="off" autofocus />
          </div>
          <button class="auth-btn" id="lic-go" type="button">登录</button>
          <div id="lic-err" class="mt"></div>
          <p class="auth-meta">登录即表示您已同意服务条款 · ${esc(ver ? "v" + ver : "")}</p>
        </div>
      </section>
    </div>`;
  const submit = async () => {
    const license_key = $("#lic-key").value.trim();
    const btn = $("#lic-go");
    const err = $("#lic-err");
    if (!license_key) {
      err.innerHTML = `<div class="error-box">请输入卡密</div>`;
      return;
    }
    btn.disabled = true;
    err.innerHTML = `<p class="muted">激活中…</p>`;
    try {
      const r = await api("/api/license/activate", {
        method: "POST",
        body: JSON.stringify({ license_key }),
      });
      sessionStorage.setItem("boss_login_after_activate", "1");
      sessionStorage.removeItem("boss_login_opened");
      if (r.announcement) alert(r.announcement);
      else if (r.human) alert(r.human);
      location.reload();
    } catch (e) {
      btn.disabled = false;
      err.innerHTML = `<div class="error-box">${esc(e.message || e)}</div>`;
    }
  };
  $("#lic-go").onclick = submit;
  $("#lic-key").onkeydown = (e) => { if (e.key === "Enter") submit(); };
}

async function checkClientUpdateQuiet() {
  try {
    const u = await api("/api/license/check-update");
    state.updateInfo = u;
    if (u.has_update && u.force) {
      showForceUpdate(u);
    }
  } catch { /* ignore */ }
}

function showForceUpdate(u) {
  const gate = $("#gate");
  if (!gate) return;
  document.body.classList.add("gate-on");
  $("#app-shell")?.classList.add("hidden");
  gate.classList.remove("hidden");
  gate.innerHTML = `
    <div class="auth-split">
      ${authHeroHtml()}
      <section class="auth-panel">
        <div class="auth-card">
          <h1>需要更新</h1>
          <p class="auth-sub">当前 ${esc(u.current)}，最新 ${esc(u.latest)}</p>
          <div class="warn-box">${esc(u.notes || "管理员已开启强制更新，请升级后继续使用。")}</div>
          <button class="auth-btn" id="upd-go" type="button">下载并安装</button>
          <div id="upd-msg" class="mt"></div>
        </div>
      </section>
    </div>`;
  $("#upd-go").onclick = async () => {
    const msg = $("#upd-msg");
    try {
      await applyClientUpdateWithProgress(msg, {
        onDone: (err, r) => {
          if (err) {
            msg.innerHTML = `<div class="error-box">${esc(err.message || err)}</div>`;
          } else {
            msg.innerHTML = `<div class="ok-box">${esc(r.human || "已启动安装")}</div>`;
          }
        },
      });
    } catch { /* shown via onDone */ }
  };
}
