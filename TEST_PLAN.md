# Curia v2 — Test Plan

## Setup

```bash
export USER_TOKEN="ck_x9vNIM3Drtg4v1Khwr1hxm57w5UMW3KbqA7J20VPqyc"
export QA_TOKEN="ck_BLBXx4gbt2t-_FO098RV5wcZjFIFWDLMTbs5mKccJUw"
export USER_ID="5112b35e-f1dd-4590-b34e-5a6a1e781f02"
export QA_ID="3836f5fc-4332-43d1-a69a-76b545d09072"
export BASE="http://localhost:8000"
```

---

## Testing Strategy — How to Orchestrate Agents

This section is for anyone (human or agent) picking up this test plan cold.
It explains the dependency graph, how to split work across parallel agents,
and the rules each agent must follow.

---

### Prerequisites — services that must be running

Before any test agent fires a single request, confirm these are up:

```bash
# 1. PostgreSQL
pg_isready -h localhost -p 5432 -U curia

# 2. API server
curl -s http://localhost:8000/health
# Expected: {"status":"ok","db":true}

# 3. Worker process (separate terminal)
# Must be running: python -m worker
# Verify a job gets picked up within ~5s of submission
```

If any of these fail, stop. Tests will produce misleading results, not useful failures.

---

### Dependency graph

Tests are grouped into four waves. A wave cannot start until the previous wave is complete.
Within a wave, independent test cases can run in parallel across separate agents.

```
Wave 1 — Foundation (sequential, ~3 min)
  TC-1  Auth & role gates
  TC-2  Source ingest happy path        ← produces SOURCE_IDs used by all later waves
  TC-3  Source ingest failure modes

Wave 2 — Knowledge + Ideas (parallel, ~5 min)
  Agent A:  TC-4 → TC-5                 ← KB must be set before rubric is tested
  Agent B:  TC-6                        ← idea generation needs sources from Wave 1

Wave 3 — Episode generation (parallel agents, ~10-15 min)
  Agent C:  TC-7 → TC-8                 ← happy path then quality inspection on same episode
  Agent D:  TC-12                       ← 4 format remixes, all parallel within the TC
  Agent E:  TC-13                       ← override episodes (length + speaker)
  Agent F:  TC-17                       ← failure states (fast, no real generation)

Wave 4 — Inspection (parallel, after Wave 3, ~5 min)
  Agent G:  TC-9 → TC-10 → TC-11       ← admin, isolation, edge cases
  Agent H:  TC-16                       ← lineage check needs completed episodes from Wave 3
```

---

### How each agent self-discovers state (no ID handoff needed)

Agents do **not** receive hardcoded IDs from prior waves. Each agent queries the API
at startup to find what it needs. This makes agents stateless and re-runnable.

**Finding a ready source:**
```bash
SOURCE_ID=$(curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/sources \
  | python3 -c "import sys,json; srcs=[s for s in json.load(sys.stdin) if s['status']=='ready']; print(srcs[0]['id'])")
```

**Finding a ready show idea:**
```bash
IDEA_ID=$(curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/ideas \
  | python3 -c "import sys,json; ideas=json.load(sys.stdin); print(ideas[0]['id'])")
```

**Finding a ready episode:**
```bash
EPISODE_ID=$(curl -s -H "Authorization: Bearer $USER_TOKEN" "$BASE/episodes?status=ready&limit=1" \
  | python3 -c "import sys,json; eps=json.load(sys.stdin); print(eps[0]['id'])")
```

**Polling until an episode is ready (max 15 min):**
```bash
poll_episode() {
  local ID=$1
  for i in $(seq 1 90); do
    STATUS=$(curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$ID \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    echo "[$i] $ID → $STATUS"
    [[ "$STATUS" == "ready" || "$STATUS" == "failed" ]] && break
    sleep 10
  done
  echo "$STATUS"
}
```

---

### Agent prompt template

When spawning a test agent, give it this context block at the top of its prompt:

```
You are a test agent for the Curia v2 pipeline.
Your job: run the test cases assigned to you, record pass/fail for each sub-test,
and append a results block to TEST_RESULTS.md.

Environment:
  BASE=http://localhost:8000
  USER_TOKEN=ck_x9vNIM3Drtg4v1Khwr1hxm57w5UMW3KbqA7J20VPqyc
  QA_TOKEN=ck_BLBXx4gbt2t-_FO098RV5wcZjFIFWDLMTbs5mKccJUw
  USER_ID=5112b35e-f1dd-4590-b34e-5a6a1e781f02
  QA_ID=3836f5fc-4332-43d1-a69a-76b545d09072
  DB=postgresql://curia:curia@localhost:5432/curia

Rules:
1. Self-discover IDs from the API — do not assume any hardcoded IDs exist.
2. Poll episodes until status=ready or status=failed (max 15 min, 10s intervals).
3. Record every sub-test result as PASS / FAIL / SKIP with a one-line reason.
4. If a sub-test fails, record the actual response and continue — do not abort.
5. Append your results to TEST_RESULTS.md using the format below.
6. Do not modify any source code. Read-only except for TEST_RESULTS.md.

Test cases assigned: TC-X, TC-Y, TC-Z
(See TEST_PLAN.md for the full commands and expected outputs for each.)

Results format:
## Wave N — Agent [letter] — [timestamp]
| TC | Sub-test | Result | Notes |
|----|----------|--------|-------|
| 7.1 | Episode reaches ready | PASS | took 4m32s |
| 7.2 | Title non-empty | PASS | "Why Memory Fails" |
| 8.1 | Quality score present | FAIL | score was null |
```

---

### Wave execution order

Run waves in sequence. Within a wave, fire all agents simultaneously.

```bash
# Wave 1 — run manually or with a single agent, sequential
# Confirm TC-2 produced at least 1 ready source before proceeding.

# Wave 2 — fire Agent A and Agent B in parallel
# Confirm TC-6 produced at least 1 show idea before proceeding.

# Wave 3 — fire Agents C, D, E, F in parallel
# All episode jobs will queue and process concurrently in the worker.
# Wait until all 4 agents report done before starting Wave 4.

# Wave 4 — fire Agents G and H in parallel
```

---

### Results file

Each agent appends to `TEST_RESULTS.md` at the repo root. After all waves complete,
`TEST_RESULTS.md` is the single source of truth for pass/fail state.
Create the file before the first wave if it doesn't exist:

```bash
echo "# Curia v2 — Test Results\nRun started: $(date)" > \
  /Users/bhabanimohapatra/Documents/Projects/curia-v2/TEST_RESULTS.md
```

---

## TC-1: Auth & Role Gates

**What we're testing:** API keys work, wrong keys fail, role boundaries are enforced.

| # | Test | Command | Expected |
|---|------|---------|----------|
| 1.1 | Valid user token | `curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/me` | 200, `"role":"user"` |
| 1.2 | Valid QA token | `curl -s -H "Authorization: Bearer $QA_TOKEN" $BASE/me` | 200, `"role":"qa"` |
| 1.3 | Missing token | `curl -s $BASE/me` | 401 |
| 1.4 | Garbage token | `curl -s -H "Authorization: Bearer bad_token" $BASE/me` | 401 |
| 1.5 | User hits admin endpoint | `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $USER_TOKEN" $BASE/admin/users` | 403 |
| 1.6 | QA hits admin endpoint | `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $QA_TOKEN" $BASE/admin/users` | 200 |
| 1.7 | QA reads another user's KB | `curl -s -H "Authorization: Bearer $QA_TOKEN" $BASE/admin/users/$USER_ID/kb` | 200, user's KB |
| 1.8 | Health check (no auth) | `curl -s $BASE/health` | `{"status":"ok","db":true}` |

---

## TC-2: Source Ingest — Happy Path

**What we're testing:** URLs are scraped, all 7 transformations run, status transitions are correct.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 2.1 | Submit a URL | `POST /sources {"url": "https://en.wikipedia.org/wiki/Attention"}` | `{id, status: "queued", job_id}` |
| 2.2 | Status transitions | Poll `GET /sources/{id}` every 5s | `queued → scraping → transforming → embedding → ready` |
| 2.3 | All 7 insights present | `GET /sources/{id}` when ready | `summary`, `metadata`, `key_insights`, `human_stakes`, `core_tensions`, `counterpoints`, `examples` all non-null |
| 2.4 | Title extracted | Same response | `title` is not "Processing..." |
| 2.5 | Appears in source list | `GET /sources` | Source is in the list with `status: ready` |
| 2.6 | Submit same URL again | `POST /sources` with same URL | Returns same `id` (idempotent, no duplicate) |

---

## TC-3: Source Ingest — Failure Modes

**What we're testing:** Bad inputs fail gracefully, errors are surfaced correctly.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 3.1 | Unreachable URL | `POST /sources {"url": "https://this-does-not-exist-xyz.com"}` | Source ends in `status: failed` |
| 3.2 | Error surfaced in job | `GET /admin/jobs?status=failed` (QA token) | Job has `last_error` populated |
| 3.3 | Bot-blocked site | `POST /sources {"url": "https://www.lesswrong.com/posts/..."}` | Ingests but scraped content is very short (<1000 chars) — verify via `GET /sources/{id}` |
| 3.4 | Delete a source | `DELETE /sources/{id}` | 204, source gone from `GET /sources` |

---

## TC-4: Knowledge Bank

**What we're testing:** KB saves and loads correctly, validation works, defaults apply when KB is empty.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 4.1 | Get empty KB | `GET /me/kb` with QA user (no KB set) | Returns empty defaults |
| 4.2 | Set KB | `PUT /me/kb` with valid payload (see below) | 200, returns saved KB |
| 4.3 | Get KB after save | `GET /me/kb` | Matches what was saved |
| 4.4 | Invalid KB field | `PUT /me/kb` with `preferred_length_minutes: 999` | 422 validation error |
| 4.5 | Invalid tone value | `PUT /me/kb` with `preferred_tone: "aggressive"` | 422 validation error |
| 4.6 | Extra unknown field | `PUT /me/kb` with `{"foo": "bar"}` | 422 (extra fields forbidden) |
| 4.7 | Partial KB (missing sections) | `PUT /me/kb` with only `identity` set | 200, other sections default |

**Sample valid KB payload:**
```json
{
  "identity": {"name": "arihunter", "reading_volume_per_week": "10-15 articles"},
  "interests": {"topics": ["cognitive science", "philosophy of mind"], "current_obsession": "how attention shapes memory"},
  "preferences": {
    "preferred_length_minutes": 12,
    "preferred_formats": ["clarity_engine", "exploration_engine"],
    "preferred_tone": "analytical",
    "tolerates_ambiguity": "high",
    "novelty_appetite": 0.8
  },
  "listening_context": {"when": "morning", "while_doing": "walking"},
  "dislikes": {"themes": ["productivity hacks"], "tones": ["motivational"], "formats": []}
}
```

---

## TC-5: Rubric — Personalization

**What we're testing:** The rubric changes when KB changes, and two users get different rubrics.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 5.1 | Rubric with no KB | `GET /me/rubric/transcript` (QA user, no KB) | Prompt contains "No user-specific preferences yet" |
| 5.2 | Rubric with KB | Set KB for QA user, then `GET /me/rubric/transcript` | Prompt contains preferred tone, topics, dislikes |
| 5.3 | Rubric reflects tone | Set `preferred_tone: "punchy"`, get rubric | "punchy" appears in USER PREFERENCES section |
| 5.4 | Rubric reflects dislikes | Set `dislikes.themes: ["philosophy"]`, get rubric | "philosophy" appears as avoid theme |
| 5.5 | Two users, different rubrics | Get rubric for user and QA user (via admin) side by side | USER PREFERENCES section is visibly different |
| 5.6 | Rubric updates immediately | Change KB, immediately get rubric | New KB reflected — no cache delay |
| 5.7 | Outline rubric exists | `GET /me/rubric/outline` | Returns outline-specific judge prompt |
| 5.8 | Invalid task | `GET /me/rubric/foobar` | 422 or 404 |

```bash
# TC-5.5 side-by-side comparison
curl -s -H "Authorization: Bearer $QA_TOKEN" $BASE/admin/users/$USER_ID/rubric/transcript \
  | python3 -c "import sys,json; p=json.load(sys.stdin)['judge_prompt']; print(p[p.find('USER PREFERENCES'):p.find('SCORING')])"

curl -s -H "Authorization: Bearer $QA_TOKEN" $BASE/admin/users/$QA_ID/rubric/transcript \
  | python3 -c "import sys,json; p=json.load(sys.stdin)['judge_prompt']; print(p[p.find('USER PREFERENCES'):p.find('SCORING')])"
```

---

## TC-6: Idea Generation

**What we're testing:** Ideas are generated from the archive, KB shapes the angles, re-running clears old ideas.

**Prerequisite:** At least 3 sources in `ready` state.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 6.1 | Generate ideas | `POST /ideas/generate` | `{job_id, status: "queued"}` |
| 6.2 | Ideas created | `GET /ideas` after job completes | At least 1 idea with `angle`, `format`, `source_ids` |
| 6.3 | Format is valid | Each idea's `format` | One of: `narrative_drift`, `clarity_engine`, `momentum_loop`, `exploration_engine` |
| 6.4 | Source IDs are valid | Each idea's `source_ids` | IDs match actual sources in `GET /sources` |
| 6.5 | Re-run clears old ideas | Run `POST /ideas/generate` again | Previous ungenerated ideas gone, new ones replace them |
| 6.6 | KB dislikes filter | Set `dislikes.themes: ["memory"]`, re-generate | Ideas about memory should not appear |
| 6.7 | KB interests steer angles | Set obsession to a specific topic, generate | Idea angles are biased toward that topic |

---

## TC-7: Episode Generation — Happy Path

**What we're testing:** Full pipeline from idea → ready episode with all fields populated.

**Prerequisite:** At least 1 idea in `GET /ideas`.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 7.1 | Submit episode | `POST /episodes {"show_name": "clarity_engine", "show_idea_id": "<id>"}` | `{id, status: "queued", job_id}` |
| 7.2 | Status transitions | Poll `GET /episodes/{id}` | `queued → outlining → transcribing → synthesizing → ready` |
| 7.3 | Outline present | Final episode | `outline.title` is specific (not generic), `outline.segments` count matches format default |
| 7.4 | Transcript present | Final episode | `transcript` is a list of `{speaker, text}` objects, 6–80 lines |
| 7.5 | Quality fields present | Final episode | `quality_score`, `quality_feedback`, `quality_violations` all populated |
| 7.6 | Audio file exists | `GET /episodes/{id}/audio` | 200, returns MP3 (silent with stub, real audio with ElevenLabs key) |
| 7.7 | Episode in list | `GET /episodes` | Episode appears with status `ready` |
| 7.8 | All 4 formats work | Generate one episode per format | Each completes successfully |

---

## TC-8: Episode Quality — Judge & Re-roll

**What we're testing:** The judge enforces the quality floor, violations are correctly identified, re-roll triggers when score is low.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 8.1 | Floor violations score 0 | Check any episode with violations | `quality_score: 0.0` whenever `quality_violations` is non-empty |
| 8.2 | Re-roll triggered | Check `regenerated` field | `true` if first transcript scored below 0.6 |
| 8.3 | Violations are specific | `quality_violations` list | Specific rule names cited, not vague |
| 8.4 | Feedback is actionable | `quality_feedback` | Points to what to change, not just "bad transcript" |
| 8.5 | KB preference score | Episode with KB set vs without | `preference_score` differs between users |
| 8.6 | High-ambiguity KB | Set `tolerates_ambiguity: "high"`, generate | Rubric penalizes explicit takeaways — score reflects this |
| 8.7 | Low-ambiguity KB | Set `tolerates_ambiguity: "low"`, generate | Rubric rewards explicit takeaways |

---

## TC-9: Admin / QA Inspection

**What we're testing:** QA can see all users' data, job queue is inspectable, failed jobs surface errors.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 9.1 | List all users | `GET /admin/users` (QA token) | Both users listed with `has_kb`, `role` |
| 9.2 | View user profile | `GET /admin/users/$USER_ID` | User's profile fields |
| 9.3 | View user's sources | `GET /admin/users/$USER_ID/sources` | User's source list |
| 9.4 | View user's episodes | `GET /admin/users/$USER_ID/episodes` | Episodes with `quality_score` |
| 9.5 | View user's KB | `GET /admin/users/$USER_ID/kb` | User's full KB |
| 9.6 | View user's rubric | `GET /admin/users/$USER_ID/rubric/transcript` | Full judge prompt for that user |
| 9.7 | List all jobs | `GET /admin/jobs` (QA token) | All jobs across all users |
| 9.8 | Filter failed jobs | `GET /admin/jobs?status=failed` | Only failed jobs, each with `last_error` |
| 9.9 | View specific job | `GET /admin/jobs/{id}` | Full job payload |

---

## TC-10: Multi-user Isolation

**What we're testing:** User A cannot see User B's data. Each user's pipeline is fully isolated.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 10.1 | Sources isolated | Ingest URL as user, check QA's `GET /sources` | QA sees 0 sources (their own archive is empty) |
| 10.2 | Ideas isolated | Generate ideas as user, check QA's `GET /ideas` | QA sees 0 ideas |
| 10.3 | Episodes isolated | Generate episode as user, check QA's `GET /episodes` | QA sees 0 episodes |
| 10.4 | KB isolated | Set KB as user, QA's `GET /me/kb` unchanged | QA's KB unaffected |
| 10.5 | Rubrics differ | Same source, different KBs | QA admin view shows different USER PREFERENCES per user |

---

## TC-11: Edge Cases

**What we're testing:** The system handles degenerate inputs without crashing.

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 11.1 | Episode with no sources | Fresh user, `POST /episodes` directly | Fails gracefully with a clear error, not a 500 |
| 11.2 | Ideas with < 3 sources | 1–2 sources only, generate ideas | Produces standalone ideas, no cluster ideas |
| 11.3 | Ideas with zero embeddings | No Voyage key (stub), generate | All standalones (no clusters) — expected degraded behaviour |
| 11.4 | Worker down, job stuck | Kill worker, submit job, restart worker | Job picked up and completed on restart |
| 11.5 | Stuck running job | Manually set job to `running` 20+ min ago | Reset query re-queues it, worker picks it up |
| 11.6 | Duplicate idea generation | `POST /ideas/generate` twice rapidly | No duplicate ideas — second run replaces first |

```bash
# TC-11.5 — reset stuck jobs
psql -U curia -d curia -c "
  UPDATE jobs SET status='queued', locked_at=NULL, locked_by=NULL
  WHERE status='running' AND locked_at < now() - interval '15 min';"
```

---

## TC-12: Episode Remix — Parameter Overrides

**What we're testing:** A user can take any existing show idea and regenerate it with overridden
parameters — different format, different angle, different length, different host — without
changing the underlying source material.

**The idea:** same sources, different output. The remix is a new episode row, not a mutation
of the original.

**Prerequisite:** At least 1 idea in `GET /ideas` and 1 completed episode to remix from.

### 12.1 — Format override (same idea, different show format)

Take the memory idea and generate it in all 4 formats. Each should produce a structurally
different episode from the same source material.

```bash
IDEA_ID="8da6663d-7713-4966-9d3d-03363348a6d0"

for FORMAT in narrative_drift clarity_engine momentum_loop exploration_engine; do
  echo "--- Generating: $FORMAT ---"
  curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
       -d "{\"show_name\":\"$FORMAT\",\"show_idea_id\":\"$IDEA_ID\"}" \
       $BASE/episodes | python3 -m json.tool
done
```

**Expected:**
- 4 separate episode IDs created
- Each episode's `outline.segments` count differs (`clarity_engine`=6, `exploration_engine`=8, `momentum_loop`=10, `narrative_drift`=8)
- `outline.thread` differs in framing across formats
- `show_name` on each episode matches the requested format

---

### 12.2 — Angle override via `editorial_direction`

Regenerate the same idea but steer the angle with a free-text override.

```bash
IDEA_ID="8da6663d-7713-4966-9d3d-03363348a6d0"

# Original angle: "Memory isn't a recording device — it's a reconstruction machine"
# Override: focus specifically on sleep and memory consolidation

curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "show_name": "clarity_engine",
       "show_idea_id": "'"$IDEA_ID"'",
       "editorial_direction": "Focus specifically on sleep and memory consolidation — why the hippocampus needs sleep to transfer memories to long-term storage."
     }' $BASE/episodes | python3 -m json.tool
```

**Expected:**
- Episode generates successfully
- `outline.thread` reflects the sleep/consolidation angle, not the generic memory angle
- `outline.segments` reference sleep-specific primitives from the source
- `editorial_direction` stored on the episode row

---

### 12.3 — Length override via KB

The cleanest way to override length today is via the KB. Set `preferred_length_minutes`
to different values and compare the resulting transcripts.

```bash
# Short version (5 min)
curl -s -X PUT -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "identity": {"name": "arihunter"},
       "interests": {"topics": ["memory"]},
       "preferences": {
         "preferred_length_minutes": 5,
         "preferred_formats": ["clarity_engine"],
         "preferred_tone": "analytical",
         "tolerates_ambiguity": "low",
         "novelty_appetite": 0.5
       },
       "listening_context": {},
       "dislikes": {"themes": [], "tones": [], "formats": []}
     }' $BASE/me/kb

curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","show_idea_id":"'"$IDEA_ID"'"}' \
     $BASE/episodes | python3 -m json.tool

# Long version (20 min)
curl -s -X PUT -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "identity": {"name": "arihunter"},
       "interests": {"topics": ["memory"]},
       "preferences": {
         "preferred_length_minutes": 20,
         "preferred_formats": ["clarity_engine"],
         "preferred_tone": "analytical",
         "tolerates_ambiguity": "low",
         "novelty_appetite": 0.5
       },
       "listening_context": {},
       "dislikes": {"themes": [], "tones": [], "formats": []}
     }' $BASE/me/kb

curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","show_idea_id":"'"$IDEA_ID"'"}' \
     $BASE/episodes | python3 -m json.tool
```

**Expected:**
- Short episode: fewer transcript lines, tighter outline
- Long episode: more transcript lines, more developed segments
- Audio duration reflects the difference (check `GET /episodes/{id}` after ready)
- Both use the same source material

**Compare line counts:**
```bash
# After both episodes are ready:
SHORT_ID="<short episode id>"
LONG_ID="<long episode id>"

curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$SHORT_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print('short lines:', len(e['transcript']))"

curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$LONG_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print('long lines:', len(e['transcript']))"
```

---

### 12.4 — Host/speaker override via show format

Different show formats use different hosts. Use this to test the same idea
voiced by different speakers.

```bash
IDEA_ID="8da6663d-7713-4966-9d3d-03363348a6d0"

# kenji hosts clarity_engine and narrative_drift
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"narrative_drift","show_idea_id":"'"$IDEA_ID"'"}' \
     $BASE/episodes | python3 -m json.tool
```

**Check which speaker voices the episode:**
```bash
EPISODE_ID="<episode id>"
curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$EPISODE_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); speakers=set(l['speaker'] for l in e['transcript']); print('speakers:', speakers)"
```

**Expected:**
- Speaker name appears consistently in all transcript lines
- Audio file uses the correct edge_tts voice for that speaker

---

### 12.5 — Full remix: all overrides at once

```bash
IDEA_ID="8da6663d-7713-4966-9d3d-03363348a6d0"

curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "show_name": "exploration_engine",
       "show_idea_id": "'"$IDEA_ID"'",
       "editorial_direction": "Reframe memory not as storage but as prediction — the brain remembers in order to anticipate, not to preserve."
     }' $BASE/episodes | python3 -m json.tool
```

**Expected:**
- Format: `exploration_engine` (claim → counterpoint → expansion → link → reframe structure)
- Angle: prediction framing, not storage/recording framing
- Same Wikipedia Memory source used
- `editorial_direction` visible on the stored episode

---

## TC-13: Direct Per-Episode Overrides (`length_minutes` + `speaker`)

**What we're testing:** The `length_minutes` and `speaker` fields added to `POST /episodes`
override the show's defaults without touching the KB or changing show format.
Each field is independent — either can be set alone or together.

**Prerequisite:** At least 1 idea in `GET /ideas`.

```bash
IDEA_ID="8da6663d-7713-4966-9d3d-03363348a6d0"
```

---

### 13.1 — Length override: short (5 min)

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "show_name": "clarity_engine",
       "show_idea_id": "'"$IDEA_ID"'",
       "length_minutes": 5
     }' $BASE/episodes | python3 -m json.tool
```

**Expected:** Episode created (202). Once `status=ready`, transcript has noticeably fewer lines
than a default-length episode of the same format.

---

### 13.2 — Length override: long (20 min)

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "show_name": "clarity_engine",
       "show_idea_id": "'"$IDEA_ID"'",
       "length_minutes": 20
     }' $BASE/episodes | python3 -m json.tool
```

**Expected:** More transcript lines than 13.1. Compare:

```bash
SHORT_ID="<id from 13.1>"
LONG_ID="<id from 13.2>"

curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$SHORT_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print('short lines:', len(e['transcript']))"

curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$LONG_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print('long lines:', len(e['transcript']))"
```

---

### 13.3 — Speaker override: arjun

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "show_name": "clarity_engine",
       "show_idea_id": "'"$IDEA_ID"'",
       "speaker": "arjun"
     }' $BASE/episodes | python3 -m json.tool
```

**Expected:** All transcript lines have `"speaker": "arjun"`. Audio uses arjun's edge_tts voice.

```bash
EPISODE_ID="<episode id>"
curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$EPISODE_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); speakers=set(l['speaker'] for l in e['transcript']); print('speakers:', speakers)"
# Expected: speakers: {'arjun'}
```

---

### 13.4 — Speaker override: emeka

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "show_name": "clarity_engine",
       "show_idea_id": "'"$IDEA_ID"'",
       "speaker": "emeka"
     }' $BASE/episodes | python3 -m json.tool
```

**Expected:** All lines `"speaker": "emeka"`. Emeka's speech pattern (fast, punchy, domain-jumping)
should be distinct from arjun's (step-by-step, precise, open-ended) in the same format.

---

### 13.5 — Combined: length + speaker

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "show_name": "narrative_drift",
       "show_idea_id": "'"$IDEA_ID"'",
       "length_minutes": 8,
       "speaker": "emeka"
     }' $BASE/episodes | python3 -m json.tool
```

**Expected:** narrative_drift format, ~8 min length, all lines voiced by emeka.
Verify DB columns are stored correctly:

```bash
EPISODE_ID="<episode id>"
psql postgresql://curia:curia@localhost:5432/curia -c \
  "SELECT show_name, length_minutes, speaker_override FROM episode WHERE id='$EPISODE_ID';"
# Expected: narrative_drift | 8 | emeka
```

---

### 13.6 — Validation: length out of range

```bash
# Too short (< 3)
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","length_minutes":1}' \
     $BASE/episodes

# Too long (> 30)
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","length_minutes":60}' \
     $BASE/episodes
```

**Expected:** Both return 422 with a validation error on `length_minutes`.

---

### 13.7 — Validation: unknown speaker

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","speaker":"morgan"}' \
     $BASE/episodes
```

**Expected:** Episode creation succeeds (422 not returned — speaker is free text at the API layer).
Worker sets status=`failed` with error: `Unknown speaker override 'morgan'. Known: ['arjun', 'emeka', 'kenji']`.

---

## TC-16: Source → Episode Lineage

**What we're testing:** The `source_ids` on an episode correctly reflects which sources were
used to generate it. The Pile source detail screen (2.4.1) relies on this to show which
episodes a source appears in. A remixed episode must carry its own independent `source_ids`,
not inherit from the original.

**Prerequisite:** At least 1 completed episode with known source IDs.

### 14.1 — Episode records its source IDs

```bash
EPISODE_ID="<a ready episode id>"
curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$EPISODE_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print('source_ids:', e['source_ids'])"
```

**Expected:** `source_ids` is a non-empty list of UUIDs. Each UUID resolves to a real source
in `GET /sources`.

```bash
# Verify each source_id exists
for SRC_ID in <id1> <id2>; do
  curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/sources/$SRC_ID \
    | python3 -c "import sys,json; s=json.load(sys.stdin); print(s['id'], s['status'], s['title'][:50])"
done
```

---

### 14.2 — Remix gets independent source IDs

Generate a remix of the same idea and verify it gets its own `source_ids` (same content,
separate row — not a pointer to the original episode's IDs).

```bash
IDEA_ID="<show idea id>"

# Original
EP1=$(curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","show_idea_id":"'"$IDEA_ID"'"}' \
     $BASE/episodes | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Remix
EP2=$(curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"narrative_drift","show_idea_id":"'"$IDEA_ID"'","speaker":"arjun"}' \
     $BASE/episodes | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo "EP1: $EP1"
echo "EP2: $EP2"
```

Once both are `ready`:

```bash
curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$EP1 \
  | python3 -c "import sys,json; print('EP1 sources:', json.load(sys.stdin)['source_ids'])"

curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$EP2 \
  | python3 -c "import sys,json; print('EP2 sources:', json.load(sys.stdin)['source_ids'])"
```

**Expected:**
- Both episodes have non-empty `source_ids`
- The lists are independent (same UUIDs is fine — both drew from the same idea's sources — but EP2's list must not be null or empty)
- Neither episode's `source_ids` is a reference to the other's row

---

## TC-17: Episode Generation Failure States

**What we're testing:** When generation fails, the episode row captures the failure clearly.
The UX show card has an error state — it relies on `status=failed` + a readable `error` field,
not a silent crash or a stuck `running` row.

### 15.1 — Unknown show name fails fast

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"nonexistent_show"}' \
     $BASE/episodes | python3 -m json.tool
```

**Expected:** Worker picks it up and sets `status=failed` with `error` containing
`"Unknown show_name 'nonexistent_show'"`. The episode row is not stuck in `queued` or `running`.

```bash
EPISODE_ID="<id from above>"
curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$EPISODE_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print(e['status'], '|', e['error'])"
# Expected: failed | Unknown show_name 'nonexistent_show'...
```

---

### 15.2 — Unknown speaker fails in worker (not at API)

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","speaker":"morgan"}' \
     $BASE/episodes | python3 -m json.tool
# Expected: 202 accepted

EPISODE_ID="<id from above>"
# Poll until failed:
curl -s -H "Authorization: Bearer $USER_TOKEN" $BASE/episodes/$EPISODE_ID \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print(e['status'], '|', e['error'])"
# Expected: failed | Unknown speaker override 'morgan'. Known: ['arjun', 'emeka', 'kenji']
```

---

### 15.3 — Failed episode does not block the queue

Submit a bad episode and a good episode back to back. The good one should complete
successfully regardless of the bad one failing.

```bash
# Bad one first
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","speaker":"morgan"}' \
     $BASE/episodes

# Good one immediately after
IDEA_ID="<a valid idea id>"
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d '{"show_name":"clarity_engine","show_idea_id":"'"$IDEA_ID"'"}' \
     $BASE/episodes | python3 -m json.tool
```

**Expected:** Good episode reaches `status=ready`. Bad episode reaches `status=failed`.
Neither blocks or poisons the other.

---

## Known Issues (from initial test run)

| Issue | Where | Severity |
|-------|-------|----------|
| `from optimization.rubrics import judge` imports module, not function | `studio/generator.py:40` | **Fixed** |
| `status_code=204` routes crash FastAPI startup | `sources.py`, `admin.py` | **Fixed** |
| Paul Graham / LessWrong scrape blocked (~500 chars) | `core/ingest.py` scraper | Open |
| Transcript uses "you/your" direct address — quality floor always violated | `core/prompts/` | Open |
| No Voyage key → zero vectors → no clusters ever form | `core/embeddings.py` | Expected (needs key) |
