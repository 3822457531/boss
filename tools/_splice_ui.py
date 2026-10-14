"""One-shot: splice models/positions UI into app.js."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
app = ROOT / "admin" / "static" / "app.js"
extra = ROOT / "admin" / "static" / "_models_positions.js"

text = app.read_text(encoding="utf-8")
extra_text = extra.read_text(encoding="utf-8")

marker_start = "async function renderLlm() {"
marker_end = "async function renderPolicy() {"
i = text.find(marker_start)
j = text.find(marker_end)
if i < 0 or j < i:
    raise SystemExit(f"markers not found: {i=} {j=}")

cut = extra_text.find("async function renderLlm() {\n  state.modelsTab")
if cut < 0:
    cut = extra_text.find("function bindClearKey")
body = extra_text[:cut].rstrip() + "\n\n"

m = re.search(
    r"function bindClearKey\(inputId, endpoint, reload\) \{.*?\n\}",
    text[i:j],
    re.S,
)
bind_fn = (m.group(0) + "\n\n") if m else (
    "function bindClearKey(inputId, endpoint, reload) {\n"
    '  const btn = document.querySelector(`[data-clear-key="${inputId}"]`);\n'
    "  if (!btn) return;\n"
    "  btn.onclick = async () => {\n"
    '    if (!confirm("确认清除已保存的密钥？")) return;\n'
    '    await api(endpoint, { method: "DELETE" });\n'
    "    reload();\n"
    "  };\n"
    "}\n\n"
)

new_block = (
    body
    + "\n"
    + bind_fn
    + """async function renderLlm() {
  state.modelsTab = "llm";
  return renderModels();
}

async function renderJev() {
  state.modelsTab = "jev";
  return renderModels();
}

async function renderFaq() {
  return renderPositions();
}

"""
)
text2 = text[:i] + new_block + text[j:]

old_pages = """const pages = {
  overview: renderOverview,
  sim: renderSim,
  batch: renderBatch,
  auto: renderAuto,
  greet: renderGreet,
  llm: renderLlm,
  jev: renderJev,
  faq: renderFaq,
  policy: renderPolicy,
  rate: renderRate,
  models: renderModelLogs,
  logs: renderLogs,
};"""
new_pages = """const pages = {
  overview: renderOverview,
  sim: renderSim,
  batch: renderBatch,
  auto: renderAuto,
  greet: renderGreet,
  models: renderModels,
  positions: renderPositions,
  llm: renderLlm,
  jev: renderJev,
  faq: renderFaq,
  policy: renderPolicy,
  rate: renderRate,
  modelogs: renderModelLogs,
  logs: renderLogs,
};"""
if old_pages not in text2:
    raise SystemExit("pages block not found")
text2 = text2.replace(old_pages, new_pages)

# Policy: remove jobs JSON
text2 = text2.replace(
    """      <h2>敏感词（命中转人工）</h2>
      <textarea id="sensitive">${esc((p.sensitive_words || []).join("\\n"))}</textarea>
      <h2>岗位</h2>
      <textarea id="jobs-json">${esc(JSON.stringify(p.jobs || [], null, 2))}</textarea>
      <button class="mt" id="policy-save">保存</button>""",
    """      <h2>敏感词（命中转人工）</h2>
      <textarea id="sensitive">${esc((p.sensitive_words || []).join("\\n"))}</textarea>
      <p class="hint mt">职位与专属知识库请到「职位与知识库」页管理（boss positions 同步）。</p>
      <button class="mt" id="policy-save">保存</button>""",
)

text2 = text2.replace(
    """  $("#policy-save").onclick = async () => {
    let jobs;
    try { jobs = JSON.parse($("#jobs-json").value); }
    catch { return alert("岗位 JSON 无效"); }
    if ($("#send-mode").value === "auto_send" && p.send_mode !== "auto_send") {
      const ok = await confirmModal("切换到自动发送", "确认启用全自动发送？");
      if (!ok) return;
    }
    const next = { ...p, send_mode: "auto_send", jobs };""",
    """  $("#policy-save").onclick = async () => {
    if ($("#send-mode").value === "auto_send" && p.send_mode !== "auto_send") {
      const ok = await confirmModal("切换到自动发送", "确认启用全自动发送？");
      if (!ok) return;
    }
    const next = { ...p, send_mode: "auto_send" };""",
)

app.write_text(text2, encoding="utf-8")
print("spliced ok", len(text), "->", len(text2))
