"""
studio/compare_server.py
Local server for the generation pipeline comparison UI.
Run: python studio/compare_server.py
Open: http://localhost:7700
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

import json as _json
from datetime import datetime, date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn


def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

from core.db.connection import db_query

app = FastAPI()

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Curia — Pipeline Compare</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f0f0f;
    color: #d4d4d4;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  header {
    padding: 12px 20px;
    border-bottom: 1px solid #1e1e1e;
    display: flex;
    align-items: center;
    gap: 14px;
    flex-shrink: 0;
    height: 48px;
  }
  header h1 { font-size: 13px; font-weight: 600; color: #fff; letter-spacing: 0.02em; }
  #run-count { font-size: 11px; color: #444; }
  #refresh-btn {
    margin-left: auto;
    padding: 5px 12px;
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 5px;
    color: #888;
    font-size: 11px;
    cursor: pointer;
  }
  #refresh-btn:hover { background: #222; color: #ccc; }

  /* ── main layout ── */
  #columns-wrapper {
    flex: 1;
    display: flex;
    overflow-x: auto;
    overflow-y: hidden;
    padding: 14px;
    gap: 10px;
    align-items: stretch;   /* columns fill full height */
  }

  /* ── column ── */
  .column {
    flex: 0 0 360px;
    background: #141414;
    border: 1px solid #1e1e1e;
    border-radius: 10px;
    display: flex;
    flex-direction: column;
    min-height: 0;          /* critical: lets flex child shrink */
    overflow: hidden;
  }

  .col-header {
    padding: 14px 16px 12px;
    border-bottom: 1px solid #1e1e1e;
    flex-shrink: 0;
  }
  .col-header .run-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #444;
    margin-bottom: 5px;
  }
  .col-header .run-title {
    font-size: 13px;
    font-weight: 600;
    color: #eee;
    line-height: 1.4;
  }
  .col-header .run-meta {
    font-size: 11px;
    color: #3a3a3a;
    margin-top: 5px;
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .col-header .run-show {
    font-size: 10px;
    background: #1c1c1c;
    border: 1px solid #252525;
    border-radius: 4px;
    padding: 2px 7px;
    color: #555;
  }

  /* ── stages scroll area ── */
  .stages {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 5px;
    min-height: 0;
  }

  /* ── individual stage ── */
  .stage {
    border: 1px solid #1e1e1e;
    border-radius: 7px;
    flex-shrink: 0;          /* don't compress collapsed stages */
  }

  .stage-header {
    padding: 9px 12px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 10px;
    background: #191919;
    border-radius: 6px;
    user-select: none;
    transition: background 0.1s;
  }
  .stage-header:hover { background: #1f1f1f; }
  .stage.open .stage-header { border-radius: 6px 6px 0 0; }

  .stage-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #555;
    flex-shrink: 0;
    width: 64px;
  }
  .stage-preview {
    font-size: 11px;
    color: #383838;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .stage-arrow {
    font-size: 9px;
    color: #333;
    flex-shrink: 0;
    transition: transform 0.15s;
  }
  .stage.open .stage-arrow { transform: rotate(90deg); }

  .stage-body {
    display: none;
    padding: 12px;
    background: #111;
    border-top: 1px solid #1a1a1a;
    border-radius: 0 0 6px 6px;
    max-height: 500px;
    overflow-y: auto;
  }
  .stage.open .stage-body { display: block; }

  .stage-body pre {
    font-size: 11px;
    line-height: 1.75;
    white-space: pre-wrap;
    word-break: break-word;
    color: #aaa;
    font-family: "SF Mono", "Fira Code", monospace;
  }

  /* ── prompt sub-section ── */
  .prompt-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    margin-bottom: 10px;
    user-select: none;
  }
  .prompt-toggle-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #333;
  }
  .prompt-toggle-label:hover { color: #555; }
  .prompt-toggle-arrow { font-size: 8px; color: #2a2a2a; transition: transform 0.12s; }
  .prompt-block { display: none; margin-bottom: 12px; padding: 10px; background: #0d0d0d; border: 1px solid #1a1a1a; border-radius: 5px; }
  .prompt-block.open { display: block; }
  .prompt-block pre { color: #555; font-size: 10.5px; }
  .output-divider { height: 1px; background: #1a1a1a; margin-bottom: 12px; }

  /* ── idea meta ── */
  .idea-field { margin-bottom: 10px; }
  .idea-field-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: #444;
    margin-bottom: 3px;
  }
  .idea-field-value { font-size: 12px; color: #bbb; line-height: 1.6; }

  .source-chip {
    display: inline-block;
    background: #181818;
    border: 1px solid #222;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 10px;
    color: #666;
    margin: 2px 2px 0 0;
    line-height: 1.5;
  }

  /* ── transcript ── */
  .transcript-line {
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid #181818;
  }
  .transcript-line:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
  .transcript-speaker {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #444;
    margin-bottom: 4px;
  }
  .transcript-text { font-size: 12px; line-height: 1.75; color: #c0c0c0; }

  /* ── outline ── */
  .outline-title { font-size: 13px; font-weight: 600; color: #ddd; margin-bottom: 4px; }
  .outline-thread { font-size: 11px; color: #666; font-style: italic; margin-bottom: 12px; line-height: 1.5; }
  .outline-seg { margin-bottom: 8px; }
  .outline-seg-num { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #444; margin-bottom: 2px; }
  .outline-seg-focus { font-size: 12px; color: #bbb; }
  .outline-seg-key { font-size: 11px; color: #555; margin-top: 2px; }

  .empty-state { padding: 60px 20px; color: #2a2a2a; font-size: 13px; text-align: center; }

  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #252525; border-radius: 2px; }
</style>
</head>
<body>
<header>
  <h1>Curia — Pipeline Compare</h1>
  <span id="run-count"></span>
  <button id="refresh-btn" onclick="loadRuns()">Refresh</button>
</header>
<div id="columns-wrapper">
  <div class="empty-state">Loading...</div>
</div>

<script>
async function loadRuns() {
  const wrapper = document.getElementById('columns-wrapper');
  const res = await fetch('/api/runs');
  const runs = await res.json();

  document.getElementById('run-count').textContent = runs.length + ' runs';
  wrapper.innerHTML = '';

  if (!runs.length) {
    wrapper.innerHTML = '<div class="empty-state">No runs yet.<br>Use run_and_log.py to generate.</div>';
    return;
  }

  for (const run of runs) wrapper.appendChild(buildColumn(run));
}

function buildColumn(run) {
  const col = document.createElement('div');
  col.className = 'column';

  const ts = run.created_at
    ? new Date(run.created_at).toLocaleString('en-US', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})
    : '';

  const labelText = run.run_label || '';

  col.innerHTML = `
    <div class="col-header">
      ${labelText ? `<div class="run-label">${esc(labelText)}</div>` : ''}
      <div class="run-title">${esc(run.episode_title || run.idea_title || 'Untitled')}</div>
      <div class="run-meta">
        <span>${esc(ts)}</span>
        <span class="run-show">${esc(run.show_name || '')}</span>
      </div>
    </div>
    <div class="stages"></div>
  `;

  const stages = col.querySelector('.stages');

  // — Idea
  stages.appendChild(buildStage('Idea', buildIdeaEl(run), null, null));

  // — Briefing (template assembly, no LLM)
  stages.appendChild(buildStage('Briefing', null, run.briefing || '', 'You are preparing a briefing'));

  // — Outline
  stages.appendChild(buildStage('Outline', buildOutlineEl(run.outline), null, null,
    buildPromptText(run.outline_prompt, run.outline_human)));

  // — Transcript
  stages.appendChild(buildStage('Transcript', buildTranscriptEl(run.transcript), null, null,
    buildPromptText(run.transcript_prompt, run.transcript_human)));

  return col;
}

function buildPromptText(system, human) {
  if (!system && !human) return null;
  const parts = [];
  if (system) parts.push('=== SYSTEM ===\n' + system);
  if (human) parts.push('=== HUMAN ===\n' + human);
  return parts.join('\n\n');
}

function buildStage(label, customEl, rawText, previewOverride, promptText) {
  const stage = document.createElement('div');
  stage.className = 'stage';

  // preview text
  let preview = previewOverride || '';
  if (!preview && rawText) {
    preview = rawText.replace(/\n/g, ' ').trim().slice(0, 70);
  }
  if (!preview && customEl) {
    preview = customEl.dataset.preview || '';
  }

  stage.innerHTML = `
    <div class="stage-header" onclick="this.parentElement.classList.toggle('open')">
      <span class="stage-label">${esc(label)}</span>
      <span class="stage-preview">${esc(preview)}</span>
      <span class="stage-arrow">&#9654;</span>
    </div>
    <div class="stage-body"></div>
  `;

  const body = stage.querySelector('.stage-body');

  // prompt sub-section
  if (promptText) {
    const toggle = document.createElement('div');
    toggle.className = 'prompt-toggle';
    toggle.innerHTML = `<span class="prompt-toggle-arrow">&#9654;</span><span class="prompt-toggle-label">Prompt</span>`;
    const block = document.createElement('div');
    block.className = 'prompt-block';
    const pre = document.createElement('pre');
    pre.textContent = promptText;
    block.appendChild(pre);
    toggle.onclick = () => {
      block.classList.toggle('open');
      toggle.querySelector('.prompt-toggle-arrow').style.transform =
        block.classList.contains('open') ? 'rotate(90deg)' : '';
    };
    body.appendChild(toggle);
    body.appendChild(block);
    const div = document.createElement('div');
    div.className = 'output-divider';
    body.appendChild(div);
  }

  // output content
  if (customEl) {
    body.appendChild(customEl);
  } else {
    const pre = document.createElement('pre');
    pre.textContent = rawText || '';
    body.appendChild(pre);
  }

  return stage;
}

function buildIdeaEl(run) {
  const div = document.createElement('div');
  div.dataset.preview = run.idea_title || '';

  const fields = [
    ['Idea title', run.idea_title],
    ['Angle', run.idea_angle],
    ['Editorial direction', run.editorial_direction],
  ];

  fields.forEach(([label, val]) => {
    if (!val) return;
    const f = document.createElement('div');
    f.className = 'idea-field';
    f.innerHTML = `<div class="idea-field-label">${esc(label)}</div><div class="idea-field-value">${esc(val)}</div>`;
    div.appendChild(f);
  });

  if (run.source_titles && run.source_titles.length) {
    const f = document.createElement('div');
    f.className = 'idea-field';
    f.innerHTML = `<div class="idea-field-label">Sources</div>`;
    run.source_titles.forEach(t => {
      const chip = document.createElement('span');
      chip.className = 'source-chip';
      chip.textContent = t;
      f.appendChild(chip);
    });
    div.appendChild(f);
  }

  return div;
}

function buildOutlineEl(rawOutline) {
  const div = document.createElement('div');
  div.dataset.preview = '';

  let o = null;
  try { o = JSON.parse(rawOutline || '{}'); } catch(e) {}

  if (!o) {
    const pre = document.createElement('pre');
    pre.textContent = rawOutline || '';
    div.appendChild(pre);
    return div;
  }

  div.dataset.preview = o.title || '';

  if (o.title) {
    const t = document.createElement('div');
    t.className = 'outline-title';
    t.textContent = o.title;
    div.appendChild(t);
  }
  if (o.thread || o.central_tension) {
    const th = document.createElement('div');
    th.className = 'outline-thread';
    th.textContent = o.thread || o.central_tension;
    div.appendChild(th);
  }
  (o.segments || []).forEach((seg, i) => {
    const s = document.createElement('div');
    s.className = 'outline-seg';
    s.innerHTML = `
      <div class="outline-seg-num">Segment ${seg.segment || i+1}</div>
      <div class="outline-seg-focus">${esc(seg.focus || '')}</div>
      ${seg.key_point ? `<div class="outline-seg-key">${esc(seg.key_point)}</div>` : ''}
    `;
    div.appendChild(s);
  });

  return div;
}

function buildTranscriptEl(rawTranscript) {
  const div = document.createElement('div');
  div.dataset.preview = '';

  let lines = [];
  try { lines = JSON.parse(rawTranscript || '[]'); } catch(e) {}

  if (!lines.length) {
    div.textContent = rawTranscript || '';
    return div;
  }

  div.dataset.preview = `${lines.length} lines`;

  lines.forEach(line => {
    const item = document.createElement('div');
    item.className = 'transcript-line';
    item.innerHTML = `
      <div class="transcript-speaker">${esc(line.speaker || 'Kenji')}</div>
      <div class="transcript-text">${esc(line.text || '')}</div>
    `;
    div.appendChild(item);
  });

  return div;
}

function esc(str) {
  return String(str || '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

loadRuns();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.get("/api/runs")
async def get_runs():
    try:
        result = await db_query(
            "SELECT * FROM generation_run ORDER BY created_at DESC",
            {}
        )
        rows = result or []
    except Exception:
        rows = []
    for row in rows:
        row["id"] = str(row.get("id", "")).replace("generation_run:", "")
    return HTMLResponse(
        content=_json.dumps(rows, default=json_serial),
        media_type="application/json"
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7700, log_level="warning")
