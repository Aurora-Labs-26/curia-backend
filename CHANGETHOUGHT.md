# CHANGETHOUGHT

> Companion to `CHANGELOG.md`. The changelog records **what changed, per file**.
> This file records **why and how we're thinking** — the live design rationale,
> the AWS migration plan, the frontend↔backend contract, doc-drift corrections,
> and the running decision log for the v2.7 backend / v2.8 frontend cycle.
>
> **How we maintain this:** before a non-trivial change, jot the intent + options
> under a dated heading here. After it lands, log the concrete file edits in
> `CHANGELOG.md`. Two docs, two jobs: this one is the brain, that one is the ledger.
>
> Seeded 2026-06-07 by Arihant + Claude (claude-opus-4-8) after a full read of both
> repos. Everything below is **code-grounded**, not copied from the (stale) README —
> see "Doc-drift corrections" for where the existing docs are wrong.

---

## 0. Repo topology (the thing the READMEs don't say)

There are **two repos**, two branches, two product codenames for one product ("Curia").

| Repo | Path (local) | Branch | What it is |
|---|---|---|---|
| **Backend** | `/Users/arihantbarjatya/Downloads/curia-main` | `v2.7-final` | FastAPI API + async worker + Postgres/pgvector. The thing we're migrating to AWS. |
| **Frontend** | `/Users/arihantbarjatya/Downloads/curia-frontend` | `v2.8` | Expo / React Native **mobile app** (SDK 54). Internally codenamed **"Memento"**; product name is Curia. Cloned from `github.com/Aurora-Labs-26/curia-frontend`. |

The frontend is **not** in this tree. It is a separate git repo. The backend `README.md`
calls Curia "a backend service" and never mentions the mobile app — that's the single
biggest gap between the docs and reality.

**The seam between them is one line:** `eas.json` in the frontend sets
`EXPO_PUBLIC_API_URL = https://curia-backend-production.up.railway.app`. The app reads it
in `services/api.ts` (`BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000"`).
**The AWS migration's user-visible cutover is changing that URL.**

---

## 1. Backend — what it actually is (code-grounded)

**Stack:** FastAPI + asyncpg · async worker polling a Postgres `jobs` table
(`FOR UPDATE SKIP LOCKED`) · DSPy for LLM calls · LangGraph for idea generation ·
PostgreSQL + pgvector · Alembic migrations · pydub + ffmpeg for audio stitching.

**Two run modes from one Docker image** (`Dockerfile`):
- API: `uvicorn api.main:app --host 0.0.0.0 --port 8000`
- Worker: `python -m worker.main`

**The pipeline** (article → podcast):
1. **Ingest** (`core/ingest.py`, `worker/handlers/ingest.py`) — scrape URL, extract full text,
   run 7 Claude transformations, embed chunks (Voyage), store in Postgres + pgvector.
2. **Cluster + Ideate** (`intelligence/idea_generator.py`, LangGraph) — cosine-similarity
   cliques over `core_tensions + counterpoints` (with a fallback to all insights for
   technical content that lacks those primitives), then per-cluster show ideas; KB-aware.
3. **Generate** (`studio/generator.py`) — selector (`intelligence/selector.py`) picks sources,
   `studio/briefing_builder.py` builds a briefing packet, Haiku writes an outline,
   Sonnet writes the transcript shaped by user tone/length prefs.
4. **Judge** (`optimization/rubrics/`) — LLM-as-judge against a rubric rendered from
   `(company guidelines + user KB)`; re-rolls the transcript once if below
   `CURIA_QUALITY_THRESHOLD` (default 0.6).
5. **Synthesize** (`core/audio/`) — merge same-speaker lines → split into TTS segments →
   render via TTS adapter → stitch with gaps → optional intro/outro/music → export MP3.
   Also supports real-time streaming over WebSocket (`api/routes/stream.py`,
   `core/audio/stream_manager.py`).
6. **Optimize** (`optimization/runner/`) — GEPA against the rubric metric (QA-gated).

**Config layers:** `.env` (secrets) → `config/models.yaml` (every provider/model/voice
binding, Pydantic-validated on boot) → DB-backed guidelines + Jinja rubric templates.
Rule from `CLAUDE.md`: **no hardcoded model IDs / voice IDs / keys** — always go through config.

**Auth:** Firebase (Google sign-in) with a legacy bearer-token fallback (`api/auth.py`,
`core/firebase.py`). Two roles gated on `users.role`: `user` and `qa`. `/admin/*` is QA-only.

**Migrations:** Alembic `0001 → 0024` (note: there are two `0016_*` files —
`0016_job_priority` and `0016_episode_playback_progress` — a duplicate revision-number
that's worth verifying doesn't break `alembic history` linearity before we touch schema).

---

## 2. Doc-drift corrections (README/CLAUDE.md/BACKEND_PLAN.md vs. code)

The user explicitly flagged: **"the readme is wrong."** Confirmed. Treat existing prose docs
as stale; trust the code. Concrete mismatches found so far:

| Doc claims | Reality (in code) | Source of truth |
|---|---|---|
| edge-tts is the active TTS provider; ElevenLabs default | **Hume Octave** is bound to all speakers (`kenji→Ito`, etc.) | `config/models.yaml` + commits `bdf4b42`, `d576f2d` |
| Curia is "a backend service" (no client) | A full **Expo mobile app** consumes the API | `curia-frontend` repo |
| Storage: local fs; "S3 is future, ~40 lines away" | S3 backend **already implemented** (upload + presigned URLs) via `CURIA_STORAGE_BACKEND=s3` | `core/storage/blob.py` |
| Migrations `0001 → 0010` (README repo-layout) | Migrations go to **`0024`** | `alembic/versions/` |
| Valid speakers `kenji, arjun, emeka` | Verify against current `studio/shows/profiles.py` before relying on it | `studio/shows/profiles.py` |

**Action item:** once the AWS work settles, do a docs-reconciliation pass to bring
`README.md` / `CLAUDE.md` / `BACKEND_PLAN.md` in line with the code (the user asked for this).

---

## 3. AWS migration — plan & current readiness

We are migrating **from Railway** (`curia-backend-production.up.railway.app`) **to AWS**.
`BACKEND_PLAN.md` already contains a target map; the good news is the codebase was written
"AWS-shaped." Here's the honest readiness state:

| Concern | Target (AWS) | Current state | Work needed |
|---|---|---|---|
| API | App Runner (or ECS Fargate) from one image | Same Docker image, runs on Railway | Infra-only (provision + push to ECR) |
| Worker | ECS Fargate service, same image, diff command | Same image, `python -m worker.main` | Infra-only |
| DB | RDS Postgres + pgvector | Postgres (Railway/compose) | Provision RDS w/ pgvector ext; set `DATABASE_URL`; run `alembic upgrade head` |
| Blob storage | S3 + presigned URLs | **Code already supports S3** (`core/storage/blob.py`) | Provision bucket; set `CURIA_STORAGE_BACKEND=s3` + `CURIA_S3_*`; **verify all audio writes go through `blob.py`** (not raw `./data/audio`) |
| Queue | SQS | **Postgres `jobs` table only** — SQS-shaped but no boto3 impl yet | Either keep Postgres queue on RDS (cheapest, zero code) **or** write the ~60-line SQS impl behind `core/queue.py`'s interface |
| Secrets | Secrets Manager → ECS task secrets | `.env` at repo root, loaded via `load_dotenv` | Infra-side; app reads `os.getenv`, so no code change |
| Scheduler | EventBridge → Lambda → SQS | None | Out of scope for first cut (BACKEND_PLAN says skip; ~30-line Lambda later) |
| Push notifications | (unchanged) Firebase Cloud Messaging | `fcm_token` stored per user; `POST /me/fcm-token` | No AWS change; verify FCM creds reachable from new env |

**Key insight:** the cheapest correct first migration is **lift-and-shift** —
App Runner + RDS + S3, **keep the Postgres jobs queue** (don't introduce SQS yet). That's
near-zero app-code change. SQS becomes a later optimization, not a blocker.

**Open AWS decisions** (need answers before we provision — see §6):
- App Runner vs. ECS Fargate for the API.
- Keep Postgres queue vs. move to SQS now.
- Region.
- Terraform/IaC vs. console-clickops for the first cut.
- How secrets cross over (Secrets Manager vs. plain ECS env vars to start).

---

## 4. Frontend — what it actually is (code-grounded)

**Stack:** Expo SDK 54 (New Architecture) · Expo Router (file-based) · React 19 / React Native ·
NativeWind (Tailwind) · Firebase (auth/messaging/crashlytics) · Google Sign-In ·
PostHog analytics · `expo-av` for playback · `expo-share-extension` (iOS/Android share sheet
to ingest articles) · EAS builds.

**Screens** (`app/`, Expo Router): `auth/`, `(tabs)/` (home + `studio`), `now-playing/[id]`,
`remix/[id]`, `onboarding/`, `share/`, `profile/` (+ `listening-history`, `how-it-works`),
`preview/sign-in`.

**API client:** `services/api.ts` (468 lines). Endpoints the app actually calls:
- `GET /sources`, `POST /sources`, `DELETE /sources/{id}`, `POST /sources/clear-failed`,
  `GET /sources/{id}/episodes`
- `GET /episodes`, `GET /episodes/{id}`, `POST /episodes`,
  `POST /episodes/{id}/feedback`, `POST /episodes/{id}/progress`
- `GET /auth/me`
- `POST /me/fcm-token`

Frontend has its **own** change-log convention: `log.md` (mandated by its `CLAUDE.md`),
plus `INTEGRATION.md` which documents the live backend↔frontend contract. Design tokens are
centralized in `constants/tokens.ts` — the frontend `CLAUDE.md` forbids hardcoding
colors/spacing/fonts.

---

## 5. Frontend ↔ backend contract — known mismatches

The frontend's `INTEGRATION.md` already catalogs these. Carry them forward; some are
backend-side fixes that may matter during the migration/refactor:

- **Duration units** — backend `duration_seconds` (int) vs. frontend `duration` (minutes).
  Frontend divides by 60.
- **Chapters casing** — backend `start_minute` (snake) vs. frontend `startMinute` (camel).
  Mapped in `services/api.ts`.
- **Source domains on episodes** — backend returns `source_ids: UUID[]` with no domain;
  frontend wants `{domain}[]` for the favicon stack. Either backend adds a `source_domains`
  computed field, or frontend builds a UUID→domain map. **Decide which.**
- **Status → state mapping** — backend `queued|selecting|outlining|transcribing|synthesizing|ready|failed`
  collapses into frontend `loading|new|default|listened|in-progress` (uses `play_progress` + `listened`).

---

## 6. Open questions / decisions to resolve

These block or shape upcoming work. We'll answer them inline as we go and record the
resolution in §7.

1. **AWS shape** — App Runner vs. ECS Fargate for API? Keep Postgres queue or adopt SQS now?
2. **Region** for all AWS resources?
3. **IaC** — Terraform from the start, or clickops the first cut and codify later?
4. **Cutover plan** — blue/green? Do we keep Railway running and flip `EXPO_PUBLIC_API_URL`
   per-build via EAS, or DNS the new endpoint behind the existing hostname?
5. **Data migration** — does the Railway Postgres have prod data to move to RDS, or do we
   start clean? (pgvector dimension is 1536 per migration `0010`/`0013`.)
6. **`source_domains`** contract gap (§5) — backend-side or frontend-side fix?
7. **What are the actual frontend + backend feature changes** the user wants this cycle,
   beyond the migration? (Not yet specified — fill in when known.)
8. **Duplicate `0016_*` migration** — confirm it doesn't break Alembic linearity.

---

## 7. Decision log

> Append decisions here as `### YYYY-MM-DD — <decision>` with the rationale and who decided.
> Empty for now; we'll fill it as we make calls.

### 2026-06-11 — Stress test results (scripts/stress_test.py)
- **Reads:** 1 API task ≈ 230 req/s sweet spot @50 concurrent, 0 errors; saturates gracefully
  @100 (p99 11s, still 0 errors) → **add API autoscaling in Phase 5** (workers have it, API doesn't).
- **Failburst ×10:** all terminal `failed` <40s, queues drained, DLQ clean (permanent-fail fix at volume).
- **Genburst ×10 (~$3.50 LLM spend):** all 10 episodes ready in **11.5 min**; one interactive
  worker drained all ingests in 90s (lane isolation proven); background scaled 1→6 in ~4–8 min
  (CloudWatch cadence — acceptable, push-announced); **zero 429s at 6 concurrent generations**
  (rate-limit ceiling ≥6, untested beyond); zero retries/DLQ. Unit cost ≈ $0.35/episode, ~$0 AWS.
- Scale-in is lazy (by design, 300s cooldown) — fleet idles a while post-drain, pennies.

### 2026-06-11 — Phase 4 SHIPPED: SQS live, e2e proven
- Deployed on branch `v2.7-sqs` (commits 49a91bf + 7e21a52). API publishes to SQS; two lane
  worker services with backlog autoscaling; old worker parked (= rollback path).
- **E2E proof:** PG-essay save → interactive ingest <1 min → chained episode on background →
  ready ~3 min, MP3 on S3, presigned playback works. Failure path proven via Wikipedia 403:
  3 visibility-spaced retries, hard stop at maxReceiveCount, truthful audit row.
- **Design lesson captured (from Arihant's "is there no other way" question):** handler-chaining
  is the lightest of four pipeline patterns (vs one-big-job / Step Functions / event
  choreography); its known weakness is the *dropped baton* — and the first idempotency guard
  made it worse (ready→skip could swallow the chained episode after an enqueue failure). Fixed
  with a **baton-aware guard**: ready + episode exists → skip; ready + no episode → resume
  chain without re-scraping. If pipelines grow (daily briefs, fan-out), Step Functions is the
  natural upgrade and the per-stage job split makes that migration cheap.
- **Open:** DLQ-arrival confirmation; `source.status` cosmetic bug on 403 path; Phase 5
  (domain+HTTPS, final data sync, cutover, alarms).

### 2026-06-11 — SQS implementation design locked (Phase 4)
- **Queues:** `curia-interactive` + `curia-background`, standard, + one DLQ each.
  Visibility **900s** both (> proven 600s handler cap; prod history: episodes run ~3 min p50,
  zero timeout kills ever → no heartbeat code, accepted rare crash ⇒ ≤15 min redelivery).
  Long-poll 20s · `maxReceiveCount=3` → DLQ (mirrors max_attempts=3) · DLQ retention 14d.
- **Lanes by trigger, chosen at call site:** API routes (save URL, remix, generate ideas) →
  interactive; pipeline-chained + optimize → background. Pipeline-spawned episodes =
  **background** ("watched jobs vs announced jobs" — synthesis is announced by push).
- **Backend toggle:** `CURIA_QUEUE_BACKEND=postgres|sqs` — Postgres impl stays for local dev,
  tests, and one-env-var prod rollback.
- **Topology:** **two worker services from day 1** (user call) — each polls only its lane's
  queue via `CURIA_WORKER_LANE`; independent autoscaling on backlog-per-task.
- **Audit:** existing `jobs` table kept as write-only history (zero schema migration; columns
  fit). Worker stamps running/done/failed; on final attempt (ReceiveCount=3) stamp `failed`
  before the message ages into the DLQ, so enqueue-side dedup never wedges on a dead job.
- **Idempotency:** guards at top of handlers — `source.status=='ready'` / `episode.status=='ready'`
  → ack & skip. Post-merge job surface: 4 types (`ingest`, `generate_ideas`, `generate_episode`,
  `optimize`); `generate_from_source` retired; new producer in `intelligence/idea_generator.py`.
- **Decided by:** Arihant (visibility A, two services, background lane, keep toggle).

### 2026-06-07 — AWS migration: target shape locked, execution started
- **Locked:** Compute = **ECS Fargate** (one image, two services: `api` behind ALB, `worker`).
  Queue = **SQS now** (two queues split by *trigger* — `curia-interactive` / `curia-background`
  — + DLQs), built this push (not deferred). DB = **RDS PostgreSQL + pgvector**. Storage =
  **audio moves R2 → S3** (`curia-audio` bucket; decided 2026-06-09, supersedes earlier
  keep-R2 lean — simpler IAM via task roles, native AWS, CloudFront later; accepts S3 egress
  cost). Zero code: `blob.py` falls back to the boto3 credential chain when no explicit keys
  are set, and `audio_url` stores keys not URLs, so the move is object-copy + env flip.
  IaC = **manual console now, Terraform later** (maintain a runbook as we click).
  Firebase/keys/CORS all move via env/Secrets — **lift is zero-code**; only SQS needs code.
- **Code-change scope (verified from code):** Lift-and-shift = **no code changes**
  (`DATABASE_URL`, `FIREBASE_SERVICE_ACCOUNT_JSON` at `core/firebase.py:26`, `CURIA_STORAGE_*`
  already env-driven; `studio/generator.py:963` already uploads audio to object storage). SQS =
  the only real code: `lane` param + boto3 in `core/queue.py` (same signatures), `worker/main.py`
  receive/delete/visibility-heartbeat, **idempotency guards** in `worker/handlers/*` (check
  `episode/source.status` before working), keep audit-table writes.
- **Sequencing decision:** get compute+DB **live on the Postgres queue first** (working
  milestone), *then* flip to SQS — not deferring SQS, just ordering so there's a testable system
  before the queue swap.
- **Gotcha:** real prod env vars live in **Railway**, not the local `.env` (which has only 4).
  Pull the full set first.
- **Decided by:** Arihant ("we do SQS now, build until we finish — this is a startup").

### 2026-06-07 — AWS job system: SQS for transport + Postgres audit table
- **What:** On AWS, replace the Postgres-table-as-queue with **Amazon SQS as the live
  transport**, and keep a **thin Postgres `jobs` table as a durable, write-only audit/history
  log** (worker stamps type/status/attempts/error/duration as it goes). The two are
  complementary, not redundant: SQS is ephemeral (deletes on success, keeps no history); the
  table is the SQL-queryable record that powers `/admin/jobs` and post-hoc debugging.
- **Why:** SQS gives independence (API↔worker fully decoupled; enqueue no longer writes the DB;
  removes the 2s RDS poll load) and queue-level observability (CloudWatch backlog/age metrics,
  DLQ). The audit table preserves the queryable job history SQS throws away. User chose to keep
  both deliberately.
- **Design implications (from reading `core/queue.py` + `worker/main.py` + handlers):**
  - **Idempotency is via domain status, NOT the table.** Handlers must check `episode.status`/
    `source.status` ("already ready/running? → skip") so SQS at-least-once redelivery is a no-op.
    This is the main correctness work of the migration.
  - **Visibility timeout must exceed the long-job runtime.** `generate_episode` runs up to
    `HANDLER_TIMEOUT_SECONDS=600`; set the slow queue's visibility > that (e.g. 15 min) or
    heartbeat via `ChangeMessageVisibility`, else a healthy long job gets redelivered/duplicated.
    SQS visibility-expiry replaces the current `reap_stale()`.
  - **DLQ replaces fail-after-N.** `maxReceiveCount` → dead-letter queue = the "failed jobs"
    surface + a `DLQ depth > 0` alarm.
  - **Code scope:** swap the 5 functions in `core/queue.py` to boto3 (keep signatures); change
    `worker/main.py` from DB-poll to `receive_message` long-poll + `delete_message` on ack +
    visibility heartbeat for long jobs; add idempotency guards in `worker/handlers/*`; worker
    also writes the audit row.
- **Still open (sub-decision):** one queue vs **two queues (fast/slow)** to preserve the current
  priority model (`ingest=1` … `generate_episode=10`). SQS has no priorities, so honoring them
  means split queues + worker preference. To be decided before spec.
- **Decided by:** Arihant.

### 2026-06-07 — Frontend caching + adaptive polling (implemented)
- **What:** First coordinated change of this cycle. In `curia-frontend` (v2.8): added a
  per-user versioned AsyncStorage cache (`utils/storage.ts`), made `EpisodesContext` and the
  studio Pile hydrate-on-launch + write-through, replaced the three always-on 5s polls with
  **adaptive polling** (≈4s while generating, **45s** idle, paused on app-background, instant
  refetch on foreground and on FCM push via `triggerEpisodesRefetch()`).
- **Why:** The app kept all list data in memory and polled `GET /episodes` + `GET /sources`
  every 5s forever → blank screen on every cold start/user-switch, list flicker, no offline,
  and constant API load (a real cost once we're on AWS/RDS). Goal was UX: open to content,
  never flash blank, stay usable offline, while still updating live during generation.
- **Decisions (locked with Arihant):** hand-rolled on existing contexts (no TanStack — follows
  the frontend `CLAUDE.md`); frontend-only this pass (no backend ETag/version endpoint yet);
  audio caching out of scope (audio stays on Cloudflare R2, S3 migration owns it later);
  idle poll set to 45s at user's request.
- **Verification:** `tsc` clean for the touched files (9 pre-existing baseline errors elsewhere
  untouched); Jest 42/42 green incl. 6 new cache tests (`__tests__/storage.test.ts`). Runtime
  UX verification (instant cold start, cadence shift, push refetch) to be done on device.
- **Deferred (tracked):** backend conditional-GET (ETag/`Cache-Control` on `/auth/me` &
  `/episodes/{id}`, list change-signal/version endpoint, `episode.updated_at` migration, fix
  `GET /sources` `covered_in` N+1) — revisit during the AWS migration.
- **Decided by:** Arihant.

### 2026-06-07 — Established two-repo + cross-repo doc convention
- **What:** Created this file (`CHANGETHOUGHT.md`) in the backend repo as the shared design/
  decision brain for both the v2.7 backend and v2.8 frontend during the AWS-migration cycle.
  `CHANGELOG.md` stays the per-file ledger; frontend keeps its own `log.md`.
- **Why:** The user is making coordinated frontend + backend changes plus an AWS migration and
  wants a living record of intent, not just diffs.
- **Decided by:** Arihant.

---

## 8. Working notes (scratch)

- Migrating **from Railway → AWS**. Frontend prod URL pinned in `eas.json`.
- Backend is genuinely "AWS-shaped": storage already S3-capable, queue is SQS-shaped,
  one image / two commands mirrors the Fargate split. Lowest-risk first move is lift-and-shift.
- Treat README/CLAUDE.md/BACKEND_PLAN.md as **stale**; verify against code. Reconcile docs
  after the migration settles.
