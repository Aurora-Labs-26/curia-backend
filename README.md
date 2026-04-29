# Curia

A pipeline that turns a personal reading archive into original podcast episodes — automatically.

You save articles. Curia reads them, finds thematic tensions between them, generates an episode outline and script, synthesizes audio in a cloned voice, and exports a finished MP3.

---

## What it does

1. **Ingest** — scrape a URL, extract full text, run 5 Claude transformations (key insights, human stakes, core tensions, counterpoints, examples), embed chunks via Ollama
2. **Cluster** — embed primitive signals (`core_tensions` + `counterpoints`) per source, find cliques of articles with cosine similarity ≥ 0.70, articles can belong to multiple clusters
3. **Ideate** — for each cluster (and standalone sources), generate a show idea: angle, format recommendation, and thematic justification
4. **Generate** — selector picks the best sources for an episode, briefing packet assembled, outline generated (Haiku), transcript generated (Sonnet)
5. **Synthesize** — each transcript line is synthesized via XTTS v2 using a reference WAV, clips stitched with pydub, intro prepended, final MP3 exported

---

## Architecture

```
curia/
├── core/
│   ├── ingest.py              # URL → scrape → transform → embed → store
│   ├── embeddings.py          # Ollama nomic-embed-text wrapper
│   └── db/
│       ├── connection.py      # SurrealDB async client
│       └── migrations.py      # Schema definitions
│
├── intelligence/
│   ├── idea_generator.py      # LangGraph workflow: archive → clusters → ideas
│   └── selector.py            # Picks best sources for a given episode
│
├── studio/
│   ├── generator.py           # Full pipeline: selector → briefing → outline → transcript → audio → save
│   ├── briefing_builder.py    # Assembles briefing packet from sources + format config
│   ├── formats.py             # Episode format configs (narrative_drift, clarity_engine, etc.)
│   └── shows/
│       ├── profiles.py        # EpisodeProfile + SpeakerProfile per show
│       └── prompts.py         # Outline and transcript system prompts
│
└── scripts/
    ├── ingest_urls.py          # Batch ingest a list of URLs
    ├── embed_primitives.py     # Backfill source_primitive_embedding for clustering
    ├── run_idea_generator.py   # Run idea generation workflow
    ├── run_show.py             # Generate a full episode for a show profile
    ├── run_show_idea.py        # Generate an episode from a specific show_idea record
    ├── synthesize_intro.py     # Synthesize the fixed show intro WAV
    └── synthesize_episode.py   # Synthesize a full episode from a generation_run record
```

---

## Data model (SurrealDB)

- **`source`** — ingested article: `title`, `full_text`, `key_insights`, `human_stakes`, `core_tensions`, `counterpoints`, `examples`
- **`source_chunk`** — chunked text segments for vector search
- **`source_embedding`** — full-text chunk embeddings (768-dim, nomic-embed-text)
- **`source_primitive_embedding`** — single embedding per source from `core_tensions` + `counterpoints` only — used for clustering
- **`show_idea`** — generated idea: `angle`, `format`, `idea_type` (cluster/standalone), `source_ids`
- **`generation_run`** — episode generation record: `episode_title`, `transcript` (JSON), `outline`
- **`episode`** — saved episode: `title`, `transcript`, `audio_path`, `source_ids`, `show_name`
- **`covered_topic`** — tracks what themes have been covered to avoid repetition

---

## Episode formats

Four formats, each with its own pacing, energy curve, and structural rules:

- **`narrative_drift`** — slow, open-ended, intimate. No resolution. Leaves the listener sitting with something.
- **`clarity_engine`** — structured, rising energy. Opens with a question, closes with an explicit takeaway.
- **`exploration_engine`** — analytical, exploratory. States a claim, steelmans the counterpoint, ends with a reframe.
- **`momentum_loop`** — fast, punchy, re-hooks every 60-90 seconds with micro-payoffs.

---

## Speakers

Each speaker has a reference WAV (for XTTS voice cloning), a backstory, and explicit speech pattern rules passed to the transcript LLM.

- **Kenji** — former wire journalist. Tight sentences, one idea per line, occasional soft undercut.
- **Arjun** — economist turned essayist. Step-by-step arguments, precise language, ends on open questions.
- **Emeka** — systems thinker. Moves fast, connects domains without explaining the connection first.

Currently Kenji is active. Arjun and Emeka require reference WAVs.

---

## Setup

### Dependencies

```bash
# Python 3.13 (main)
pip install anthropic surrealdb langgraph loguru python-dotenv content-core

# Python 3.11 (XTTS env — separate venv)
# XTTS requires Python ≤ 3.11 due to audioop dependency
python3.11 -m venv xtts_env
source xtts_env/bin/activate
pip install TTS pydub
```

### Services

- **SurrealDB** — `surreal start --log trace --user root --pass root file:data/studio.db`
- **Ollama** — `ollama serve` with `nomic-embed-text` pulled

### Config

Copy `.env.example` to `.env` and fill in your Anthropic API key.

### Run migrations

```bash
python scripts/setup_db.py
```

---

## Running the pipeline

### 1. Ingest articles

```bash
python scripts/ingest_urls.py
```

Edit the URL list in `scripts/ingest_urls.py` before running.

### 2. Embed primitives (for clustering)

```bash
python scripts/embed_primitives.py
```

### 3. Generate show ideas

```bash
python scripts/run_idea_generator.py
```

### 4. Synthesize intro

```bash
# Uses xtts_env Python
python scripts/synthesize_intro.py
```

Output: `studio/intro.wav`

### 5. Generate a full episode

```bash
# From a show profile
python scripts/run_show.py

# From a specific show_idea record
python scripts/run_show_idea.py <show_idea_id>
```

### 6. Synthesize episode audio

```bash
python scripts/synthesize_episode.py <generation_run_id>
```

Output: `data/episodes/<title>.mp3`

---

## Voice cloning

XTTS v2 is run via subprocess using the `xtts_env` Python 3.11 interpreter. The main process (Python 3.13) cannot import XTTS directly — pydub's `audioop` dependency was removed in 3.13.

Reference WAV path is set per speaker in `studio/shows/profiles.py`. A 10-30 second clean recording is enough for XTTS to clone the voice.

---

## Clustering notes

- Primitive embeddings (`core_tensions` + `counterpoints`) give genuine discriminating range (~0.47-0.77 cosine) vs full-text embeddings which cluster near 0.80+ floor
- Similarity threshold: **0.70**
- Clique-based — an article can appear in multiple clusters if it's thematically relevant to each
- Max cluster size: **5**
