# Daily-Brief-Harness — Deep Analysis v1

**Date:** 2026-07-22 · **Branch analyzed:** `feat/dailyBrief_v3` (2 commits, author Sourajit Chowdhury / s0radummy)
**Method:** full read of all 44 Python files + 8 design docs, cross-referenced against curia-main systems.

> **Recommendation (final call is Arihant's): Option C — staged assimilation** (§8).
> Fix the hard-rule violations now (~1 week: llm_config port, shared DB via Alembic,
> loguru, auth, tests for pure logic); defer worker-lane orchestration, host scraper
> reuse, real TTS, and the Beats-vs-IAB taxonomy decision to Phase 2 when the
> feature graduates. Same playbook as this month's Curia work: land the
> non-negotiables, let the feature earn the rest.

---

## 1. What it is

A self-contained FastAPI app that generates a personalized "Daily Brief" — a ~5-6
minute spoken-news *script* (text only; TTS is stubbed with `placeholder://` mp3
URLs) — via a two-phase cached pipeline (batch "Pre-Opt" per topic + per-user
assembly), wrapped in an unusually serious LLM-evaluation harness: deterministic
checks, five Sonnet-tier LLM judges (one with web search), a synchronous
faithfulness-check-and-regenerate loop that gates output, gold-set calibration
with Cohen's kappa, and full cost/latency logging to its own 20-table Postgres
schema. Maturity: **well-engineered prototype** — working end-to-end with real
measured data and real production scars (segfault fix, OOM-tuned concurrency),
but zero automated tests, no scheduler, no real audio, hardcoded model IDs, an
unauthenticated-by-default API, its own DB/LLM/migration layers, and the
dashboard's `index.html` missing from the committed tree.

Doc caveat: `dailybrief.md` describes the *retired* architecture (now in
`legacy/`, unwired). The current app matches `daily_brief_high_level_summary.md`,
`daily_brief_technicalities.md`, and `architecture_changes.md`.

## 2. Architecture & data flow

**News source:** Google News RSS only (hand-rolled urllib + xml.etree in
`news_service.py`). 7 fixed "Beats" (Tech/Business/World/Science&Health/Culture/
Lifestyle/Sports) → Google topic verticals; 30-hour recency cutoff; 22 hardcoded
country geo editions; local news via quoted location+topic search.

**Pipeline (two entry points):**
1. **Pre-Opt** (`POST /api/preopt/run`) — per Beat: fetch 50 → no-LLM rank/dedup
   (`scoring_service.py`: MiniLM embeddings vs scenario anchors + greedy
   clustering @0.72 + significance heuristics, keep top 50%) → one Sonnet
   "Score & Curate" call (event clustering → validity gate → ranking) →
   full-text scrape of top-4 (`enrichment_service.py`: googlenewsdecoder →
   requests → trafilatura, cluster-alternate racing) → Haiku writes one cached
   segment per article → `harness.article_segment_cache`.
2. **Generate Brief** (`POST /api/users/{id}/generate-brief`) — pool cached
   candidates + fresh custom/local fetch → same rank + curate → resolve 5
   winners (lead / 3×standard / local) against cache → generate misses through
   the **synchronous faithfulness judge+regen loop** → weather
   (WeatherAPI.com) → Haiku intro+outro → ordered text manifest with estimated
   durations (150 wpm). Same-day re-trigger regenerates bookends only.

**LLM:** direct `anthropic` SDK (`llm_service.py`) with **hardcoded**
`ACTIVE_MODEL = "claude-haiku-4-5-20251001"` (line 14) and
`JUDGE_MODEL = "claude-sonnet-5"` (line 22); per-step max_tokens; web-search
tool on the faithfulness step only; empty API key ⇒ full "Simulated Mode" mocks.

**Output:** JSON text manifest only. Real TTS/storage/push code exists only in
unwired `legacy/`.

**Service map (`app/services/`):**

| File | Role |
|---|---|
| `harness_db.py` | own asyncpg pool; all queries schema-qualify `harness.*` |
| `cache_service.py` (724 ln) | core CRUD; article dedup by lowercased normalized_url; segment cache upserts (text change nulls memoized faithfulness verdict); brief/transcript CRUD |
| `news_service.py` | Google News RSS fetch; blocking (callers thread it) |
| `scoring_service.py` (605) | no-LLM pre-filter: MiniLM topic similarity + clustering + heuristics; composite score; boot-time model warmup |
| `enrichment_service.py` | redirect-decode + scrape + trafilatura; same-story fallback racing; libxml2 segfault warmup; semaphore 3 |
| `pipeline.py` | PipelineManager step wrappers (rank / score-curate / fetch / segment / bookends) |
| `preopt_runner.py` | per-topic batch orchestration with eval-run logging |
| `user_brief_runner.py` (540) | the main orchestrator + bookends-only regen path; triggers automated judging |
| `weather_service.py` | WeatherAPI current conditions; degrades to ("","") |
| `llm_service.py` (403) | single LLM chokepoint `call_llm(step_id=…)`; model/token routing; mock fallback; JSON parsing |
| `eval_logging_service.py` | write-only run instrumentation (articles, rankings, transcripts, judge outputs, results) |
| `eval_checks_service.py` | deterministic checks: TTS format, error leaks, word budgets, structure, duration band, personalization, cost |
| `eval_judges_service.py` (955) | 5 LLM judges + diversity; manual gold orchestration + kind-gated automated subset; Cohen's kappa |
| `faithfulness_regen_service.py` | generate→judge→regen (≤2 retries; least-severe wins); memoized verdicts for cache hits |
| `automated_judging.py` | fire-and-forget post-brief judging trigger with GC-safe task registry |
| `settings_service.py` | two persisted toggles |
| `eval_gold_service.py` | gold-run freeze / labels / golden scripts (append-only supersede) |

## 3. The eval harness (the crown jewel)

Four layers:
1. **Deterministic checks** after every run — zero-cost, never blocking.
2. **Automated LLM judges** post-brief, fire-and-forget: relevance (binary per
   pooled article), order-correctness + order-ranking ("senior wire editor"),
   bookend faithfulness. Observational; per-call cost/latency logged.
3. **Inline faithfulness gate** — the one judge that changes output: every fresh
   segment fact-checked by Sonnet **with web search against the real world**;
   4-way severity; critical/moderate → targeted regen with flagged claims
   spliced into the prompt, ≤2 retries, least-severe wins. Verified live: a
   planted fake statistic was caught and cleanly rewritten.
4. **Gold-set calibration** (manual): frozen runs, human labels (append-only),
   per-profile Cohen's kappa (pooled kappa deliberately removed), tone/flow via
   position-swapped pairwise with forced "inconclusive" on flip.

**Methodology: high for a prototype.** Strengths: judge/generator on different
model tiers, no label circularity, careful cost attribution, "unverifiable ≠
rewrite". Weaknesses: tiny gold set (3 runs); the *gating* faithfulness judge is
itself unvalidated; observed **60.9% of segments flagged moderate/critical** —
generator hallucinating vs judge over-flagging is unresolved; and the judge
costs ~42.7k input tokens / **~54s average latency per call, synchronously
inside the HTTP request path**.

## 4. Infra expectations

- **Env:** `ANTHROPIC_API_KEY` (empty ⇒ simulated; placeholder ⇒ 401s),
  `WEATHERAPI_KEY`, `DATABASE_URL` (default local :5433 `curia_harness`),
  `BASIC_AUTH_USER/PASS` (optional), `ENABLE_ARTICLE_ENRICHMENT` (undocumented
  in .env.example), legacy Google-Calendar vars.
- **DB:** own Postgres, **`harness` schema**, 20 tables + 6 enums via 16 raw SQL
  files + `bootstrap_db.py` — no Alembic. Key table:
  `article_segment_cache` (UNIQUE(article_id, segment_type, is_local) +
  memoized faithfulness columns).
- **Deps:** fastapi, anthropic, asyncpg, **sentence-transformers + torch (CPU
  wheel)**, sklearn, trafilatura, googlenewsdecoder.
- **Posture:** assumes it *is* the app (own Dockerfile :8080, Railway-tuned).

## 5. Prompts

Above-average, battle-scarred prompt engineering; file headers document the
live failure each rule fixes (the SK-Hynix 7× dup, the Morningstar-digest
rank-1 incident). Score & Curate forces structured event-clustering before
ranking; segment prompts enforce a self-contained rule (required by cross-user
caching), banned anchor-phrase list, TTS format rules, hard word ranges
(routinely blown and only logged); regen addendum framed "fix the correction,
don't rewrite". Weak spots: typo'd lead-prompt header, budgets not enforced,
intro few-shots contain tone typos.

## 6. Code-quality critique (worst first)

1. **Zero tests** — 8,800 lines verified only by manual live runs (host suite: 889).
2. **Hardcoded model IDs/provider** (`llm_service.py:14,22,344`; `main.py:120`) —
   violates the host's config rule directly.
3. **Security:** CORS `allow_origins=["*"]` **with** `allow_credentials=True`
   (`main.py:46-52`, invalid combo); no auth unless Basic Auth set; open
   endpoints spend real money; callers may supply their own
   `X-Anthropic-API-Key`; new client per call (`llm_service.py:211`).
4. **Silent-mock fallback** (`llm_service.py:216-259`): empty key ⇒ realistic
   fake data — harness feature, product-integration hazard.
5. **Race:** two concurrent generate-brief calls for the same (user, date) both
   run the full pipeline (`user_brief_runner.py:182-183` only short-circuits on
   `ready`); no stale `generating` cleanup.
6. **Structural latency flaw:** ~54s×5×≤3 synchronous judge calls inside one
   HTTP request — needs the host's worker/queue model, as their own docs admit.
7. SQL f-string interpolation of judge/column names
   (`eval_judges_service.py:~907`, `eval_gold_service.py:276`); interval via
   string concat (`eval_judges_service.py:829`).
8. Unbounded eval-table growth; articles never pruned; URL normalization
   lowercases paths (cache conflation) and doesn't resolve Google redirect
   tokens.
9. Broken bits: `static/index.html` missing (dashboard dead in this copy);
   legacy `tts_service.py` NameError (why audio never shipped); stdlib logging
   not loguru; ~30× copy-pasted try/except-500 route boilerplate; deprecated
   `@app.on_event`.
10. **Constitution drift:** the user's saved links influence nothing; the
    anti-repeat rule ("never re-tell the same story") is unimplemented and
    structurally at odds with the shared segment cache.

## 7. Overlap with curia-main

| Host system | Verdict |
|---|---|
| `core/llm_config` + models.yaml | **Duplicated & violated** — but all 10 call sites funnel through one `call_llm(step_id)`; porting to task bindings is a one-file rewrite. Frictions: raw tool-use for the web-search judge (DSPy escape hatch needed), per-request key override (drop), mock mode (port to test fixture or delete). |
| `core/db/connection` + Alembic | **Second pool, but zero schema collision** (everything `harness.*`). Path: same RDS, squash 16 SQL files → one Alembic migration, delete `harness_db.py`. ~1-2 days. Open question: 20 eval tables in prod RDS? |
| `core/scraper` | Partial duplicate (trafilatura step). **Keep** the genuinely-new Google-News redirect decoding + cluster-alternate racing as a pre-step; delegate scraping to the host cascade (gains jina/firecrawl fallbacks). |
| `core/taxonomy` / `core/tension` | **Conceptual conflict:** 7 Beats vs 32 IAB buckets — two topic vocabularies in one product; a conscious team decision needed. Also MiniLM/torch vs the host's embedding stack. No tension overlap. |
| `studio/` + `worker/` | Parallel, non-overlapping pipelines — but the harness *needs* worker lanes (fixes flaw #6) and the host's TTS/BGM (fixes "no audio"). Natural shape: `worker/handlers/generate_brief.py`. |
| `api/` (Firebase auth) | Duplicate shell; routes port as a router behind host auth. |
| loguru / pytest / CHANGELOG | Straight conflicts: find-replace / write-from-scratch (eval services are very testable) / doc discipline maps fine. |
| `optimization/` | **Complementary** — the harness's calibration methodology (gold sets, kappa, position-swap, cost logging) is more mature than the host's and should back-propagate into episode judging. |

## 8. Integration options

**A — Sidecar as-is (~2-4 days):** separate service+DB; add auth, fix CORS,
restore index.html, env-var the model IDs. *Fast, zero host risk — but
permanently violates every host convention, two deploys/DBs, no path to audio,
guaranteed drift.*

**B — Full assimilation (~3-5 weeks):** move to `brief/` in the monorepo;
llm_config bindings (+raw tool-use path); shared DB via Alembic 0035; host
scraper cascade behind the gnews-decode layer; Pre-Opt/Generate as worker
handlers (fixes the sync-latency flaw); wire manifests into host TTS/BGM for
real audio; loguru; pytest suite; routes under host auth. *One truth, unlocks
audio + scheduling, eval machinery becomes backend-wide — but biggest
effort/risk and freezes feature iteration; needs taxonomy + embeddings + DSPy
decisions first.*

**C — Staged (recommended): Phase 1 ~1 week — the non-negotiables:** monorepo
`brief/`, llm_config port, Alembic-squashed shared DB (`harness` schema), drop
second pool, loguru, auth, tests for pure logic (scoring/checks/regen). Keep own
routes + sync orchestration temporarily. **Phase 2 when the feature graduates:**
worker handlers, host scraper, real TTS, taxonomy decision. *Fixes every hard
rule violation cheaply; the seams (`call_llm`, runners) stay clean for Phase 2;
risk: half-integrated states calcify — Phase 2 needs a committed trigger
(e.g. "first real user cohort").*

**Cross-cutting day-one items under any option:** missing `index.html`, the
CORS config, the zero-test situation, the (user,date) race, and deciding the
60.9%-flagged mystery (validate the faithfulness judge before trusting its
gate).
