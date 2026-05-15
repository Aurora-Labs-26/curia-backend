# Curia v2 — Backlog

Last updated: 2026-05-16

---

## Critical

- [x] **BUG-05** — edge_tts fails under burst load → switched to Smallest.ai Lightning TTS

---

## High

- [ ] **BUG-03** — `length_minutes` validation (ge=3, le=30) not enforced by FastAPI
- [ ] **AUDIO** — add `GET /episodes/{id}/audio` endpoint to serve generated MP3
- [ ] **AUDIO** — include `audio_url` in `GET /episodes/{id}` response once status is `complete`

---

## Medium

- [ ] **BUG-01** — failed URL scrape marks source as `ready` instead of `failed`
- [ ] **RE-ROLL** — re-roll can ship a worse transcript than the first attempt
- [ ] **QUALITY** — transcript prompt never told the rules the judge scores it on; judge always 0.0
- [ ] **INGEST** — decide what happens when a new bookmark is added: auto-trigger idea regeneration, add to pool as "new", or re-cluster everything?
- [ ] **INGEST** — fix duplicate ideas in `save_ideas` — deduplicate before writing
- [ ] **INGEST** — re-embed 3 missing sources (Dostoevsky, intimacy article, Putting Ideas into Words) — run embed manually or fix background task lifecycle in `ingest.py`

---

## Low

- [ ] **BUG-02** — 3/7 insight fields null on valid long-form sources
- [ ] **SPEAKER** — unknown speaker name accepted at API layer, only fails in worker
- [ ] **RENAME** — rename `show_name` parameter to `profile_name` in `generator.py` `generate_outline` and `generate_transcript`
- [ ] **COMPARE** — update compare UI to reflect new pipeline — show `format`, `speaker`, briefing as formatted JSON not prose

---

## Pipeline

- [ ] **OPTIMIZATION** — GEPA loop never run; needs completed episodes as examples first
- [ ] **VOYAGE** — no embeddings key means no cross-source clusters, core differentiator untested
- [ ] **CLUSTERING** — cosine similarity on full-text embeddings unreliable (Voyage-3 scores 0.80+ on unrelated articles); consider LLM-driven clustering or embedding on `core_tensions`/`counterpoints` only

---

## UX / Frontend

- [ ] Surface idea list with angle + recommended format prominently
- [ ] Let user pick an idea, confirm or change format, trigger generation
- [ ] Show generation progress (briefing → outline → transcript → audio)
- [ ] Playback UI for generated episodes (Expo AV, real audio)
- [ ] Add intro/outro music per show/format; wire into `synthesize_and_stitch`

---

## Audio

- [ ] Add intro music per show/format
- [ ] Add outro music per show/format
- [ ] Add optional mid-episode break music
- [ ] Wire music segments into `synthesize_and_stitch`

---

## Test plan fixes

- [ ] Fix `PUT /me/kb` example — wrong wrapper in TEST_PLAN.md
- [ ] Fix `reading_volume_per_week` type in TEST_PLAN.md (str not int)
- [ ] Fix TC-2.2 polling script — breaks on control chars in JSON
- [ ] Add fresh test user to Setup for TC-11.1
- [ ] Add poll-until-done to TC-11.6 dedup check

---

## Decisions needed

- [ ] TTS provider long-term — Smallest.ai working now, decide if ElevenLabs or local model needed later
- [ ] Audio storage — EC2 local disk for now, migrate to Cloudflare R2 or S3 when scaling
- [ ] Bookmark ingest trigger — auto vs manual idea regeneration
