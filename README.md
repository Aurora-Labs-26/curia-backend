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

---

## Deep architecture: how an episode is built

### Stage 1 — Ingest (`core/ingest.py`)

Every URL goes through a five-step pipeline:

1. **Scrape** — `content_core.extract_content()` fetches and parses the page into plain text + title
2. **Store** — creates a `source` record in SurrealDB with `full_text`, `title`, `user_id`, `pool`
3. **Transform** — five Claude calls run in parallel, each with a focused extraction prompt (see below)
4. **Chunk + embed** — full text is split into 300-word chunks with 50-word overlap, each chunk embedded via `nomic-embed-text` through Ollama (768-dim vectors) and stored in `source_embedding`
5. **Primitive embed** — `core_tensions` + `counterpoints` text is concatenated and embedded as a single vector into `source_primitive_embedding` — used only for clustering

### Stage 2 — Transformations (`core/ingest.py → TRANSFORMATIONS`)

Five extractions per source. All are extracted at ingest time and stored as `source_insight` records.

---

**`key_insights`** (Tier 1 — always present)

> Extract the 3-4 most important insights, ideas, or claims. Specific and concrete — include actual numbers, names, claims, or mechanisms if present.

Used in: outline hook segment, early briefing material

---

**`human_stakes`** (Tier 2 — null if not applicable)

> What is actually at stake for real people — the concrete human consequence of the idea being true or false. Name the actual people or group affected. State what specifically changes or is lost.

Used in: grounding abstract ideas, mid-episode weight

---

**`core_tensions`** (Tier 2 — null if not applicable)

> The central tension or contradiction AND the most important question it leaves unresolved. Format: "[Force A] vs [Force B]" + one sentence explanation + one unresolved question.

Used in: structural turn of the episode, cluster similarity signal

---

**`counterpoints`** (Tier 2 — null if not applicable)

> The strongest counterpoint to the article's main claim — steelmanned as strongly as possible, whether the article raises it or not.

Used in: exploration_engine format's counterpoint beat, cluster similarity signal

---

**`examples`** (Tier 2 — null if not applicable)

> Either the single most concrete specific example OR the most useful mental model the piece introduces — whichever is stronger.

Used in: anchoring abstract ideas in the outline, early hook material

---

### Stage 3 — Clustering (`intelligence/idea_generator.py`)

Runs after all sources are ingested and primitive embeddings exist.

```
load_archive → cluster_sources → evaluate_ideas → filter_covered → save_ideas
```

**Clique algorithm:**
1. Fetch one primitive embedding per source (from `source_primitive_embedding`)
2. Compute all pairwise cosine similarity scores
3. Any pair scoring ≥ 0.70 is a candidate edge
4. Expand each pair into the largest clique possible (all pairwise scores within the group must all be ≥ 0.70)
5. Deduplicate — remove any clique that is a strict subset of a larger clique
6. Articles can appear in multiple cliques if they're genuinely similar to multiple groups

Why primitive embeddings over full-text: full-text Ollama embeddings produce a similarity floor of ~0.80+ across all articles (text length and style swamp the thematic signal). Embedding only `core_tensions` + `counterpoints` — the extracted discriminating signal — gives a genuine range of ~0.47-0.77.

### Stage 4 — Idea generation (`intelligence/idea_generator.py → evaluate_ideas`)

For each cluster (and standalone source), a Claude call generates:
- **`angle`** — the specific editorial lens for this episode: what tension to follow, what the episode argues or explores
- **`format`** — recommended episode format (`narrative_drift`, `clarity_engine`, `exploration_engine`, `momentum_loop`)
- **`idea_type`** — `cluster` or `standalone`
- **`source_ids`** — which articles feed this episode

Previously covered topics are filtered before saving to avoid repetition.

### Stage 5 — Briefing packet (`studio/briefing_builder.py`)

Deterministic — no LLM. Assembles everything the outline LLM needs into a single JSON structure:

```json
{
  "format": "exploration_engine",
  "format_config": {
    "pacing": "medium",
    "resolution_style": "partial",
    "energy_curve": "steady_with_spikes",
    "structure_pattern": ["claim", "counterpoint", "expansion", "link", "reframe"],
    "voice_style": "analytical, exploratory",
    "rules": {
      "must_do": ["state a strong claim early, then complicate it", "..."],
      "must_avoid": ["premature closure or tidy conclusions", "..."]
    }
  },
  "episode_constraints": {
    "target_length_minutes": 12,
    "segment_count": 8
  },
  "editorial_direction": "the angle from the show_idea record",
  "source_primitives": [
    {
      "title": "Article title",
      "key_insights": "...",
      "human_stakes": "...",
      "core_tensions": "...",
      "counterpoints": "...",
      "examples": "..."
    }
  ]
}
```

The format config is fully encoded in the packet — the outline and transcript prompts are format-agnostic and read format behaviour entirely from this JSON.

### Stage 6 — Outline (`studio/generator.py → generate_outline`)

**Model:** `claude-haiku-4-5-20251001`

The outline LLM receives the briefing packet JSON and produces a structured allocation — not prose. It decides which primitives go in which segments, how the arc builds, and what the structural turn is.

Output schema:
```json
{
  "title": "episode title",
  "thread": "one sentence — the single idea this episode follows",
  "segments": [
    {
      "segment": 1,
      "purpose": "what this segment does in the arc",
      "primitives_used": ["key_insights from Article A", "examples from Article B"],
      "transition": "one phrase — how this leads to the next segment"
    }
  ]
}
```

Key constraints baked into the outline prompt:
- Each primitive appears in at most one segment
- Never transition between sources explicitly — no source boundaries should be audible
- `resolution_style` governs how the final segment ends
- `structure_pattern` maps directly to segment purposes in order

### Stage 7 — Transcript (`studio/generator.py → generate_transcript`)

**Model:** `claude-sonnet-4-6`

The transcript LLM receives the briefing packet + outline + speaker definition and converts the structural allocation into spoken lines.

Speaker definition injected into the human message:
```
Name: Kenji
Backstory: Former wire journalist who reported from three continents...
Speech patterns:
  Keeps sentences tight.
  Prefers one idea per line.
  Occasionally undercuts a statement with a softer follow-up.
  Uses contrast sparingly but sharply.
  Avoids over-explaining.
  Sometimes restates an idea in simpler terms after saying it once.
```

Priority order for the transcript LLM:
1. Follow outline structure and format_config rules
2. Keep meaning clear and easy to follow on first listen
3. Apply speaker speech patterns last, subtly

Output — one JSON object per spoken unit (usually 1-2 sentences):
```json
[
  {"speaker": "Kenji", "text": "..."},
  {"speaker": "Kenji", "text": "..."}
]
```

### Stage 8 — Synthesis (`scripts/synthesize_episode.py`)

1. Prepend `studio/intro.wav` (fixed, synthesized once via `synthesize_intro.py`)
2. For each transcript line, call XTTS v2 via subprocess (xtts_env Python 3.11) with the speaker's reference WAV — produces a `.wav` clip per line
3. Stitch all clips with 500ms silence between lines using pydub
4. Export final MP3 at 128k bitrate to `data/episodes/<title>.mp3`

The intro text (synthesized once, reused every episode):
> Hello. Welcome in. Thanks for listening. But before we begin, let's first take a second to settle. Right. We'll keep this quiet at first. And then we'll move into it slowly.
