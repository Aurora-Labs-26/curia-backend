# Curia Daily Brief — Pipeline & Test Harness

This is the FastAPI backend + dashboard for the **Curia Daily Brief** spoken audio news briefing feature, built around the **two-phase Pre-Opt + Per-User Brief Cache pipeline** — the entire Daily Brief feature. There is no other pipeline in this app.

## How it works

1. **Pre-Opt** (`POST /api/preopt/run/{topic_id}`): for each of the 7 system topics ("Beats"), fetches ~50 articles, ranks them locally (no LLM), scores/curates the top 4 with a single Claude call, fetches full article text for those 4, then writes and caches a spoken segment transcript for each — reused by every user who picked that Beat, instead of being regenerated per user.
2. **Generate Brief** (`POST /api/users/{user_id}/generate-brief`): builds one user's article pool (reusing Pre-Opt's cached candidates for their chosen Beats, plus a fresh fetch for their custom topics and local news), ranks + curates the winning 5 articles, resolves each against the segment cache (generating only what's missing), writes a fresh personalized intro/outro, and assembles the ordered manifest.

The dashboard has two tabs:
- **Multi-User Cache** — trigger Pre-Opt per topic, add/manage simulated users, trigger Generate Brief, inspect the article cache.
- **Open Coding** — browse every generated transcript, read the full script, and leave free-text review notes (this is what the harness is deployed for — letting teammates review generated briefs and comment).

**Claude Simulation Mode**: if no Anthropic API key is configured, every LLM call falls back to a realistic mocked response, so the full pipeline is explorable without a key.

A `legacy/` folder holds real TTS/storage/push/calendar integration code from an earlier, now-retired production pipeline — not wired into the live app, kept as reference for when this pipeline gets real production scheduling. See `legacy/README.md`.

---

## Setup & Running

### 1. Install Dependencies
Requires Python 3.11+.
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file from the example:
```bash
cp .env.example .env
```
Fill in `ANTHROPIC_API_KEY` to enable live LLM generation (leave empty to run in simulated mode), and `DATABASE_URL` for the harness's Postgres cache schema (see `docker-compose.yml` + `scripts/bootstrap_db.py` for local setup).

### 3. Launch the Server
```bash
uvicorn app.main:app --reload
```

Open your browser to:
```
http://127.0.0.1:8000/
```
