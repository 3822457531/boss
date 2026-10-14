# -*- coding: utf-8 -*-
from pathlib import Path

root = Path(r"D:\workSpace\boss-style-kb\admin\static")
p = root / "app.js"
text = p.read_text(encoding="utf-8")
start = text.index("async function renderLlm()")
end = text.index("async function renderPolicy()")
new = (root / "_models_positions.js").read_text(encoding="utf-8")
text = text[:start] + new + "\n\n" + text[end:]

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

if old_pages not in text:
    raise SystemExit("pages block not found")
text = text.replace(old_pages, new_pages, 1)
p.write_text(text, encoding="utf-8")
print("ok", len(text))
