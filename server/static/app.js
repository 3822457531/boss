/* 卡密总控台前端 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = {
  page: "models",
  me: null,
  modelsTab: "llm",
  presets: null,
  lastTests: { llm: null, jev: null },
  batchPoll: null,
  simPersonas: null,
  simAutoPersona: "reask_hiring",
  simAutoTurns: 5,
  simAutoOpener: "",
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    credentials: "same-origin",
    ...opts,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data.detail || data.error || text || res.statusText);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

async function boot() {
  try {
    state.me = await api("/api/admin/me");
  } catch {
    state.me = { ok: false };
  }
  if (!state.me.ok) renderLogin();
  else renderShell();
}

function authHeroHtml() {
  return `
    <section class="auth-hero">
      <div class="auth-brand">
        <div class="auth-logo" aria-hidden="true">
          <svg viewBox="0 0 48 48" fill="none"><circle cx="24" cy="20" r="10" stroke="#fff" stroke-width="2.4"/><path d="M14 34c2.8-5 7-7.5 10-7.5S31.2 29 34 34" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><circle cx="20" cy="19" r="1.6" fill="#fff"/><circle cx="28" cy="19" r="1.6" fill="#fff"/></svg>
        </div>
        <div class="auth-brand-text">
          <strong>直聊小蜜</strong>
          <span>智能招聘自动化 · 总控台</span>
        </div>
      </div>
      <div class="auth-hero-body">
        <h2>让招聘更简单<br><em>用直聊小蜜</em></h2>
        <p>统一管理卡密、模型密钥与客户端发版，为每台客户机安全下发配置。</p>
        <div class="auth-feats">
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg></i><b>自动筛选</b><span>高效匹配人才</span></div>
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a8 8 0 01-11.5 7.2L4 20l.9-4.2A8 8 0 1121 12z"/></svg></i><b>智能沟通</b><span>自动发起对话</span></div>
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg></i><b>面试安排</b><span>一站式管理</span></div>
          <div class="auth-feat"><i><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19V5M4 19h16M8 15v4M12 10v9M16 7v12"/></svg></i><b>数据洞察</b><span>效果可视化</span></div>
        </div>
      </div>
      <div class="auth-art" aria-hidden="true">
        <div class="auth-float af1">卡密下发 · <strong>AES</strong><br><span class="muted">模型密钥加密同步</span></div>
        <div class="auth-float af2">在线设备 <strong>—</strong><br><span class="muted">心跳监控</span></div>
        <div class="auth-float af3">发版通道已就绪</div>
        <div class="auth-art-bot">
          <svg viewBox="0 0 160 160" fill="none">
            <ellipse cx="80" cy="138" rx="46" ry="10" fill="#12b7a7" opacity=".18"/>
            <rect x="48" y="78" width="64" height="52" rx="18" fill="#fff" stroke="#12b7a7" stroke-width="3"/>
            <circle cx="80" cy="52" r="28" fill="#fff" stroke="#12b7a7" stroke-width="3"/>
            <circle cx="70" cy="50" r="3.5" fill="#12b7a7"/><circle cx="90" cy="50" r="3.5" fill="#12b7a7"/>
            <path d="M70 62c3 3 7 3 10 0" stroke="#12b7a7" stroke-width="2.5" stroke-linecap="round"/>
            <rect x="58" y="96" width="44" height="28" rx="6" fill="#e7f8f6" stroke="#12b7a7" stroke-width="2"/>
          </svg>
        </div>
      </div>
    </section>`;
}

function renderLogin() {
  document.body.className = "login-only";
  const app = $("#app");
  if (app) app.className = "login-mode";
  $("#app").innerHTML = `
    <div class="auth-split">
      ${authHeroHtml()}
      <section class="auth-panel">
        <div class="auth-card">
          <h1>欢迎登录 <em>直聊小蜜</em></h1>
          <p class="auth-sub">总控台 · 开启智能招聘管理</p>
          <div class="auth-field">
            <span class="ico" aria-hidden="true"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V8a5 5 0 0110 0v3"/></svg></span>
            <input id="pwd" type="password" placeholder="请输入管理员密码" autocomplete="current-password" autofocus />
          </div>
          <button class="auth-btn" id="login" type="button">登录</button>
          <div id="login-err" class="mt"></div>
          <p class="auth-meta">仅授权管理员可登录</p>
        </div>
      </section>
    </div>`;
  const submit = async () => {
    const btn = $("#login");
    btn.disabled = true;
    try {
      await api("/api/admin/login", { method: "POST", body: JSON.stringify({ password: $("#pwd").value }) });
      boot();
    } catch (e) {
      btn.disabled = false;
      $("#login-err").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    }
  };
  $("#login").onclick = submit;
  $("#pwd").onkeydown = (e) => { if (e.key === "Enter") submit(); };
}

function renderShell() {
  document.body.className = "";
  const app = $("#app");
  if (app) app.className = "";
  $("#app").innerHTML = `
    <aside class="nav">
      <div class="brand">${esc(state.me.site_name || "直聊小蜜总控")}</div>
      <p class="brand-sub">服务器部署 · 直聊小蜜</p>
      <div class="nav-group">配置</div>
      <button data-page="models" class="active">模型与判断</button>
      <button data-page="licenses">卡密生成</button>
      <button data-page="users">激活用户</button>
      <button data-page="releases">客户端发版</button>
      <div class="nav-group">运维</div>
      <button data-page="sim">模拟聊天</button>
      <button data-page="batch">批量测试</button>
      <button data-page="logs">运行日志</button>
      <button data-page="settings">其他设置</button>
      <button class="ghost mt" id="logout" style="width:100%">退出登录</button>
    </aside>
    <main id="main"><p class="muted">加载中…</p></main>`;
  $$("[data-page]").forEach(b => b.onclick = () => {
    state.page = b.dataset.page;
    $$("[data-page]").forEach(x => x.classList.toggle("active", x === b));
    go();
  });
  $("#logout").onclick = async () => {
    await api("/api/admin/logout", { method: "POST", body: "{}" });
    boot();
  };
  go();
}

async function go() {
  const map = {
    models: pageModels,
    licenses: pageLicenses,
    users: pageUsers,
    releases: pageReleases,
    sim: pageSim,
    batch: pageBatch,
    logs: pageLogs,
    settings: pageSettings,
  };
  if (state.page !== "batch") stopBatchPoll();
  const main = $("#main");
  try {
    await (map[state.page] || pageModels)();
  } catch (e) {
    if (main) {
      main.innerHTML = `<div class="error-box">加载失败：${esc(e.message || e)}</div>
        <p class="hint mt">请确认 MySQL 已启动，且 .env.server 中 LICENSE_MYSQL_DSN 正确；模拟/批量还需本机可访问大模型与 Jev。</p>`;
    }
  }
}

function maskKeyBlock(configured, mask, inputId) {
  return `
    <label>API Key</label>
    <div class="row gap">
      <input id="${inputId}" type="password" placeholder="${configured ? "已保存 " + esc(mask || "") + "（输入新值以替换）" : "粘贴密钥"}" autocomplete="off" />
      ${configured ? `<button type="button" class="ghost" data-clear-key="${inputId}">清除密钥</button>` : ""}
    </div>
    <p class="hint">已保存的密钥不会回显完整值；仅显示末四位掩码。客户机激活后加密下发，不落客户机 .env。</p>`;
}

function testBox(last) {
  if (!last) return `<p class="muted">尚未测试</p>`;
  if (last.ok) {
    return `<div class="ok-box">成功 · ${esc(last.human || "")}
      ${last.raw ? `<details><summary>原始片段</summary><pre class="raw">${esc(last.raw)}</pre></details>` : ""}
    </div>`;
  }
  return `<div class="error-box">${esc(last.human || "失败")}
    <details open><summary>原始错误</summary><pre class="raw">${esc(last.error || "")}</pre></details>
  </div>`;
}

async function pageModels() {
  if (!state.presets) state.presets = await api("/api/admin/presets");
  const d = await api("/api/admin/models");
  const llm = d.llm || {};
  const jev = d.jev || {};
  const tab = state.modelsTab || "llm";
  const llmOpts = state.presets.llm.map(p =>
    `<option value="${esc(p.id)}" ${p.id === (llm.provider || "") ? "selected" : ""}>${esc(p.name)}</option>`
  ).join("");
  const jevOpts = state.presets.jev.map(p =>
    `<option value="${esc(p.id)}" ${p.id === (jev.provider || "") ? "selected" : ""}>${esc(p.name)}</option>`
  ).join("");
  $("#main").innerHTML = `
    <h1>模型与判断</h1>
    <p class="lead">生成回复用大模型；意图/动作/是否回复用 Jev。配置保存在总控，客户机激活后加密下发。</p>
    <div class="tabs">
      <button type="button" class="tab ${tab === "llm" ? "active" : ""}" data-mtab="llm">生成（大模型）</button>
      <button type="button" class="tab ${tab === "jev" ? "active" : ""}" data-mtab="jev">判断（Jev）</button>
    </div>
    <div class="card ${tab === "llm" ? "" : "hidden"}" id="panel-llm">
      <h2 style="margin-top:0">大模型 · 生成回复文本</h2>
      <div class="grid two">
        <div><label>厂商</label><select id="llm-provider">${llmOpts}</select></div>
        <div><label>是否启用</label>
          <select id="llm-enabled"><option value="true" ${llm.enabled ? "selected" : ""}>启用</option>
          <option value="false" ${!llm.enabled ? "selected" : ""}>关闭</option></select></div>
        <div><label>API 地址</label><input id="llm-base" value="${esc(llm.base_url || "")}" /></div>
        <div><label>模型名</label><input id="llm-model" value="${esc(llm.model || "")}" /></div>
        <div><label>超时（秒）</label><input id="llm-timeout" type="number" value="${esc(llm.timeout ?? 30)}" /></div>
        <div><label>最大输出长度</label><input id="llm-max" type="number" min="64" value="${esc(llm.max_tokens ?? 1600)}" />
          <p class="hint">deepseek-flash 等推理模型建议 ≥1600。</p></div>
        <div><label>采样温度 0~1</label><input id="llm-temp" type="number" step="0.1" min="0" max="1" value="${esc(llm.temperature ?? 0.3)}" /></div>
        <div>${maskKeyBlock(!!llm.api_key_configured, llm.api_key_mask || "", "llm-key")}</div>
      </div>
      <div class="row gap mt">
        <button id="llm-save">保存</button>
        <button class="ghost" id="llm-test">测试连接</button>
      </div>
      <div class="mt" id="llm-test-result">${testBox(state.lastTests.llm)}</div>
    </div>
    <div class="card ${tab === "jev" ? "" : "hidden"}" id="panel-jev">
      <h2 style="margin-top:0">Jev · 只做判断不做生成</h2>
      <p class="hint">勿填大模型 chat/completions 地址。正确接口返回 answers.*.noul。</p>
      ${!jev.enabled ? `<div class="warn-box">Jev 关闭时客户机降级为关键词规则，判断质量会显著下降。</div>` : ""}
      <div class="grid two">
        <div><label>厂商</label><select id="jev-provider">${jevOpts}</select></div>
        <div><label>是否启用</label>
          <select id="jev-enabled"><option value="true" ${jev.enabled !== false ? "selected" : ""}>启用</option>
          <option value="false" ${jev.enabled === false ? "selected" : ""}>关闭</option></select></div>
        <div><label>API 地址</label><input id="jev-base" value="${esc(jev.base_url || "")}" /></div>
        <div><label>模型名</label><input id="jev-model" value="${esc(jev.model || "")}" /></div>
        <div>${maskKeyBlock(!!jev.api_key_configured, jev.api_key_mask || "", "jev-key")}</div>
      </div>
      <div class="row gap mt">
        <button id="jev-save">保存</button>
        <button class="ghost" id="jev-test">测试连接</button>
      </div>
      <div class="mt" id="jev-test-result">${testBox(state.lastTests.jev)}</div>
    </div>`;

  $$("[data-mtab]").forEach(b => b.onclick = () => {
    state.modelsTab = b.dataset.mtab;
    pageModels();
  });
  $("#llm-provider").onchange = () => {
    const p = state.presets.llm.find(x => x.id === $("#llm-provider").value);
    if (p) { $("#llm-base").value = p.base_url; $("#llm-model").value = p.model; }
  };
  $("#jev-provider").onchange = () => {
    const p = state.presets.jev.find(x => x.id === $("#jev-provider").value);
    if (p) { $("#jev-base").value = p.base_url; $("#jev-model").value = p.model; }
  };
  $("#llm-save").onclick = async () => {
    await api("/api/admin/models", {
      method: "PUT",
      body: JSON.stringify({
        llm: {
          enabled: $("#llm-enabled").value === "true",
          provider: $("#llm-provider").value,
          base_url: $("#llm-base").value.trim(),
          model: $("#llm-model").value.trim(),
          timeout: +$("#llm-timeout").value,
          max_tokens: +$("#llm-max").value,
          temperature: +$("#llm-temp").value,
          api_key: $("#llm-key").value.trim() || undefined,
        },
      }),
    });
    alert("大模型已保存");
    state.modelsTab = "llm";
    pageModels();
  };
  $("#jev-save").onclick = async () => {
    await api("/api/admin/models", {
      method: "PUT",
      body: JSON.stringify({
        jev: {
          enabled: $("#jev-enabled").value === "true",
          provider: $("#jev-provider").value,
          base_url: $("#jev-base").value.trim(),
          model: $("#jev-model").value.trim(),
          api_key: $("#jev-key").value.trim() || undefined,
        },
      }),
    });
    alert("Jev 已保存");
    state.modelsTab = "jev";
    pageModels();
  };
  const clearLlm = document.querySelector(`[data-clear-key="llm-key"]`);
  if (clearLlm) {
    clearLlm.onclick = async () => {
      if (!confirm("确认清除大模型密钥？")) return;
      await api("/api/admin/models/llm/key", { method: "DELETE" });
      state.modelsTab = "llm";
      pageModels();
    };
  }
  const clearJev = document.querySelector(`[data-clear-key="jev-key"]`);
  if (clearJev) {
    clearJev.onclick = async () => {
      if (!confirm("确认清除 Jev 密钥？")) return;
      await api("/api/admin/models/jev/key", { method: "DELETE" });
      state.modelsTab = "jev";
      pageModels();
    };
  }
  $("#llm-test").onclick = async () => {
    $("#llm-test-result").innerHTML = `<p class="muted">测试中…</p>`;
    try {
      state.lastTests.llm = await api("/api/admin/models/test-llm", { method: "POST", body: "{}" });
    } catch (e) {
      state.lastTests.llm = { ok: false, human: "请求失败", error: String(e.message || e) };
    }
    $("#llm-test-result").innerHTML = testBox(state.lastTests.llm);
  };
  $("#jev-test").onclick = async () => {
    $("#jev-test-result").innerHTML = `<p class="muted">测试中…</p>`;
    try {
      state.lastTests.jev = await api("/api/admin/models/test-jev", { method: "POST", body: "{}" });
    } catch (e) {
      state.lastTests.jev = { ok: false, human: "请求失败", error: String(e.message || e) };
    }
    $("#jev-test-result").innerHTML = testBox(state.lastTests.jev);
  };
}

async function pageLicenses() {
  const d = await api("/api/admin/licenses");
  $("#main").innerHTML = `
    <h1>卡密生成</h1>
    <p class="lead">生成后发给客户；客户机输入卡密激活才能使用。</p>
    <div class="card">
      <div class="grid two">
        <div><label>生成数量</label><input id="lc-count" type="number" min="1" max="200" value="5" /></div>
        <div><label>每卡最多设备数</label><input id="lc-dev" type="number" min="1" max="20" value="1" /></div>
        <div><label>有效天数（首次激活起算）</label><input id="lc-days" type="number" min="1" value="365" /></div>
        <div><label>备注</label><input id="lc-note" placeholder="例如：华东代理批次" /></div>
      </div>
      <button class="mt" id="lc-gen">生成卡密</button>
      <div id="lc-new" class="mt"></div>
    </div>
    <div class="card">
      <h2>全部卡密</h2>
      <table><thead><tr><th>卡密</th><th>状态</th><th>设备</th><th>有效期</th><th>备注</th><th></th></tr></thead>
      <tbody>
        ${(d.items || []).map(it => `
          <tr>
            <td class="mono">${esc(it.license_key)}</td>
            <td><span class="tag ${it.status === "active" ? "ok" : it.status === "revoked" ? "danger" : ""}">${esc(it.status)}</span></td>
            <td>${esc(it.max_devices)}</td>
            <td>${esc(it.expires_at || (it.valid_days + " 天"))}</td>
            <td>${esc(it.note)}</td>
            <td>${it.status !== "revoked" ? `<button class="ghost" data-rev="${it.id}">吊销</button>` : ""}</td>
          </tr>`).join("") || '<tr><td colspan="6" class="muted">暂无</td></tr>'}
      </tbody></table>
    </div>`;
  $("#lc-gen").onclick = async () => {
    const r = await api("/api/admin/licenses", {
      method: "POST",
      body: JSON.stringify({
        count: +$("#lc-count").value,
        max_devices: +$("#lc-dev").value,
        valid_days: +$("#lc-days").value,
        note: $("#lc-note").value.trim(),
      }),
    });
    $("#lc-new").innerHTML = `<div class="ok-box">已生成 ${r.count} 张：<br><pre class="mono" style="white-space:pre-wrap;margin:.5rem 0 0">${esc((r.items || []).map(x => x.license_key).join("\n"))}</pre></div>`;
    pageLicenses();
  };
  $$("[data-rev]").forEach(b => b.onclick = async () => {
    if (!confirm("确认吊销？已激活设备将立即失效。")) return;
    await api(`/api/admin/licenses/${b.dataset.rev}/revoke`, { method: "POST", body: "{}" });
    pageLicenses();
  });
}

async function pageUsers() {
  const d = await api("/api/admin/activations");
  $("#main").innerHTML = `
    <h1>激活用户</h1>
    <p class="lead">已绑定设备；可解绑 / 吊销会话。</p>
    <div class="card">
      <table><thead><tr><th>机器</th><th>卡密</th><th>设备 ID</th><th>IP</th><th>心跳</th><th>状态</th><th></th></tr></thead>
      <tbody>
        ${(d.items || []).map(it => `
          <tr>
            <td>${esc(it.machine_name || "—")}</td>
            <td class="mono">${esc(it.license_key_mask)}</td>
            <td class="mono">${esc((it.device_id || "").slice(0, 12))}…</td>
            <td>${esc(it.ip)}</td>
            <td>${esc(it.last_heartbeat)}</td>
            <td><span class="tag ${it.status === "active" ? "ok" : "danger"}">${esc(it.status)}</span></td>
            <td>${it.status === "active" ? `<button class="ghost" data-arev="${it.id}">解绑</button>` : ""}</td>
          </tr>`).join("") || '<tr><td colspan="7" class="muted">暂无激活</td></tr>'}
      </tbody></table>
    </div>`;
  $$("[data-arev]").forEach(b => b.onclick = async () => {
    if (!confirm("解绑该设备？")) return;
    await api(`/api/admin/activations/${b.dataset.arev}/revoke`, { method: "POST", body: "{}" });
    pageUsers();
  });
}

async function pageReleases() {
  const d = await api("/api/admin/releases");
  $("#main").innerHTML = `
    <h1>客户端发版</h1>
    <p class="lead">上传直聊小蜜安装包（ZhiLiaoXiaoMi-Setup-*.exe）；客户机启动时自动检查更新。</p>
    <div class="card">
      <div class="grid two">
        <div><label>版本号</label><input id="rel-ver" placeholder="1.0.1" /></div>
        <div><label>安装包文件</label><input id="rel-file" type="file" accept=".exe" /></div>
        <div><label>更新说明</label><textarea id="rel-notes" rows="3"></textarea></div>
        <div><label class="check mt"><input id="rel-force" type="checkbox" /> 强制更新</label>
          <p class="hint">勾选后旧版本必须升级才能进主界面</p></div>
      </div>
      <button class="mt" id="rel-upload">上传并设为最新</button>
      <div id="rel-msg" class="mt"></div>
    </div>
    <div class="card">
      <h2>历史版本 ${d.latest ? `· 当前最新 ${esc(d.latest.version)}` : ""}</h2>
      <table><thead><tr><th>版本</th><th>大小</th><th>强制</th><th>时间</th><th>说明</th></tr></thead>
      <tbody>
        ${(d.items || []).map(it => `
          <tr>
            <td>${esc(it.version)} ${it.is_latest ? '<span class="tag ok">最新</span>' : ""}</td>
            <td>${esc(Math.round((it.size_bytes || 0) / 1024 / 1024 * 10) / 10)} MB</td>
            <td>${it.force_update ? "是" : "否"}</td>
            <td>${esc(it.created_at)}</td>
            <td>${esc(it.notes)}</td>
          </tr>`).join("") || '<tr><td colspan="5" class="muted">暂无发版</td></tr>'}
      </tbody></table>
    </div>`;
  $("#rel-upload").onclick = async () => {
    const file = $("#rel-file").files?.[0];
    const version = $("#rel-ver").value.trim();
    if (!file || !version) return alert("请填写版本并选择文件");
    const fd = new FormData();
    fd.append("file", file);
    const q = new URLSearchParams({
      version,
      notes: $("#rel-notes").value,
      force_update: $("#rel-force").checked ? "true" : "false",
    });
    const btn = $("#rel-upload");
    const msg = $("#rel-msg");
    const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
    btn.disabled = true;
    msg.innerHTML = `
      <div class="progress mt" aria-hidden="true"><span id="rel-prog" style="width:0%"></span></div>
      <p class="hint mt" id="rel-prog-text">准备上传 ${esc(sizeMb)} MB…</p>`;
    try {
      const data = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `/api/admin/releases?${q}`);
        xhr.withCredentials = true;
        xhr.upload.onprogress = (ev) => {
          if (!ev.lengthComputable) return;
          const pct = Math.max(0, Math.min(100, Math.round((ev.loaded / ev.total) * 100)));
          const bar = $("#rel-prog");
          const text = $("#rel-prog-text");
          if (bar) bar.style.width = `${pct}%`;
          if (text) {
            text.textContent = `已上传 ${(ev.loaded / (1024 * 1024)).toFixed(1)} / ${(ev.total / (1024 * 1024)).toFixed(1)} MB · ${pct}%`;
          }
        };
        xhr.onload = () => {
          let body = {};
          try { body = JSON.parse(xhr.responseText || "{}"); } catch { body = { detail: xhr.responseText }; }
          if (xhr.status >= 200 && xhr.status < 300) resolve(body);
          else reject(new Error(body.detail || body.error || `上传失败 HTTP ${xhr.status}`));
        };
        xhr.onerror = () => reject(new Error("网络错误，上传中断"));
        xhr.onabort = () => reject(new Error("上传已取消"));
        xhr.send(fd);
      });
      msg.innerHTML = `<div class="ok-box">已发布 ${esc(data.version)}</div>`;
      pageReleases();
    } catch (e) {
      msg.innerHTML = `<div class="error-box">${esc(e.message || e)}</div>`;
    } finally {
      btn.disabled = false;
    }
  };
}

async function pageLogs() {
  const d = await api("/api/admin/audit?limit=150");
  $("#main").innerHTML = `
    <h1>运行日志</h1>
    <p class="lead">激活、心跳失败、管理员操作记录。</p>
    <div class="card">
      <table><thead><tr><th>时间</th><th>角色</th><th>动作</th><th>详情</th></tr></thead>
      <tbody>
        ${(d.items || []).map(it => `
          <tr>
            <td>${esc(it.ts)}</td>
            <td>${esc(it.actor)}</td>
            <td>${esc(it.action)}</td>
            <td class="mono">${esc(JSON.stringify(it.detail || {}))}</td>
          </tr>`).join("") || '<tr><td colspan="4" class="muted">暂无</td></tr>'}
      </tbody></table>
    </div>`;
}

async function pageSettings() {
  const s = await api("/api/admin/settings");
  $("#main").innerHTML = `
    <h1>其他设置</h1>
    <div class="card">
      <div class="grid two">
        <div><label>站点名称</label><input id="st-name" value="${esc(s.site_name || "")}" /></div>
        <div><label>心跳间隔（秒）</label><input id="st-hb" type="number" min="60" value="${esc(s.heartbeat_sec || 600)}" /></div>
        <div><label>公告（下发给客户机）</label><textarea id="st-ann">${esc(s.announcement || "")}</textarea></div>
        <div>
          <label class="check"><input id="st-force" type="checkbox" ${s.force_update ? "checked" : ""} /> 全局强制更新</label>
          <label class="mt">新管理员密码（留空不改）</label>
          <input id="st-pwd" type="password" />
        </div>
      </div>
      <button class="mt" id="st-save">保存</button>
    </div>`;
  $("#st-save").onclick = async () => {
    await api("/api/admin/settings", {
      method: "PUT",
      body: JSON.stringify({
        site_name: $("#st-name").value.trim(),
        heartbeat_sec: +$("#st-hb").value,
        announcement: $("#st-ann").value,
        force_update: $("#st-force").checked,
        new_password: $("#st-pwd").value || null,
      }),
    });
    alert("已保存");
    state.me.site_name = $("#st-name").value.trim();
    renderShell();
    state.page = "settings";
    $$("[data-page]").forEach(x => x.classList.toggle("active", x.dataset.page === "settings"));
    pageSettings();
  };
}

/* ---- 模拟聊天 / 批量测试（总控专用） ---- */

const SIM_CAND_ACTIONS = [
  { label: "打招呼", text: "你好，还在招人吗" },
  { label: "自我介绍", text: "本人女，有相关经验，方便的话想了解一下岗位详情" },
  { label: "发简历卡", text: "【模拟】对方想给您发送一份附件简历，您是否同意接收？" },
  { label: "换微信卡", text: "【模拟】我想要和您交换微信，您是否同意？" },
  { label: "不感兴趣", text: "不太感兴趣，先看看别的机会吧" },
];

const SIM_ASK_PRESETS = [
  { label: "还招吗", text: "请问这个岗位还在招吗" },
  { label: "问地点", text: "工作地点在哪里呀" },
  { label: "问薪资", text: "薪资和上班休息时间分别怎么算呢" },
  { label: "问工作内容", text: "工作内容有哪些" },
  { label: "约面试", text: "这周可以安排面试吗，我时间都比较空" },
];

const SIM_HR_INJECT = [
  { label: "标准收简历确认", text: "收到你的简历了，我先看看匹配度，稍后反馈～" },
  { label: "口语确认(易漏检)", text: "好的，这边先看一下你的简历ha" },
];

function stopBatchPoll() {
  if (state.batchPoll) {
    clearInterval(state.batchPoll);
    state.batchPoll = null;
  }
}

function batchOutcomeTag(row) {
  const oc = String(row.outcome || "");
  if (row.error || oc === "error") return `<span class="tag danger">错误</span>`;
  if (row.handoff || oc === "handoff") return `<span class="tag warn">转人工</span>`;
  if (oc === "skip") return `<span class="tag mute">跳过</span>`;
  if (oc === "wechat") return `<span class="tag ok">换微信</span>`;
  if (oc === "action") return `<span class="tag ok">平台动作</span>`;
  if (oc === "sent" || oc === "reply" || oc === "draft") return `<span class="tag ok">${esc(oc)}</span>`;
  return `<span class="tag">${esc(oc || "-")}</span>`;
}

function batchRowClass(row) {
  const oc = String(row.outcome || "");
  if (row.error || oc === "error") return "err";
  if (row.handoff || oc === "handoff") return "hand";
  if (!(row.answer || "").trim() && oc !== "skip") return "warn";
  return "";
}

function renderBatchQaList(results, filter) {
  let rows = results || [];
  if (filter === "empty") rows = rows.filter(r => !(r.answer || "").trim());
  else if (filter === "handoff") rows = rows.filter(r => r.handoff || r.outcome === "handoff");
  else if (filter === "error") rows = rows.filter(r => r.error || r.outcome === "error");
  else if (filter === "ok") {
    rows = rows.filter(r => (r.answer || "").trim() && !r.handoff && r.outcome !== "error" && !r.error);
  }
  if (!rows.length) {
    return `<p class="hint">暂无结果${filter && filter !== "all" ? "（当前筛选为空）" : "，导入话术后点「开始测试」"}。</p>`;
  }
  return rows.map(r => {
    const ans = (r.answer || "").trim();
    const notes = (r.notes || []).filter(Boolean).slice(0, 2);
    return `<div class="batch-qa ${batchRowClass(r)}">
      <div class="bq-head">
        <span class="bq-idx">#${esc(r.index ?? "")}</span>
        ${batchOutcomeTag(r)}
        ${r.intent ? `<span class="tag mute">意图 ${esc(r.intent)}</span>` : ""}
        ${(r.action || r.next_action) ? `<span class="tag mute">${esc(r.action_label || r.action || r.next_action)}</span>` : ""}
        ${r.elapsed_ms != null ? `<span>${esc(r.elapsed_ms)}ms</span>` : ""}
      </div>
      <div class="bq-q">${esc(r.question || "")}</div>
      <div class="bq-a ${ans ? "" : "empty"}">${esc(ans || "（无正文回复）")}</div>
      ${r.reason ? `<p class="hint" style="margin:0.35rem 0 0">原因：${esc(r.reason)}</p>` : ""}
      ${notes.length ? `<p class="hint" style="margin:0.25rem 0 0">${notes.map(esc).join(" · ")}</p>` : ""}
      ${r.error ? `<p class="hint" style="margin:0.25rem 0 0;color:var(--danger)">${esc(r.error)}</p>` : ""}
    </div>`;
  }).join("");
}

function paintBatchStatus(snap) {
  const st = snap.status || {};
  const total = +st.total || 0;
  const done = +st.done || 0;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const running = !!st.running;
  const elProg = $("#bt-prog");
  if (elProg) elProg.style.width = `${pct}%`;
  const elMeta = $("#bt-meta");
  if (elMeta) {
    elMeta.innerHTML = `
      <span>进度 <b>${esc(done)}/${esc(total)}</b></span>
      <span>有回复 <b>${esc(st.ok || 0)}</b></span>
      <span>转人工 <b>${esc(st.handoff || 0)}</b></span>
      <span>跳过 <b>${esc(st.skip || 0)}</b></span>
      <span>错误 <b>${esc(st.error || 0)}</b></span>
      <span>${running ? "运行中…" : (st.finished_at ? "已结束" : "未开始")}</span>
      <span class="hint">${esc(st.last_message || "")}</span>`;
  }
  const btnStart = $("#bt-start");
  const btnStop = $("#bt-stop");
  if (btnStart) btnStart.disabled = running;
  if (btnStop) btnStop.disabled = !running;
  const filter = ($("#bt-filter") && $("#bt-filter").value) || "all";
  const list = $("#bt-qa-list");
  if (list) list.innerHTML = renderBatchQaList(snap.results || [], filter);
  const logs = $("#bt-logs");
  if (logs) logs.textContent = (snap.logs || []).slice(-40).join("\n");
}

async function pageBatch() {
  stopBatchPoll();
  const [phrases, snap] = await Promise.all([
    api("/api/admin/batch-test/phrases"),
    api("/api/admin/batch-test/status"),
  ]);
  const st = snap.status || {};
  const running = !!st.running;

  $("#main").innerHTML = `
    <h1>批量测试</h1>
    <p class="lead">导入求职者话术，一键跑真实判断与生成（不调招聘网页）。使用总控「模型与判断」中的密钥。跑完后按「问 / 答」审查。</p>

    <div class="card">
      <h2 style="margin-top:0">① 话术导入</h2>
      <p class="hint">每行一条；空行与 <code>#</code> 注释会被忽略。也可点「加载默认 100 条」。</p>
      <textarea id="bt-phrases" rows="12" style="font-family:ui-monospace,Consolas,monospace;font-size:0.85rem"
        placeholder="每行一条求职者消息…">${esc(phrases.text || "")}</textarea>
      <div class="row gap mt">
        <button id="bt-load-default" class="ghost" ${running ? "disabled" : ""}>加载默认 100 条</button>
        <button id="bt-save" class="ghost" ${running ? "disabled" : ""}>保存话术</button>
        <span class="hint" id="bt-count">当前 ${esc(phrases.count || 0)} 条</span>
      </div>
      <div class="mt" style="max-width:320px">
        <label>沟通职位（可选，写入会话上下文）</label>
        <input id="bt-job" type="text" value="${esc(st.job_title || "")}" placeholder="如：销售专员" ${running ? "disabled" : ""} />
      </div>
    </div>

    <div class="card">
      <h2 style="margin-top:0">② 测试效果</h2>
      <div class="row gap">
        <button id="bt-start" ${running ? "disabled" : ""}>开始测试</button>
        <button id="bt-stop" class="danger" ${running ? "" : "disabled"}>停止</button>
        <button id="bt-export" class="ghost">导出问答 JSON</button>
        <button id="bt-refresh" class="ghost">刷新结果</button>
      </div>
      <div class="progress mt" aria-hidden="true"><span id="bt-prog" style="width:0%"></span></div>
      <div class="batch-stats" id="bt-meta"></div>
      <details>
        <summary class="hint" style="cursor:pointer">运行日志</summary>
        <pre class="raw" id="bt-logs" style="max-height:180px;overflow:auto;margin-top:0.5rem"></pre>
      </details>
    </div>

    <div class="card">
      <h2 style="margin-top:0">③ 问答审查</h2>
      <div class="row gap" style="align-items:center;flex-wrap:wrap">
        <label style="margin:0">筛选</label>
        <select id="bt-filter" style="width:auto;min-width:140px">
          <option value="all">全部</option>
          <option value="ok">有正文回复</option>
          <option value="empty">无正文</option>
          <option value="handoff">转人工</option>
          <option value="error">错误</option>
        </select>
        <span class="hint">重点看：无关话术是否乱答、招聘问答是否空洞、转人工是否合理。</span>
      </div>
      <div id="bt-qa-list" class="mt"></div>
    </div>
  `;

  paintBatchStatus(snap);

  const refreshCount = () => {
    const n = ($("#bt-phrases").value || "").split(/\n/).filter(l => {
      const t = l.trim();
      return t && !t.startsWith("#");
    }).length;
    $("#bt-count").textContent = `编辑区约 ${n} 条（保存后生效）`;
  };
  $("#bt-phrases").oninput = refreshCount;

  $("#bt-load-default").onclick = async () => {
    try {
      const data = await api("/api/admin/batch-test/load-default", { method: "POST", body: "{}" });
      $("#bt-phrases").value = data.text || "";
      $("#bt-count").textContent = `当前 ${data.count || 0} 条`;
    } catch (e) {
      alert(e.message || e);
    }
  };

  $("#bt-save").onclick = async () => {
    try {
      const data = await api("/api/admin/batch-test/phrases", {
        method: "PUT",
        body: JSON.stringify({ text: $("#bt-phrases").value || "" }),
      });
      $("#bt-count").textContent = `当前 ${data.count || 0} 条`;
      alert(`已保存 ${data.count || 0} 条话术`);
    } catch (e) {
      alert(e.message || e);
    }
  };

  $("#bt-start").onclick = async () => {
    try {
      await api("/api/admin/batch-test/phrases", {
        method: "PUT",
        body: JSON.stringify({ text: $("#bt-phrases").value || "", persist: false }),
      });
      await api("/api/admin/batch-test/start", {
        method: "POST",
        body: JSON.stringify({ job_title: ($("#bt-job").value || "").trim() || null }),
      });
      startBatchPolling();
    } catch (e) {
      alert(e.message || e);
    }
  };

  $("#bt-stop").onclick = async () => {
    try {
      await api("/api/admin/batch-test/stop", { method: "POST", body: "{}" });
      paintBatchStatus(await api("/api/admin/batch-test/status"));
    } catch (e) {
      alert(e.message || e);
    }
  };

  $("#bt-refresh").onclick = async () => {
    paintBatchStatus(await api("/api/admin/batch-test/status"));
  };

  $("#bt-filter").onchange = async () => {
    paintBatchStatus(await api("/api/admin/batch-test/status"));
  };

  $("#bt-export").onclick = async () => {
    const data = await api("/api/admin/batch-test/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `batch_qa_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (running) startBatchPolling();
}

function startBatchPolling() {
  stopBatchPoll();
  state.batchPoll = setInterval(async () => {
    if (state.page !== "batch") {
      stopBatchPoll();
      return;
    }
    try {
      const snap = await api("/api/admin/batch-test/status");
      paintBatchStatus(snap);
      if (!(snap.status || {}).running) stopBatchPoll();
    } catch {
      /* ignore */
    }
  }, 1200);
}

async function pageSim() {
  let data = await api("/api/admin/sim/session");
  if (!data.session) {
    data = await api("/api/admin/sim/reset", {
      method: "POST",
      body: JSON.stringify({ force_reply: false }),
    });
  }
  let personas = state.simPersonas;
  if (!personas) {
    try {
      const pd = await api("/api/admin/sim/personas");
      personas = pd.items || [];
      state.simPersonas = personas;
    } catch {
      personas = [
        { id: "reask_hiring", label: "反复确认还招" },
        { id: "active", label: "积极求职" },
        { id: "salary", label: "紧盯薪资待遇" },
        { id: "brief", label: "惜字如金" },
        { id: "offtopic", label: "爱跑题" },
      ];
    }
  }
  const s = data.session;
  const j = s.last_judgment || {};
  const o = s.last_outcome || {};
  const stats = s.stats || {};
  const dlg = o.dialog || {};
  const lastAuto = s.last_auto || null;
  const savedPersona = state.simAutoPersona || s.auto_persona || "reask_hiring";
  const savedTurns = state.simAutoTurns || 5;
  const pendingN = (() => {
    let n = 0;
    for (const m of s.messages || []) {
      if (m.role === "recruiter") n = 0;
      else if (m.role === "candidate" && (m.text || "").trim()) n += 1;
    }
    return n;
  })();
  const autoReply = s.force_reply === true;

  $("#main").innerHTML = `
    <h1>模拟聊天</h1>
    <p class="lead">不调真实网页。使用总控已配置的大模型与 Jev。默认可<strong>连发多条牛人消息</strong>，再点<strong>立即回复</strong>。</p>

    <div class="card sim-auto-card">
      <h2 style="margin-top:0">Boss 多轮模拟（推荐演示）</h2>
      <p class="hint" style="margin-top:0">AI 扮演牛人连续发言，HR 侧走真实生成链路。</p>
      <div class="grid two">
        <div>
          <label>牛人人格</label>
          <select id="sim-persona">
            ${personas.map(p => `<option value="${esc(p.id)}" ${p.id === savedPersona ? "selected" : ""}>${esc(p.label)}</option>`).join("")}
          </select>
        </div>
        <div>
          <label>轮数（1～12）</label>
          <input id="sim-turns" type="number" min="1" max="12" value="${esc(savedTurns)}" />
        </div>
        <div>
          <label>开场白（可选）</label>
          <input id="sim-opener" placeholder="例如：你好，前台还招吗" value="${esc(state.simAutoOpener || "")}" />
        </div>
        <div>
          <label>沟通职位（可选）</label>
          <input id="sim-job" placeholder="${esc(s.job_title || "前台 人事")}" value="" />
        </div>
        <div>
          <label>牛人生成方式</label>
          <select id="sim-cand-mode">
            <option value="script" selected>脚本快测（推荐演示）</option>
            <option value="auto">自动（优先 LLM）</option>
            <option value="llm">仅 LLM 扮牛人</option>
          </select>
        </div>
      </div>
      <div class="row gap mt">
        <button type="button" id="sim-auto-run">开始多轮对打</button>
        <button type="button" class="ghost" id="sim-auto-continue">在当前会话再打 N 轮</button>
        <label class="check" style="margin:0"><input type="checkbox" id="sim-auto-reset" checked /> 开打前换人重置</label>
      </div>
      <p class="hint mt" id="sim-auto-hint">推荐人格「反复确认还招」：测是否还会复读同一句还在招。</p>
      ${lastAuto ? `<details class="mt open"><summary>上次多轮摘要（${esc(lastAuto.turns_done)}/${esc(lastAuto.turns_requested)} 轮）</summary>
        <pre class="raw">${esc((lastAuto.transcript || []).map(t =>
          `#${t.n} 牛：${t.candidate}\n   HR：${t.hr || "—"}\n   stage=${(t.dialog || {}).stage || "—"} goal=${(t.dialog || {}).goal || "—"} gate=${t.gate || "—"}`
        ).join("\n\n"))}</pre>
      </details>` : ""}
    </div>

    <div class="sim-layout">
      <div class="sim-phone">
        <div class="sim-topbar">
          <div class="name">${esc(s.candidate)}</div>
          <div class="meta">${esc(s.job_title)} · 模拟会话 ${esc(s.id)} · 回合 ${esc(stats.turns || 0)}</div>
        </div>
        <div class="sim-thread" id="sim-thread">
          ${(s.messages || []).map(renderSimMsg).join("")}
        </div>
        <div class="sim-composer">
          <div class="sim-action-label">牛人动作（点一下即发送）</div>
          <div class="sim-presets sim-actions">
            ${SIM_CAND_ACTIONS.map((p, i) => `<button type="button" data-cand-action="${i}">${esc(p.label)}</button>`).join("")}
          </div>
          <div class="sim-action-label">常用提问（填入输入框）</div>
          <div class="sim-presets">
            ${SIM_ASK_PRESETS.map((p, i) => `<button type="button" data-ask-preset="${i}">${esc(p.label)}</button>`).join("")}
          </div>
          <div class="sim-action-label">插入 HR（不自动回复）</div>
          <div class="sim-presets sim-hr-inject">
            ${SIM_HR_INJECT.map((p, i) => `<button type="button" class="ghost" data-hr-inject="${i}">${esc(p.label)}</button>`).join("")}
          </div>
          <textarea id="sim-input" placeholder="输入牛人消息…"></textarea>
          <div class="row gap">
            <button id="sim-send">发送</button>
            <button id="sim-reply-now" ${pendingN ? "" : "disabled"} type="button">立即回复${pendingN ? `（${pendingN}）` : ""}</button>
            <button class="ghost" id="sim-reset">换人 / 重连</button>
            <label class="check" style="margin:0"><input type="checkbox" id="sim-force" ${autoReply ? "checked" : ""} /> 发送后自动回复</label>
          </div>
          <p class="hint" id="sim-status">${pendingN
            ? `已积压 ${pendingN} 条牛人消息，点「立即回复」生成一条 HR`
            : "默认可连发多条牛人消息；勾选「发送后自动回复」则每条立刻回。"}</p>
        </div>
      </div>
      <div class="card sim-debug">
        <h2 style="margin-top:0">本轮诊断</h2>
        <div class="kv"><b>结果</b><span>${esc(o.outcome || "—")}</span></div>
        <div class="kv"><b>耗时</b><span>${o.elapsed_ms != null ? esc(o.elapsed_ms) + " ms" : "—"}</span></div>
        <div class="kv"><b>实际评判</b><span>${esc(o.effective_question || "—")}</span></div>
        <div class="kv"><b>意图</b><span>${esc(j.intent || "—")} (${esc(j.intent_confidence ?? "—")})</span></div>
        <div class="kv"><b>需回复</b><span>${j.need_reply === false ? "否（跳过）" : j.need_reply === true ? "是" : "—"}${j.ambiguous ? " · 歧义" : ""}</span></div>
        <div class="kv"><b>FAQ</b><span>${esc(o.faq_title || "—")} ${o.faq_confidence != null ? "(" + esc(o.faq_confidence) + ")" : ""}</span></div>
        <div class="kv"><b>2.0 阶段</b><span>${esc(dlg.stage || "—")} · ${esc(dlg.goal || "—")}</span></div>
        <div class="kv"><b>已说</b><span>${esc((dlg.said || []).join("、") || "—")}</span></div>
        <div class="kv"><b>门禁</b><span>${esc(o.gate || "—")}</span></div>
        <div class="kv"><b>建议动作</b><span>${esc(j.next_action_label || j.next_action || "—")} (${esc(j.action_confidence ?? "—")})</span></div>
        <div class="kv"><b>可执行</b><span>${j.actionable === true ? "是" : j.actionable === false ? "否" : "—"}</span></div>
        <div class="kv"><b>转人工</b><span>${esc(j.handoff_prob ?? "—")} / redline ${esc(j.redline_prob ?? "—")}</span></div>
        <div class="kv"><b>引擎</b><span>${esc(j.engine || "—")}</span></div>
        <div class="kv"><b>原因</b><span>${esc(o.reason || j.reason || "—")}</span></div>
        <p class="hint mt">计数：回复 ${esc(stats.replies || 0)} · 动作 ${esc(stats.actions || 0)} · 转人工 ${esc(stats.handoff || 0)}</p>
        <details class="mt"><summary>原始 judgment</summary>
          <pre class="raw">${esc(JSON.stringify(j, null, 2) || "{}")}</pre>
        </details>
      </div>
    </div>`;

  const thread = $("#sim-thread");
  if (thread) thread.scrollTop = thread.scrollHeight;

  const setBusy = (busy, msg) => {
    const btn = $("#sim-send");
    if (btn) btn.disabled = busy;
    const replyBtn = $("#sim-reply-now");
    if (replyBtn) replyBtn.disabled = busy || (!pendingN && !busy);
    const autoBtn = $("#sim-auto-run");
    if (autoBtn) autoBtn.disabled = busy;
    const contBtn = $("#sim-auto-continue");
    if (contBtn) contBtn.disabled = busy;
    $$("[data-cand-action], [data-hr-inject]").forEach((b) => { b.disabled = busy; });
    const status = $("#sim-status");
    if (msg != null && status) status.textContent = msg;
    const hint = $("#sim-auto-hint");
    if (msg != null && hint) hint.textContent = msg;
  };

  const syncForce = async () => {
    const force = !!$("#sim-force")?.checked;
    if (force !== !!s.force_reply) {
      await api("/api/admin/sim/session", {
        method: "PATCH",
        body: JSON.stringify({ force_reply: force }),
      });
      s.force_reply = force;
    }
  };

  $("#sim-force")?.addEventListener("change", () => { syncForce().catch(() => {}); });

  $("#sim-reply-now").onclick = async () => {
    setBusy(true, "正在生成 HR 回复…");
    try {
      await api("/api/admin/sim/reply-now", { method: "POST", body: "{}" });
      await pageSim();
    } catch (e) {
      setBusy(false, e.message || String(e));
    }
  };

  const runAuto = async (resetFirst) => {
    const persona = $("#sim-persona")?.value || "reask_hiring";
    const turns = Math.max(1, Math.min(12, +($("#sim-turns")?.value || 5)));
    const opener = ($("#sim-opener")?.value || "").trim();
    const job = ($("#sim-job")?.value || "").trim();
    const candidate_mode = $("#sim-cand-mode")?.value || "script";
    state.simAutoPersona = persona;
    state.simAutoTurns = turns;
    state.simAutoOpener = opener;
    setBusy(true, `多轮对打中（${persona} · ${turns} 轮 · ${candidate_mode}）…`);
    try {
      await api("/api/admin/sim/auto", {
        method: "POST",
        body: JSON.stringify({
          persona,
          turns,
          opener: opener || null,
          reset_first: !!resetFirst,
          job_title: job || null,
          candidate_mode,
        }),
      });
      await pageSim();
    } catch (e) {
      setBusy(false, e.message || String(e));
    }
  };

  $("#sim-auto-run").onclick = () => runAuto($("#sim-auto-reset")?.checked !== false);
  $("#sim-auto-continue").onclick = () => runAuto(false);

  const sendText = async (text) => {
    const t = (text || "").trim();
    if (!t) return;
    setBusy(true, "发送中…");
    try {
      await syncForce();
      await api("/api/admin/sim/message", {
        method: "POST",
        body: JSON.stringify({ text: t }),
      });
      await pageSim();
    } catch (e) {
      setBusy(false, e.message || String(e));
    }
  };

  $$("[data-cand-action]").forEach((btn) => {
    btn.onclick = () => {
      const p = SIM_CAND_ACTIONS[+btn.dataset.candAction];
      if (p) sendText(p.text);
    };
  });

  $$("[data-ask-preset]").forEach((btn) => {
    btn.onclick = () => {
      const p = SIM_ASK_PRESETS[+btn.dataset.askPreset];
      if (p) $("#sim-input").value = p.text;
    };
  });

  $$("[data-hr-inject]").forEach((btn) => {
    btn.onclick = async () => {
      const p = SIM_HR_INJECT[+btn.dataset.hrInject];
      if (!p) return;
      setBusy(true, "插入 HR 消息…");
      try {
        await api("/api/admin/sim/inject-hr", {
          method: "POST",
          body: JSON.stringify({ text: p.text }),
        });
        await pageSim();
      } catch (e) {
        setBusy(false, e.message || String(e));
      }
    };
  });

  $("#sim-reset").onclick = async () => {
    const force = $("#sim-force").checked;
    const name = prompt("新牛人昵称（留空随机）", "") || "";
    const job = prompt("沟通职位（留空默认）", s.job_title || "") || "";
    await api("/api/admin/sim/reset", {
      method: "POST",
      body: JSON.stringify({
        candidate: name.trim() || null,
        job_title: job.trim() || null,
        force_reply: force,
      }),
    });
    pageSim();
  };

  const send = async () => {
    const text = ($("#sim-input").value || "").trim();
    if (!text) return;
    $("#sim-input").value = "";
    await sendText(text);
  };

  $("#sim-send").onclick = send;
  $("#sim-input").onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };
}

function renderSimMsg(m) {
  const role = m.role || "system";
  if (role === "system") {
    const warn = /转人工|质检未过|空草稿|待确认简历覆盖|⚠️/.test(m.text || "");
    return `<div class="sim-sys ${warn ? "warn" : ""}"><span>${esc(m.text)}</span></div>`;
  }
  if (role === "candidate") {
    return `<div class="sim-bubble-row">
      <div class="sim-avatar">牛</div>
      <div><div class="sim-bubble cand">${esc(m.text)}</div>
      <div class="sim-time">${esc(m.ts || "")}</div></div>
    </div>`;
  }
  if (role === "draft") {
    return `<div class="sim-bubble-row me">
      <div><div class="sim-bubble draft">草稿（未发送）\n${esc(m.text)}</div>
      <div class="sim-time">${esc(m.ts || "")}</div></div>
      <div class="sim-avatar">草</div>
    </div>`;
  }
  return `<div class="sim-bubble-row me">
    <div><div class="sim-bubble hr">${esc(m.text)}</div>
    <div class="sim-time">${esc(m.ts || "")} · 已读</div></div>
    <div class="sim-avatar">HR</div>
  </div>`;
}

boot();
