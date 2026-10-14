/* 模型与判断已迁至总控台 :8787；本文件仅保留职位 / FAQ。 */

function faqItemHtml(it, i, qAttr, aAttr) {
  return `
    <div class="kb-item" data-faq-i="${i}">
      <div class="kb-idx">${i + 1}</div>
      <div class="kb-fields">
        <div>
          <label>问题标题</label>
          <input ${qAttr}="${i}" value="${esc(it.question || "")}" placeholder="例如：薪资范围、上班时间" />
        </div>
        <div>
          <label>标准答案</label>
          <textarea ${aAttr}="${i}" rows="3" placeholder="作为口径依据的标准回复">${esc(it.answer || "")}</textarea>
        </div>
      </div>
      <div class="kb-item-actions">
        <button type="button" class="ov-btn ov-btn-ghost" data-faq-del>删除</button>
      </div>
    </div>`;
}

function collectFaqByAttrs(qAttr, aAttr) {
  return $$(`[${qAttr}]`).map((el) => {
    const i = el.getAttribute(qAttr);
    return {
      question: el.value.trim(),
      answer: ($(`[${aAttr}="${i}"]`)?.value || "").trim(),
    };
  }).filter(x => x.question);
}

function renumberFaqList(box) {
  if (!box) return;
  $$(".kb-item, .pos-faq-item", box).forEach((row, idx) => {
    const badge = row.querySelector(".kb-idx");
    if (badge) badge.textContent = String(idx + 1);
  });
}

function applyFaqFilter(query) {
  const q = (query || "").trim().toLowerCase();
  const box = $("#common-faq-items");
  if (!box) return;
  let shown = 0;
  $$(".kb-item", box).forEach((row) => {
    const text = row.textContent.toLowerCase();
    const inputs = $$("input, textarea", row).map(el => el.value).join(" ").toLowerCase();
    const hit = !q || text.includes(q) || inputs.includes(q);
    row.classList.toggle("hidden", !hit);
    if (hit) shown += 1;
  });
  const hint = $("#faq-filter-hint");
  const total = $$(".kb-item", box).length;
  if (hint) hint.textContent = q ? `显示 ${shown} / ${total}` : "保存时自动备份旧版";
  const empty = $("#faq-filter-empty");
  if (empty) empty.classList.toggle("hidden", !q || shown > 0 || total === 0);
}

function showCommonFaqPreview(items) {
  const box = $("#faq-preview");
  if (!box) return;
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="ov-card kb-preview" style="margin-top:0">
      <h3 class="ov-sec-title">导入预览</h3>
      <p class="ov-desc">确认后才会覆盖写入公共 FAQ；可取消勾选或就地编辑。</p>
      <div class="kb-preview-list">
        ${items.map((it, i) => `
          <div class="kb-preview-row">
            <input type="checkbox" checked data-keep="${i}" />
            <div class="kb-fields">
              <div>
                <label>问题</label>
                <input data-iq="${i}" value="${esc(it.question)}" />
              </div>
              <div>
                <label>答案</label>
                <textarea data-ia="${i}" rows="2">${esc(it.answer)}</textarea>
              </div>
            </div>
          </div>`).join("") || '<p class="ov-hint">未解析到条目</p>'}
      </div>
      <div class="ov-actions mt">
        <button type="button" class="ov-btn ov-btn-primary" id="faq-activate">确认覆盖写入</button>
        <button type="button" class="ov-btn ov-btn-ghost" id="faq-cancel-preview">取消</button>
        <span class="ov-hint" style="margin:0;align-self:center">共 ${items.length} 条待确认</span>
      </div>
    </div>`;
  $("#faq-cancel-preview").onclick = () => {
    box.classList.add("hidden");
    box.innerHTML = "";
  };
  $("#faq-activate").onclick = async () => {
    const next = [];
    items.forEach((_, i) => {
      if (!$(`[data-keep="${i}"]`)?.checked) return;
      next.push({
        question: ($(`[data-iq="${i}"]`)?.value || "").trim(),
        answer: ($(`[data-ia="${i}"]`)?.value || "").trim(),
      });
    });
    await api("/api/faq", { method: "PUT", body: JSON.stringify({ items: next.filter(x => x.question) }) });
    alert("公共 FAQ 已更新");
    state.posTab = "faq";
    renderPositions();
  };
}

function showPosAiFaqPreview(posId, items, modelTip) {
  const box = $("#pos-ai-box");
  if (!box) return;
  const list = items || [];
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="ov-card" style="margin:.75rem 0 0;padding:.9rem 1rem;background:#f8fbff;border-color:#bfdbfe">
      <div class="ov-sec" style="margin-bottom:.55rem">
        <span class="ov-ico ov-ico-violet">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.8 5.5L19 10l-5.2 1.5L12 17l-1.8-5.5L5 10l5.2-1.5L12 3z"/></svg>
        </span>
        <div>
          <h3 class="ov-sec-title" style="font-size:1rem">AI 推荐预览</h3>
          <p class="ov-desc">勾选要采纳的条目，写入专属 FAQ（不会覆盖未勾选的已有内容）。${modelTip ? ` · ${esc(modelTip)}` : ""}</p>
        </div>
      </div>
      <div class="kb-preview-list" style="max-height:360px">
        ${list.map((it, i) => `
          <div class="kb-preview-row">
            <input type="checkbox" checked data-pai-keep="${i}" />
            <div class="kb-fields">
              <div>
                <label>问题</label>
                <input data-pai-q="${i}" value="${esc(it.question || "")}" />
              </div>
              <div>
                <label>答案</label>
                <textarea data-pai-a="${i}" rows="2">${esc(it.answer || "")}</textarea>
              </div>
            </div>
          </div>`).join("") || `<p class="ov-hint">未生成条目</p>`}
      </div>
      <div class="ov-actions mt">
        <button type="button" class="ov-btn ov-btn-primary" id="pos-ai-apply">采纳选中并写入</button>
        <button type="button" class="ov-btn ov-btn-ghost" id="pos-ai-dismiss">不采纳</button>
        <span class="ov-hint" style="margin:0;align-self:center">共 ${list.length} 条</span>
      </div>
    </div>`;

  $("#pos-ai-dismiss").onclick = () => {
    box.classList.add("hidden");
    box.innerHTML = "";
  };
  $("#pos-ai-apply").onclick = async () => {
    const picked = [];
    list.forEach((_, i) => {
      if (!$(`[data-pai-keep="${i}"]`)?.checked) return;
      const q = ($(`[data-pai-q="${i}"]`)?.value || "").trim();
      const a = ($(`[data-pai-a="${i}"]`)?.value || "").trim();
      if (q) picked.push({ question: q, answer: a });
    });
    if (!picked.length) return alert("请至少勾选一条");
    const existing = collectFaqByAttrs("data-pq", "data-pa");
    const byQ = new Map(existing.map(it => [it.question, it]));
    picked.forEach(it => byQ.set(it.question, it)); // 同标题覆盖
    const merged = [...byQ.values()];
    try {
      await api(`/api/positions/${encodeURIComponent(posId)}/faq`, {
        method: "PUT",
        body: JSON.stringify({ items: merged }),
      });
      alert(`已写入 ${picked.length} 条 AI 推荐（专属 FAQ 共 ${merged.length} 条）`);
      renderPositions();
    } catch (e) {
      alert(e.message || e);
    }
  };
}

function showCommonAiFaqPreview(items, modelTip, industry) {
  const box = $("#common-ai-box");
  if (!box) return;
  const list = items || [];
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="ov-card" style="margin:.55rem 0 .85rem;padding:.9rem 1rem;background:#f8fbff;border-color:#bfdbfe">
      <div class="ov-sec" style="margin-bottom:.55rem">
        <span class="ov-ico ov-ico-violet">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.8 5.5L19 10l-5.2 1.5L12 17l-1.8-5.5L5 10l5.2-1.5L12 3z"/></svg>
        </span>
        <div>
          <h3 class="ov-sec-title" style="font-size:1rem">公共 FAQ · AI 推荐预览</h3>
          <p class="ov-desc">行业：${esc(industry || "—")}。勾选要采纳的条目后写入公共 FAQ（同标题覆盖，其余保留）。${modelTip ? ` · ${esc(modelTip)}` : ""}</p>
        </div>
      </div>
      <div class="kb-preview-list" style="max-height:420px">
        ${list.map((it, i) => `
          <div class="kb-preview-row">
            <input type="checkbox" checked data-cai-keep="${i}" />
            <div class="kb-fields">
              <div>
                <label>问题</label>
                <input data-cai-q="${i}" value="${esc(it.question || "")}" />
              </div>
              <div>
                <label>答案</label>
                <textarea data-cai-a="${i}" rows="2">${esc(it.answer || "")}</textarea>
              </div>
            </div>
          </div>`).join("") || `<p class="ov-hint">未生成条目</p>`}
      </div>
      <div class="ov-actions mt">
        <button type="button" class="ov-btn ov-btn-primary" id="common-ai-apply">采纳选中并写入</button>
        <button type="button" class="ov-btn ov-btn-ghost" id="common-ai-dismiss">不采纳</button>
        <span class="ov-hint" style="margin:0;align-self:center">共 ${list.length} 条</span>
      </div>
    </div>`;

  $("#common-ai-dismiss").onclick = () => {
    box.classList.add("hidden");
    box.innerHTML = "";
  };
  $("#common-ai-apply").onclick = async () => {
    const picked = [];
    list.forEach((_, i) => {
      if (!$(`[data-cai-keep="${i}"]`)?.checked) return;
      const q = ($(`[data-cai-q="${i}"]`)?.value || "").trim();
      const a = ($(`[data-cai-a="${i}"]`)?.value || "").trim();
      if (q) picked.push({ question: q, answer: a });
    });
    if (!picked.length) return alert("请至少勾选一条");
    const existing = collectFaqByAttrs("data-cq", "data-ca");
    const byQ = new Map(existing.map(it => [it.question, it]));
    picked.forEach(it => byQ.set(it.question, it));
    const merged = [...byQ.values()];
    try {
      await api("/api/faq", { method: "PUT", body: JSON.stringify({ items: merged }) });
      alert(`已写入 ${picked.length} 条 AI 推荐（公共 FAQ 共 ${merged.length} 条）`);
      state.posTab = "faq";
      renderPositions();
    } catch (e) {
      alert(e.message || e);
    }
  };
}

async function renderPositions() {
  const [pos, faq] = await Promise.all([api("/api/positions"), api("/api/faq")]);
  const stats = pos.stats || {};
  const items = pos.items || [];
  const selected = state.posId || (items[0] && items[0].id) || "";
  const cur = items.find(x => x.id === selected) || null;
  const tab = state.posTab || "jobs";
  const faqItems = faq.items || [];
  let posFaq = { items: [], path: "" };
  if (cur && tab === "jobs") {
    try { posFaq = await api(`/api/positions/${encodeURIComponent(cur.id)}/faq`); }
    catch { posFaq = { items: [], path: cur.faq_path || "" }; }
  }
  const statusLabel = { open: "开放中", pending: "待开放", closed: "已关闭" };

  $("#main").innerHTML = `
    <div class="ov">
      <header class="ov-hero">
        <div class="ov-hero-text">
          <h1>职位与知识库</h1>
         
          
          <div class="ov-meta">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
            职位 ${esc(stats.total || 0)}　|　公共 FAQ ${esc(faqItems.length)} 条
            ${pos.synced_at ? `　|　同步 ${esc(pos.synced_at)}` : "　|　尚未同步"}
          </div>
        </div>
        <div class="ov-hero-actions">
          ${tab === "jobs"
            ? `<button type="button" class="ov-btn ov-btn-primary" id="pos-sync">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a9 9 0 11-2.6-6.2M21 4v6h-6"/></svg>
                同步职位
              </button>`
            : `<button type="button" class="ov-btn ov-btn-primary" id="common-faq-save">保存变更</button>`}
        </div>
        <div class="ov-hero-art" aria-hidden="true">
          <svg viewBox="0 0 200 160" fill="none">
            <ellipse cx="100" cy="142" rx="54" ry="12" fill="#3b82f6" opacity=".12"/>
            <rect x="48" y="40" width="104" height="72" rx="12" fill="#fff" stroke="#60a5fa" stroke-width="2.5"/>
            <rect x="62" y="56" width="48" height="8" rx="4" fill="#93c5fd"/>
            <rect x="62" y="72" width="72" height="8" rx="4" fill="#bfdbfe"/>
            <rect x="62" y="88" width="36" height="8" rx="4" fill="#dbeafe"/>
          </svg>
        </div>
      </header>

      <div class="ov-tabs">
        <button type="button" class="ov-tab ${tab === "jobs" ? "active" : ""}" data-ptab="jobs">职位管理</button>
        <button type="button" class="ov-tab ${tab === "faq" ? "active" : ""}" data-ptab="faq">公共 FAQ · ${faqItems.length}</button>
      </div>

      ${tab === "jobs" ? `
      <div class="ov-grid3">
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-blue">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">全部职位</div>
              <div class="ov-mod-stat">${stats.total || 0}</div>
            </div>
          </div>
          <p class="ov-hint">${pos.synced_at ? `上次同步 ${esc(pos.synced_at)}` : "尚未同步职位"}</p>
        </article>
        <article class="ov-card ov-mod">
          <span class="ov-dot ok"></span>
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-green">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><path d="M22 4L12 14.01l-3-3"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">开放中</div>
              <div class="ov-mod-stat ok">${stats.open || 0}</div>
            </div>
          </div>
          <p class="ov-hint">待开放 ${stats.pending || 0} · 已关闭 ${stats.closed || 0}</p>
        </article>
        <article class="ov-card ov-mod">
          <div class="ov-mod-head">
            <span class="ov-ico ov-ico-violet">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>
            </span>
            <div>
              <div class="ov-mod-label">公共 FAQ</div>
              <div class="ov-mod-stat">${faqItems.length} <span class="ov-sub">条</span></div>
            </div>
          </div>
          <p class="ov-tags"><a class="link" data-ptab="faq">去编辑公共口径 →</a></p>
        </article>
      </div>

      <div class="ov-grid2 ov-pos-layout">
        <section class="ov-card">
          <div class="ov-sec">
            <span class="ov-ico ov-ico-blue">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h10M4 18h14"/></svg>
            </span>
            <div>
              <h2 class="ov-sec-title">职位列表</h2>
              <p class="ov-desc">点击查看详情与专属 FAQ</p>
            </div>
          </div>
          <div class="pos-list">
            ${items.map(it => `
              <button type="button" class="pos-item ${it.id === selected ? "active" : ""}" data-pos="${esc(it.id)}">
                <strong>${esc(it.title)}</strong>
                <span class="tag">${esc(statusLabel[it.status] || it.status || "")}</span>
                ${it.enabled ? '<span class="tag ok">启用</span>' : '<span class="tag">未启用</span>'}
              </button>`).join("") || '<p class="ov-hint">暂无职位，请先同步</p>'}
          </div>
        </section>
        <section class="ov-card">
          <div class="ov-sec">
            <span class="ov-ico ov-ico-green">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
            </span>
            <div>
              <h2 class="ov-sec-title">${cur ? esc(cur.title) : "职位详情"}</h2>
              <p class="ov-desc">${cur ? `ID · ${esc(cur.id)}` : "请选择左侧职位"}</p>
            </div>
          </div>
          ${cur ? `
            <div class="ov-form-grid">
              <div>
                <label>别名（逗号分隔，用于沟通职位匹配）</label>
                <input id="pos-aliases" value="${esc((cur.aliases || []).join(", "))}" placeholder="例如：客服, 在线客服" />
              </div>
            </div>
            <div class="ov-checks">
              <label class="check"><input type="checkbox" id="pos-enabled" ${cur.enabled ? "checked" : ""} /> 参与自动回复</label>
            </div>
            <div class="ov-actions">
              <button type="button" class="ov-btn ov-btn-primary" id="pos-save">保存职位设置</button>
              <button type="button" class="ov-btn ov-btn-ghost" id="pos-ai-faq" title="用总控下发的大模型生成本岗位专属 FAQ">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3l1.8 5.5L19 10l-5.2 1.5L12 17l-1.8-5.5L5 10l5.2-1.5L12 3z"/><path d="M19 14l.9 2.6L22 17l-2.1.6L19 20l-.9-2.4L16 17l2.1-.4L19 14z"/></svg>
                AI 推荐 FAQ
              </button>
            </div>
            <div id="pos-ai-box" class="hidden"></div>
            <p class="ov-hint" id="pos-ai-msg" style="margin-top:.45rem"></p>
            <div class="pos-faq-block">
              <h3 class="ov-subhead" style="margin-top:0">专属 FAQ</h3>
              <p class="ov-hint" style="margin-top:0">同标题覆盖公共口径 · ${esc(posFaq.path || cur.faq_path || "保存时自动建文件")}</p>
              <div class="pos-faq-list" id="pos-faq-items">
                ${(posFaq.items || []).map((it, i) => `
                  <div class="pos-faq-item" data-faq-i="${i}">
                    <div class="row gap" style="justify-content:space-between">
                      <span class="kb-idx">${i + 1}</span>
                      <button type="button" class="ov-btn ov-btn-ghost" data-faq-del>删除</button>
                    </div>
                    <input data-pq="${i}" value="${esc(it.question)}" placeholder="问题标题" />
                    <textarea data-pa="${i}" rows="2" placeholder="专属标准答案">${esc(it.answer)}</textarea>
                  </div>`).join("") || '<p class="ov-hint">暂无专属条目，差异口径在此补充即可</p>'}
              </div>
              <div class="ov-actions mt">
                <button type="button" class="ov-btn ov-btn-ghost" id="pos-faq-add">添加条目</button>
                <button type="button" class="ov-btn ov-btn-primary" id="pos-faq-save">保存专属 FAQ</button>
              </div>
            </div>
          ` : '<p class="ov-hint">请选择左侧职位</p>'}
        </section>
      </div>
      ` : `
      <div class="ov-alert ov-alert-ok">
        「发送节奏 → 回复风格」里若选口语改写，会按事实换说法再发出；选「照搬原文」则原样发出。导入须先预览确认，不会上传即生效。
      </div>
      <section class="ov-card">
        <div class="ov-sec">
          <span class="ov-ico ov-ico-violet">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/></svg>
          </span>
          <div>
            <h2 class="ov-sec-title">公共 FAQ</h2>
            <p class="ov-desc">公司通用口径，所有职位默认继承；职位专属同名条目优先。</p>
            <div class="kb-meta">
              <span class="tag ok">${faqItems.length} 条</span>
              <span class="kb-path">${esc(faq.path || "config/knowledge/faq.md")}</span>
            </div>
          </div>
        </div>
        <div class="kb-toolbar">
          <input class="kb-search" id="faq-filter" type="search" placeholder="搜索问题或答案…" value="${esc(state.faqFilter || "")}" />
          <button type="button" class="ov-btn ov-btn-ghost" id="common-faq-add">新增条目</button>
          <label class="ov-btn ov-btn-ghost ov-file-btn">
            导入文件<input id="faq-file" type="file" accept=".md,.txt,.csv,.xlsx,.docx" hidden />
          </label>
          <button type="button" class="ov-btn ov-btn-ghost" id="common-ai-faq" title="填写行业后，用总控大模型生成公共 FAQ">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3l1.8 5.5L19 10l-5.2 1.5L12 17l-1.8-5.5L5 10l5.2-1.5L12 3z"/><path d="M19 14l.9 2.6L22 17l-2.1.6L19 20l-.9-2.4L16 17l2.1-.4L19 14z"/></svg>
            AI 推荐
          </button>
          <span class="spacer"></span>
          <span class="ov-hint" id="faq-filter-hint" style="margin:0">保存时自动备份旧版</span>
        </div>
        <div id="common-ai-industry" class="hidden ov-card" style="margin:.75rem 0;padding:.85rem 1rem;background:#f8fbff;border-color:#bfdbfe">
          <div class="ov-form-grid two" style="align-items:end">
            <div>
              <label>所属行业</label>
              <input id="common-ai-industry-input" type="text" placeholder="例如：餐饮、教培、物流、互联网、零售" value="${esc(state.faqIndustry || "")}" />
            </div>
            <div class="ov-actions" style="margin:0">
              <button type="button" class="ov-btn ov-btn-primary" id="common-ai-run">开始生成</button>
              <button type="button" class="ov-btn ov-btn-ghost" id="common-ai-cancel">取消</button>
            </div>
          </div>
          <p class="ov-hint" style="margin-top:.45rem">将按该行业生成公司通用 FAQ；生成后可勾选采纳，不会立刻覆盖现有内容。</p>
        </div>
        <p class="ov-hint" id="common-ai-msg" style="margin:.35rem 0 0"></p>
        <div id="common-ai-box" class="hidden"></div>
        <div class="kb-list" id="common-faq-items">
          ${faqItems.length ? faqItems.map((it, i) => faqItemHtml(it, i, "data-cq", "data-ca")).join("") : `
            <div class="kb-empty" id="faq-blank">
              <strong>尚未配置公共 FAQ</strong>
              <p>先新增条目，或从已有文档导入口径。</p>
              <button type="button" class="ov-btn ov-btn-primary" id="common-faq-add-empty">新增第一条</button>
            </div>`}
          <div class="kb-empty hidden" id="faq-filter-empty">
            <strong>无匹配条目</strong>
            <p>试试清空搜索，或换个关键词。</p>
          </div>
        </div>
        <div class="kb-foot">
          <p class="ov-hint" style="margin:0">修改后请点击保存；空标题条目保存时自动忽略。</p>
          <div class="ov-actions">
            <button type="button" class="ov-btn ov-btn-ghost" id="common-faq-add-2">新增条目</button>
            <button type="button" class="ov-btn ov-btn-primary" id="common-faq-save-2">保存变更</button>
          </div>
        </div>
      </section>
      <div id="faq-preview" class="hidden"></div>
      `}
    </div>`;

  $$("[data-ptab]").forEach(b => b.onclick = () => {
    state.posTab = b.dataset.ptab;
    renderPositions();
  });

  if (tab === "jobs") {
    $("#pos-sync").onclick = async () => {
      try {
        const r = await api("/api/positions/sync", { method: "POST", body: "{}" });
        alert(`已同步 ${r.count} 个职位`);
        state.posId = "";
        renderPositions();
      } catch (e) { alert(e.message || e); }
    };
    $$("[data-pos]").forEach(b => b.onclick = () => {
      state.posId = b.dataset.pos;
      renderPositions();
    });
    if (cur) {
      $("#pos-save").onclick = async () => {
        const aliases = ($("#pos-aliases").value || "").split(/[,，]/).map(s => s.trim()).filter(Boolean);
        await api(`/api/positions/${encodeURIComponent(cur.id)}`, {
          method: "PUT",
          body: JSON.stringify({ aliases, enabled: $("#pos-enabled").checked }),
        });
        alert("职位已保存");
        renderPositions();
      };
      $("#pos-ai-faq").onclick = async () => {
        const btn = $("#pos-ai-faq");
        const msg = $("#pos-ai-msg");
        const box = $("#pos-ai-box");
        if (btn) btn.disabled = true;
        if (msg) msg.textContent = "正在调用总控大模型生成推荐…";
        if (box) { box.classList.add("hidden"); box.innerHTML = ""; }
        try {
          const r = await api(`/api/positions/${encodeURIComponent(cur.id)}/faq/ai-suggest`, {
            method: "POST",
            body: "{}",
          });
          if (msg) msg.textContent = `已生成 ${(r.items || []).length} 条推荐，请勾选采纳。`;
          showPosAiFaqPreview(cur.id, r.items || [], r.model_tip || "");
        } catch (e) {
          if (msg) msg.textContent = "";
          alert(e.message || e);
        } finally {
          if (btn) btn.disabled = false;
        }
      };
      $("#pos-faq-add").onclick = () => {
        const box = $("#pos-faq-items");
        const empty = box.querySelector(".ov-hint, .muted");
        if (empty) empty.remove();
        const i = box.querySelectorAll("[data-pq]").length;
        box.insertAdjacentHTML("beforeend", `
          <div class="pos-faq-item" data-faq-i="${i}">
            <div class="row gap" style="justify-content:space-between">
              <span class="kb-idx">${i + 1}</span>
              <button type="button" class="ov-btn ov-btn-ghost" data-faq-del>删除</button>
            </div>
            <input data-pq="${i}" placeholder="问题标题" />
            <textarea data-pa="${i}" rows="2" placeholder="专属标准答案"></textarea>
          </div>`);
        box.querySelector(`[data-pq="${i}"]`)?.focus();
      };
      $("#pos-faq-items").onclick = (e) => {
        const del = e.target.closest("[data-faq-del]");
        if (!del) return;
        del.closest(".pos-faq-item")?.remove();
        renumberFaqList($("#pos-faq-items"));
      };
      $("#pos-faq-save").onclick = async () => {
        await api(`/api/positions/${encodeURIComponent(cur.id)}/faq`, {
          method: "PUT",
          body: JSON.stringify({ items: collectFaqByAttrs("data-pq", "data-pa") }),
        });
        alert("专属 FAQ 已保存");
        renderPositions();
      };
    }
    return;
  }

  const saveCommon = async () => {
    const next = collectFaqByAttrs("data-cq", "data-ca");
    await api("/api/faq", { method: "PUT", body: JSON.stringify({ items: next }) });
    alert("公共 FAQ 已保存（已自动备份旧版）");
    state.posTab = "faq";
    renderPositions();
  };

  const addCommonItem = () => {
    const box = $("#common-faq-items");
    if (!box) return;
    $("#faq-blank")?.remove();
    state.faqFilter = "";
    const filterEl = $("#faq-filter");
    if (filterEl) filterEl.value = "";
    applyFaqFilter("");
    const i = box.querySelectorAll("[data-cq]").length;
    box.insertAdjacentHTML("beforeend", faqItemHtml({ question: "", answer: "" }, i, "data-cq", "data-ca"));
    renumberFaqList(box);
    box.querySelector(`[data-cq="${i}"]`)?.focus();
  };

  ["#common-faq-add", "#common-faq-add-2", "#common-faq-add-empty"].forEach((sel) => {
    const btn = $(sel);
    if (btn) btn.onclick = addCommonItem;
  });

  $("#common-faq-items")?.addEventListener("click", (e) => {
    const del = e.target.closest("[data-faq-del]");
    if (!del) return;
    del.closest(".kb-item")?.remove();
    renumberFaqList($("#common-faq-items"));
    applyFaqFilter($("#faq-filter")?.value || "");
  });

  $("#common-faq-save").onclick = saveCommon;
  $("#common-faq-save-2").onclick = saveCommon;

  const filterEl = $("#faq-filter");
  if (filterEl) {
    filterEl.oninput = () => {
      state.faqFilter = filterEl.value;
      applyFaqFilter(filterEl.value);
    };
    if (state.faqFilter) applyFaqFilter(state.faqFilter);
  }

  $("#faq-file").onchange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/faq/preview-upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) return alert(data.detail || "解析失败");
    showCommonFaqPreview(data.items || []);
    e.target.value = "";
  };
  const industryPanel = $("#common-ai-industry");
  const aiBox = $("#common-ai-box");
  const aiMsg = $("#common-ai-msg");
  const aiFaqBtn = $("#common-ai-faq");
  if (aiFaqBtn) aiFaqBtn.onclick = () => {
    if (aiBox) { aiBox.classList.add("hidden"); aiBox.innerHTML = ""; }
    if (aiMsg) aiMsg.textContent = "";
    industryPanel?.classList.remove("hidden");
    $("#common-ai-industry-input")?.focus();
  };
  const aiCancel = $("#common-ai-cancel");
  if (aiCancel) aiCancel.onclick = () => {
    industryPanel?.classList.add("hidden");
  };
  const aiRun = $("#common-ai-run");
  if (aiRun) aiRun.onclick = async () => {
    const industry = ($("#common-ai-industry-input")?.value || "").trim();
    if (!industry) return alert("请先填写行业，例如：餐饮、教培、物流");
    state.faqIndustry = industry;
    const runBtn = $("#common-ai-run");
    if (runBtn) runBtn.disabled = true;
    if (aiMsg) aiMsg.textContent = `正在按「${industry}」调用总控大模型生成公共 FAQ…`;
    if (aiBox) { aiBox.classList.add("hidden"); aiBox.innerHTML = ""; }
    try {
      const r = await api("/api/faq/ai-suggest", {
        method: "POST",
        body: JSON.stringify({ industry, count: 10 }),
      });
      industryPanel?.classList.add("hidden");
      if (aiMsg) aiMsg.textContent = `已生成 ${(r.items || []).length} 条推荐，请勾选采纳。`;
      showCommonAiFaqPreview(r.items || [], r.model_tip || "", r.industry || industry);
    } catch (e) {
      if (aiMsg) aiMsg.textContent = "";
      alert(e.message || e);
    } finally {
      if (runBtn) runBtn.disabled = false;
    }
  };
}

async function renderFaq() {
  state.posTab = "faq";
  return renderPositions();
}
