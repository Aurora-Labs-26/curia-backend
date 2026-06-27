"""
api/routes/eval.py
Web UI for golden dataset evaluation — no auth required (dev/QA tool).

Routes:
  GET  /eval                         → single-page HTML app
  POST /eval/ingest                  → {url} → {source_id, status}
  GET  /eval/sources/{source_id}     → status + transforms
  POST /eval/sources/{source_id}/verdict → save a verdict
  GET  /eval/stats                   → verdict stats JSON
  GET  /eval/export.csv              → CSV download of all verdicts
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from core.db.connection import db_fetchrow, db_query
from core.ingest import get_or_create_source, process_source

router = APIRouter(prefix="/eval", tags=["eval"])

DATA_DIR = Path("data")
VERDICTS_FILE = DATA_DIR / "eval_verdicts.jsonl"

TRANSFORMS = [
    "summary", "metadata", "key_insights",
    "human_stakes", "core_tensions", "counterpoints", "examples",
]


# ── Verdict storage ────────────────────────────────────────────────────────────

def _append_verdict(record: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(VERDICTS_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def _load_verdicts() -> list[dict]:
    if not VERDICTS_FILE.exists():
        return []
    rows = []
    with open(VERDICTS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


# ── Background ingest ─────────────────────────────────────────────────────────

async def _run_ingest(source_id: str) -> None:
    try:
        await process_source(source_id=source_id)
    except Exception:
        pass  # process_source already sets status=failed with error message


# ── Pydantic models ───────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    url: str


class VerdictRequest(BaseModel):
    transform: str
    verdict: str         # "good" | "bad" | "edit"
    golden: Optional[str] = None
    note: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def eval_ingest(body: IngestRequest, bg: BackgroundTasks):
    user = await db_fetchrow("SELECT id FROM users LIMIT 1")
    if not user:
        raise HTTPException(500, "No users found — run scripts/create_user.py first")
    user_id = str(user["id"])

    source_id = await get_or_create_source(url=body.url, user_id=user_id)
    row = await db_fetchrow(
        "SELECT status FROM source WHERE id = $id::uuid", {"id": source_id}
    )
    status = (row or {}).get("status", "queued")

    if status not in ("ready", "failed"):
        bg.add_task(_run_ingest, source_id)

    return {"source_id": source_id, "status": status}


@router.get("/sources/{source_id}")
async def eval_source(source_id: str):
    source = await db_fetchrow(
        "SELECT id, title, url, status, full_text, error FROM source WHERE id = $id::uuid",
        {"id": source_id},
    )
    if not source:
        raise HTTPException(404, "Source not found")

    transforms: dict = {}
    if source["status"] == "ready":
        rows = await db_query(
            "SELECT insight_type, content FROM source_insight WHERE source_id = $id::uuid",
            {"id": source_id},
        )
        transforms = {r["insight_type"]: r["content"] for r in (rows or [])}

    return {
        "source_id": source_id,
        "title": source["title"],
        "url": source["url"],
        "status": source["status"],
        "error": source["error"],
        "char_count": len(source["full_text"] or ""),
        "full_text": source["full_text"],
        "transforms": transforms,
    }


@router.post("/sources/{source_id}/verdict")
async def eval_verdict(source_id: str, body: VerdictRequest):
    source = await db_fetchrow(
        "SELECT title, url FROM source WHERE id = $id::uuid", {"id": source_id}
    )
    if not source:
        raise HTTPException(404, "Source not found")
    _append_verdict({
        "source_id": source_id,
        "url": source["url"],
        "title": source["title"],
        "transform": body.transform,
        "verdict": body.verdict,
        "golden": body.golden,
        "note": body.note,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True}


@router.get("/stats")
async def eval_stats():
    verdicts = _load_verdicts()
    counts: dict[str, dict[str, int]] = {}
    for v in verdicts:
        t = v.get("transform", "?")
        counts.setdefault(t, {"good": 0, "bad": 0})
        key = "good" if v.get("verdict") == "good" else "bad"
        counts[t][key] += 1

    stats = []
    for t in (["scrape"] + TRANSFORMS):
        if t not in counts:
            continue
        c = counts[t]
        total = c["good"] + c["bad"]
        stats.append({
            "transform": t,
            "good": c["good"],
            "bad": c["bad"],
            "total": total,
            "pass_rate": round(c["good"] / total * 100) if total else 0,
        })
    return {"stats": stats, "total_verdicts": len(verdicts)}


@router.get("/export.csv")
async def eval_export():
    verdicts = _load_verdicts()
    out = io.StringIO()
    fields = ["source_id", "url", "title", "transform", "verdict", "golden", "note", "ts"]
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    for v in verdicts:
        w.writerow({k: v.get(k, "") for k in fields})
    out.seek(0)
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=eval_verdicts.csv"},
    )


@router.get("", response_class=HTMLResponse)
async def eval_ui():
    return HTMLResponse(content=_HTML)


# ── Single-page app ───────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>  <!-- v2 -->
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curia Eval</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f6f8fa;--surface:#fff;--border:#d0d7de;--text:#1f2328;
  --muted:#656d76;--good:#1a7f37;--bad:#cf222e;--accent:#0969da;
  --r:6px;--mono:'SF Mono',Menlo,Monaco,Consolas,monospace;
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh}

header{background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 24px;display:flex;align-items:center;justify-content:space-between;
  height:52px;position:sticky;top:0;z-index:10;gap:12px}
.logo{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px;white-space:nowrap}
.badge{font-size:10px;font-weight:500;padding:2px 7px;border-radius:10px;
  background:#ddf4ff;color:var(--accent);letter-spacing:.03em}
nav{display:flex;gap:16px;flex-shrink:0}
nav a{font-size:13px;color:var(--accent);text-decoration:none}
nav a:hover{text-decoration:underline}

.wrap{max-width:820px;margin:0 auto;padding:28px 16px}

.ingest-row{display:flex;gap:8px;margin-bottom:20px}
.ingest-row input{flex:1;padding:8px 12px;border:1px solid var(--border);
  border-radius:var(--r);font-size:14px;background:var(--surface);color:var(--text);
  min-width:0}
.ingest-row input:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px rgba(9,105,218,.12)}
.btn-primary{padding:8px 18px;background:var(--accent);color:#fff;border:none;
  border-radius:var(--r);font-size:14px;font-weight:500;cursor:pointer;white-space:nowrap}
.btn-primary:hover{background:#0757ba}
.btn-primary:disabled{background:#8c959f;cursor:not-allowed}

.status-bar{display:none;align-items:center;gap:10px;padding:10px 14px;
  background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
  margin-bottom:16px;font-size:13px;color:var(--muted)}
.spin{width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);
  border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0}
@keyframes spin{to{transform:rotate(360deg)}}

.err{display:none;padding:10px 14px;background:#fff8c5;border:1px solid #d4a72c;
  border-radius:var(--r);color:#633c01;font-size:13px;margin-bottom:16px}

.src-hdr{display:none;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border)}
.src-hdr h2{font-size:18px;font-weight:600;margin-bottom:4px;line-height:1.3}
.src-meta{font-size:13px;color:var(--muted)}

#results{display:none}

.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
  margin-bottom:10px;transition:border-color .2s,box-shadow .15s}
.card.v-good{border-color:var(--good);box-shadow:0 0 0 1px var(--good)}
.card.v-bad {border-color:var(--bad); box-shadow:0 0 0 1px var(--bad)}

.c-head{display:flex;align-items:center;justify-content:space-between;
  padding:9px 14px;background:var(--bg);border-bottom:1px solid var(--border);
  border-radius:var(--r) var(--r) 0 0;gap:8px}
.c-label{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:700;
  letter-spacing:.07em;text-transform:uppercase}
.tier{font-size:10px;font-weight:500;padding:2px 6px;border-radius:10px}
.t1{background:#ddf4ff;color:#0550ae}.t2{background:#f6f8fa;color:var(--muted)}

.c-acts{display:flex;align-items:center;gap:6px}
.saved{font-size:11px;color:var(--good);opacity:0;transition:opacity .3s}
.saved.show{opacity:1}
.btn-g,.btn-b{padding:4px 11px;border-radius:4px;font-size:12px;font-weight:500;
  cursor:pointer;border:1px solid;transition:all .15s}
.btn-g{border-color:#2da44e;color:#2da44e;background:transparent}
.btn-g:hover,.btn-g.on{background:#2da44e;color:#fff}
.btn-b{border-color:var(--bad);color:var(--bad);background:transparent}
.btn-b:hover,.btn-b.on{background:var(--bad);color:#fff}
.btn-e{border-color:var(--accent);color:var(--accent);background:transparent}
.btn-e:hover,.btn-e.on{background:var(--accent);color:#fff}
.card.v-edit{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}

.c-body{padding:12px 14px}
pre.ct{font-family:var(--mono);font-size:12px;line-height:1.65;white-space:pre-wrap;
  word-break:break-word;max-height:240px;overflow-y:auto;color:var(--text)}
pre.ct.nil{color:var(--muted);font-style:italic}

.edit-area{display:none;margin-top:10px;padding-top:10px;border-top:1px dashed var(--border)}
.edit-area label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
  color:var(--accent);display:block;margin-bottom:6px}
.edit-area textarea{width:100%;min-height:110px;padding:8px 10px;font-family:var(--mono);
  font-size:12px;line-height:1.5;border:1px solid var(--accent);border-radius:4px;
  resize:vertical;color:var(--text)}
.edit-area textarea:focus{outline:none}
.edit-btns{display:flex;gap:6px;margin-top:6px;justify-content:flex-end}
.bsm{padding:4px 12px;border-radius:4px;font-size:12px;font-weight:500;cursor:pointer}
.bsave{background:var(--accent);color:#fff;border:none}.bsave:hover{background:#0757ba}
.bskip{background:transparent;color:var(--muted);border:1px solid var(--border)}
.bskip:hover{border-color:var(--muted)}

.note-row{margin-top:10px;padding-top:8px;border-top:1px solid var(--border)}
.note-row textarea{width:100%;padding:6px 10px;font-family:inherit;font-size:12px;
  line-height:1.4;border:1px solid var(--border);border-radius:4px;resize:none;
  color:var(--text);min-height:52px}
.note-row textarea:focus{outline:none;border-color:var(--accent)}

.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);
  z-index:100;align-items:center;justify-content:center}
.modal-bg.open{display:flex}
.modal{background:var(--surface);border-radius:8px;padding:24px;
  width:min(480px,92vw);position:relative;max-height:85vh;overflow-y:auto}
.modal h3{font-size:15px;font-weight:600;margin-bottom:16px}
.mcl{position:absolute;top:14px;right:14px;background:none;border:none;
  font-size:20px;cursor:pointer;color:var(--muted);padding:2px 6px;border-radius:4px}
.mcl:hover{background:var(--bg)}

.sr{display:flex;align-items:center;gap:8px;padding:7px 0;
  border-bottom:1px solid var(--border);font-size:13px}
.sr:last-child{border-bottom:none}
.sn{width:130px;text-transform:capitalize;flex-shrink:0;font-size:12px}
.sb{flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.sf{height:100%;background:var(--good);border-radius:3px}
.sp{width:34px;text-align:right;color:var(--muted);font-size:12px}
.sc{width:44px;text-align:right;font-size:11px;color:var(--muted)}
.no-data{font-size:13px;color:var(--muted);text-align:center;padding:20px 0}
</style>
</head>
<body>

<header>
  <div class="logo">Curia Eval <span class="badge">dataset</span></div>
  <nav>
    <a href="#" onclick="openStats();return false">Stats</a>
    <a href="/eval/export.csv">Export CSV</a>
  </nav>
</header>

<div class="wrap">
  <div class="ingest-row">
    <input id="url-in" type="url" placeholder="https://…"
      onkeydown="if(event.key==='Enter')ingest()">
    <button class="btn-primary" id="eval-btn" onclick="ingest()">Evaluate →</button>
  </div>
  <div class="status-bar" id="sbar"><div class="spin"></div><span id="stxt">Starting…</span></div>
  <div class="err" id="err"></div>
  <div class="src-hdr" id="src-hdr">
    <h2 id="src-title"></h2>
    <div class="src-meta" id="src-meta"></div>
  </div>
  <div id="results"></div>
</div>

<div class="modal-bg" id="stats-modal" onclick="if(event.target===this)closeStats()">
  <div class="modal">
    <button class="mcl" onclick="closeStats()">×</button>
    <h3>Verdict stats</h3>
    <div id="stats-body"><div class="no-data">Loading…</div></div>
  </div>
</div>

<script>
const CARDS=[
  {k:'scrape',       label:'Scraped Content', tier:null},
  {k:'summary',      label:'Summary',         tier:1},
  {k:'metadata',     label:'Metadata',        tier:1},
  {k:'key_insights', label:'Key Insights',    tier:1},
  {k:'human_stakes', label:'Human Stakes',    tier:2},
  {k:'core_tensions',label:'Core Tensions',   tier:2},
  {k:'counterpoints',label:'Counterpoints',   tier:2},
  {k:'examples',     label:'Examples',        tier:2},
];

let srcId=null, pollT=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function setStatus(msg){
  const b=$('sbar');
  if(!msg){b.style.display='none';return;}
  b.style.display='flex';$('stxt').textContent=msg;
}
function setErr(msg){const e=$('err');e.textContent=msg||'';e.style.display=msg?'block':'none';}

async function ingest(){
  const url=$('url-in').value.trim();if(!url)return;
  $('eval-btn').disabled=true;
  $('src-hdr').style.display='none';
  $('results').style.display='none';
  $('results').innerHTML='';
  setErr('');setStatus('Submitting…');
  try{
    const r=await fetch('/eval/ingest',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    if(!r.ok)throw new Error((await r.json()).detail||'Ingest failed');
    const d=await r.json();srcId=d.source_id;
    d.status==='ready'?await render():poll();
  }catch(e){setErr(e.message);setStatus('');$('eval-btn').disabled=false;}
}

const SMSG={queued:'Queued…',scraping:'Scraping article…',
  transforming:'Running 7 transforms…',embedding:'Building embeddings…',
  ready:'Done!',failed:'Failed'};

function poll(){
  if(pollT)clearTimeout(pollT);
  pollT=setTimeout(async()=>{
    try{
      const d=await(await fetch(`/eval/sources/${srcId}`)).json();
      setStatus(SMSG[d.status]||d.status);
      if(d.status==='ready')await render(d);
      else if(d.status==='failed'){
        setErr('Ingest failed: '+(d.error||'unknown'));
        setStatus('');$('eval-btn').disabled=false;
      }else poll();
    }catch{poll();}
  },2000);
}

async function render(data){
  if(!data)data=await(await fetch(`/eval/sources/${srcId}`)).json();
  setStatus('');$('eval-btn').disabled=false;

  // source header
  $('src-title').textContent=data.title||'Untitled';
  const meta=[];
  try{
    const m=typeof data.transforms.metadata==='string'
      ?JSON.parse(data.transforms.metadata):data.transforms.metadata;
    if(m?.type)meta.push(m.type.replace(/_/g,' '));
    if(m?.category?.length)meta.push(m.category.join(', '));
    if(m?.language)meta.push(m.language);
  }catch{}
  if(data.char_count)meta.push(data.char_count.toLocaleString()+' chars');
  $('src-meta').textContent=meta.join(' · ');
  $('src-hdr').style.display='block';

  // cards
  const box=$('results');box.innerHTML='';
  for(const c of CARDS){
    let raw=c.k==='scrape'?(data.full_text||'(no content scraped)')
      :(data.transforms[c.k]??null);
    const nil=raw===null||raw===undefined
      ||(typeof raw==='string'&&raw.trim().toLowerCase()==='null');
    let disp=nil?'(null — not applicable)':raw;
    if(c.k==='metadata'&&!nil){try{disp=JSON.stringify(JSON.parse(disp),null,2);}catch{}}
    box.appendChild(mkCard(c,disp,nil));
  }
  box.style.display='block';
}

function mkCard(c,content,nil){
  const d=document.createElement('div');
  d.className='card';d.id='card-'+c.k;
  const tier=c.tier?`<span class="tier t${c.tier}">Tier ${c.tier}</span>`:'';
  d.innerHTML=`
<div class="c-head">
  <div class="c-label">${esc(c.label)}${tier}</div>
  <div class="c-acts">
    <span class="saved" id="sv-${c.k}">✓ saved</span>
    <button class="btn-g" id="g-${c.k}" onclick="vote('${c.k}','good')">Good</button>
    <button class="btn-b" id="b-${c.k}" onclick="vote('${c.k}','bad')">Bad</button>
    <button class="btn-e" id="e-${c.k}" onclick="editMode('${c.k}')">Edit</button>
  </div>
</div>
<div class="c-body">
  <pre class="ct${nil?' nil':''}" id="pre-${c.k}">${esc(content)}</pre>
  <div class="edit-area" id="ea-${c.k}">
    <label>Edit — your version</label>
    <textarea id="ta-${c.k}">${nil?'':esc(content)}</textarea>
    <div class="edit-btns">
      <button class="bsm bskip" onclick="cancelEdit('${c.k}')">Cancel</button>
      <button class="bsm bsave" onclick="saveEdit('${c.k}')">Submit edit</button>
    </div>
  </div>
  <div class="note-row">
    <textarea id="note-${c.k}" placeholder="Add a note…" rows="2"></textarea>
  </div>
</div>`;
  return d;
}

async function vote(k,v,golden=null){
  const card=$('card-'+k);
  card.className='card v-'+v;
  $('g-'+k).className='btn-g'+(v==='good'?' on':'');
  $('b-'+k).className='btn-b'+(v==='bad'?' on':'');
  $('e-'+k).className='btn-e'+(v==='edit'?' on':'');
  const s=$('sv-'+k);s.classList.add('show');
  setTimeout(()=>s.classList.remove('show'),1600);
  const note=($('note-'+k)?.value||'').trim()||null;
  await fetch(`/eval/sources/${srcId}/verdict`,{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({transform:k,verdict:v,golden,note})});
}

function editMode(k){
  $('pre-'+k).style.display='none';
  $('ea-'+k).style.display='block';
  $('ta-'+k).focus();
}
function cancelEdit(k){
  $('ea-'+k).style.display='none';
  $('pre-'+k).style.display='';
  $('e-'+k).classList.remove('on');
}
async function saveEdit(k){
  const golden=$('ta-'+k).value.trim()||null;
  // update visible pre with edited text
  if(golden)$('pre-'+k).textContent=golden;
  cancelEdit(k);
  await vote(k,'edit',golden);
}

async function openStats(){
  $('stats-modal').classList.add('open');
  $('stats-body').innerHTML='<div class="no-data">Loading…</div>';
  const d=await(await fetch('/eval/stats')).json();
  if(!d.stats.length){
    $('stats-body').innerHTML='<div class="no-data">No verdicts yet — rate some transforms first.</div>';
    return;
  }
  $('stats-body').innerHTML=d.stats.map(s=>`
<div class="sr">
  <span class="sn">${s.transform.replace(/_/g,' ')}</span>
  <div class="sb"><div class="sf" style="width:${s.pass_rate}%"></div></div>
  <span class="sp">${s.pass_rate}%</span>
  <span class="sc">${s.good}/${s.total}</span>
</div>`).join('')
  +`<p style="margin-top:12px;font-size:12px;color:var(--muted)">${d.total_verdicts} total verdicts</p>`;
}
function closeStats(){$('stats-modal').classList.remove('open');}
</script>
</body>
</html>"""
