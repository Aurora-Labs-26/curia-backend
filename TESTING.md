# Curia — Local Testing Guide

Two identities pre-provisioned. Use them in two terminals to verify the system end-to-end and to confirm multi-user isolation.

---

## The two identities

| Role  | user_id                                 | email                                    | api_token                                              |
|-------|-----------------------------------------|------------------------------------------|--------------------------------------------------------|
| user  | `3d7e8e41-8908-413c-beac-74c143cdd29c`  | arihantbarjatyausa@gmail.com (arihunter) | `ck_9Dv8uQ995fVVvW6rFcPq56m6v-3cuKzbe43h6pWfuYk`       |
| **qa** | `4ed32e1d-b75d-41b2-899a-a7d2a935bfc5`  | qa@curia.local                           | `ck_-suG6R7nNH-WcwsB4Cf5o8uQd9dJpfDXGd-w2ohIgKc`       |

**Roles are real** — `users.role` is `'user'` or `'qa'`. The `qa_required` middleware gates `/admin/*` endpoints; regular users get 403.

What each role can do:

| Capability                                | user | qa |
|-------------------------------------------|------|----|
| `GET /me`, `GET/PUT /me/kb`               | ✓    | ✓  |
| `GET /me/rubric/{task}` (own rubric)      | ✓    | ✓  |
| `POST /sources`, `GET /sources`, etc.     | ✓    | ✓  (own data only) |
| `GET /admin/users`                        | ✗    | ✓  |
| `GET /admin/users/{id}` (any user)        | ✗    | ✓  |
| `GET /admin/users/{id}/kb`                | ✗    | ✓  |
| `GET /admin/users/{id}/rubric/{task}`     | ✗    | ✓  |
| `GET /admin/users/{id}/sources`           | ✗    | ✓  |
| `GET /admin/users/{id}/episodes`          | ✗    | ✓  |
| `GET /admin/jobs` + `/admin/jobs/{id}`    | ✗    | ✓  |

---

## Pre-flight

### 1. Set your API keys
Edit `.env` at the repo root:

```bash
ANTHROPIC_API_KEY=sk-ant-...     # required for any LLM call (transformations, outline, transcript, judge)
VOYAGE_API_KEY=...               # optional — stub returns zero vectors if unset
ELEVENLABS_API_KEY=...           # optional — stub returns silent WAVs if unset
```

After editing:
```bash
docker compose restart api worker
```

### 2. Confirm the stack is up
```bash
docker compose ps
# expect: curia-postgres (healthy), curia-api, curia-worker (all Up)

curl -s http://localhost:8000/health
# expect: {"status":"ok","db":true}
```

### 3. Set tokens as env vars for convenience
```bash
export USER_TOKEN="ck_9Dv8uQ995fVVvW6rFcPq56m6v-3cuKzbe43h6pWfuYk"
export QA_TOKEN="ck_-suG6R7nNH-WcwsB4Cf5o8uQd9dJpfDXGd-w2ohIgKc"
```

---

## User-facing flow (run this in Terminal 1)

### Verify identity
```bash
curl -s -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/me | python3 -m json.tool
# expect: {"id": "3d7e8e41-...", "email": "arihantbarjatyausa@gmail.com", "name": "arihunter"}
```

### See your KB (already seeded)
```bash
curl -s -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/me/kb | python3 -m json.tool
```

### Re-onboard interactively (optional — to update preferences)
```bash
docker compose exec api python scripts/onboard.py --user-id 3d7e8e41-8908-413c-beac-74c143cdd29c
```

### Ingest 3-5 articles
```bash
for url in \
  "https://en.wikipedia.org/wiki/Memory" \
  "https://www.paulgraham.com/maker.html" \
  "https://www.lesswrong.com/posts/9hzzcb6kzc6PT4Yqu/the-ground-of-optimization" \
; do
  curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
       -d "{\"url\":\"$url\"}" http://localhost:8000/sources \
    | python3 -m json.tool
done
```

Each returns `{id, status, job_id}`. The work happens in the worker — poll until ready:

```bash
# Watch source statuses
watch -n 2 'curl -s -H "Authorization: Bearer '$USER_TOKEN'" http://localhost:8000/sources \
  | python3 -c "import sys,json; [print(r[\"status\"], r[\"title\"][:60]) for r in json.load(sys.stdin)]"'
```

A source moves through `queued → scraping → transforming → embedding → ready` over ~30–60s (faster with API keys, slower with stubs).

### Inspect one source's insights
```bash
SID="<paste source id>"
curl -s -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/sources/$SID | python3 -m json.tool
```

You should see all 7 transformation outputs (`summary`, `metadata`, `key_insights`, `human_stakes`, `core_tensions`, `counterpoints`, `examples`).

### Generate ideas across your archive
Need at least ~3 ingested sources for clustering to do meaningful work.

```bash
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/ideas/generate
# returns: {"job_id": "...", "status": "queued"}

# After ~30s
curl -s -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/ideas | python3 -m json.tool
```

### Generate an episode
Pick a `show_idea_id` from the list above (or skip it to use the selector against your archive):

```bash
IDEA_ID="<paste idea id>"
curl -s -X POST -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
     -d "{\"show_name\":\"clarity_engine\",\"show_idea_id\":\"$IDEA_ID\"}" \
     http://localhost:8000/episodes
# returns: {"id": "...", "status": "queued", "job_id": "..."}
```

Watch its progress (status moves through `selecting → outlining → transcribing → synthesizing → ready`):

```bash
EPISODE_ID="<paste episode id>"
watch -n 3 'curl -s -H "Authorization: Bearer '$USER_TOKEN'" http://localhost:8000/episodes/'$EPISODE_ID' \
  | python3 -c "import sys,json; e=json.load(sys.stdin); print(e[\"status\"], e.get(\"title\",\"\"), \"score:\", e.get(\"quality_score\"))"'
```

### Read the judged details
```bash
curl -s -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/episodes/$EPISODE_ID | python3 -m json.tool
# Look for: quality_score, quality_feedback, quality_violations, regenerated
```

### Download the audio
```bash
curl -s -H "Authorization: Bearer $USER_TOKEN" -o episode.mp3 http://localhost:8000/episodes/$EPISODE_ID/audio
open episode.mp3   # or: ffplay episode.mp3
```

(With ElevenLabs stub: ~1 second of silence per transcript line. With a real key: actual audio.)

---

## User self-service — view your own rubric

Transparency feature. The user can see exactly what the LLM judge sees when scoring their episodes.

```bash
# Render the rubric I'm being scored against (transcript task)
curl -s -H "Authorization: Bearer $USER_TOKEN" \
     http://localhost:8000/me/rubric/transcript \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['judge_prompt'])"

# Same for outline task
curl -s -H "Authorization: Bearer $USER_TOKEN" http://localhost:8000/me/rubric/outline
```

Useful when an episode scored poorly: "what was it being judged on?" Edit your KB → the rubric changes immediately.

---

## QA flow (run this in Terminal 2 — admin / inspection)

### List all users
```bash
curl -s -H "Authorization: Bearer $QA_TOKEN" http://localhost:8000/admin/users | python3 -m json.tool
# Returns id, email, name, role, created_at, has_kb for every user
```

### View a specific user's profile + KB + rubric
```bash
ARIHUNTER_ID="3d7e8e41-8908-413c-beac-74c143cdd29c"

# Profile
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/users/$ARIHUNTER_ID | python3 -m json.tool

# Their knowledge bank
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/users/$ARIHUNTER_ID/kb | python3 -m json.tool

# The rubric for them — single most useful debug tool
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/users/$ARIHUNTER_ID/rubric/transcript \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['judge_prompt'])"
```

### Inspect a user's pipeline state
```bash
# All sources
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/users/$ARIHUNTER_ID/sources | python3 -m json.tool

# All episodes (with quality_score)
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/users/$ARIHUNTER_ID/episodes | python3 -m json.tool
```

### Watch the queue across all users
```bash
# Recent jobs (any user)
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/jobs | python3 -m json.tool

# Just failed ones
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     "http://localhost:8000/admin/jobs?status=failed" | python3 -m json.tool

# Specific job's full payload + last_error
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/jobs/$JOB_ID | python3 -m json.tool
```

### Verify role gate (regular user can't hit /admin)
```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $USER_TOKEN" \
     http://localhost:8000/admin/users
# expect: 403
```

### Comparing personalization across users
The QA user starts with no KB. Seed it with deliberately different prefs, then verify the rubric they'd be scored against is genuinely different from arihunter's:

```bash
# Seed QA's KB (or run scripts/onboard.py interactively)
curl -s -X PUT -H "Authorization: Bearer $QA_TOKEN" -H "Content-Type: application/json" -d '{
  "identity": {"name": "qa"},
  "interests": {"topics": ["productivity"], "current_obsession": "shipping fast"},
  "preferences": {
    "preferred_length_minutes": 5,
    "preferred_formats": ["momentum_loop"],
    "preferred_tone": "punchy",
    "tolerates_ambiguity": "low",
    "novelty_appetite": 0.0
  },
  "dislikes": {"themes": ["philosophy"], "tones": ["dispassionate"]}
}' http://localhost:8000/me/kb

# Then via QA's *admin* view, render rubrics for both users side-by-side
curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/users/3d7e8e41-8908-413c-beac-74c143cdd29c/rubric/transcript \
  | python3 -c "import sys, json; p=json.load(sys.stdin)['judge_prompt']; print('--- ARIHUNTER ---'); print(p[p.find('USER PREFERENCES'):p.find('SCORING')])"

curl -s -H "Authorization: Bearer $QA_TOKEN" \
     http://localhost:8000/admin/users/4ed32e1d-b75d-41b2-899a-a7d2a935bfc5/rubric/transcript \
  | python3 -c "import sys, json; p=json.load(sys.stdin)['judge_prompt']; print('--- QA ---'); print(p[p.find('USER PREFERENCES'):p.find('SCORING')])"
```

The two USER PREFERENCES sections will be visibly different — same code path, different rubric per user.

---

## Admin / inspection (the QA-helper toolkit)

These don't need a token — they go around the API.

### Watch logs in real time
```bash
docker compose logs -f api worker
```

### Peek at the Postgres state
```bash
# Counts
docker exec curia-postgres psql -U curia -d curia -c "
  SELECT
    (SELECT COUNT(*) FROM users) AS users,
    (SELECT COUNT(*) FROM source) AS sources,
    (SELECT COUNT(*) FROM source_insight) AS insights,
    (SELECT COUNT(*) FROM source_embedding) AS chunks,
    (SELECT COUNT(*) FROM show_idea) AS ideas,
    (SELECT COUNT(*) FROM episode) AS episodes,
    (SELECT COUNT(*) FROM jobs) AS jobs;"

# Source statuses
docker exec curia-postgres psql -U curia -d curia -c "
  SELECT status, COUNT(*) FROM source GROUP BY status;"

# Episode statuses + quality scores
docker exec curia-postgres psql -U curia -d curia -c "
  SELECT id, status, title, quality_score, regenerated,
         LEFT(quality_feedback, 60) AS feedback_preview
  FROM episode ORDER BY created_at DESC LIMIT 5;"

# Job dispatch history
docker exec curia-postgres psql -U curia -d curia -c "
  SELECT id, type, status, attempts, LEFT(last_error, 60) AS error_preview
  FROM jobs ORDER BY created_at DESC LIMIT 10;"
```

### Reset everything (nuke + reapply)
```bash
docker compose down -v        # -v drops the postgres volume
docker compose up -d postgres
docker compose run --rm api alembic upgrade head
docker compose up -d api worker
docker compose exec api python scripts/create_user.py --email arihantbarjatyausa@gmail.com --name arihunter
docker compose exec api python scripts/create_user.py --email qa@curia.local --name qa
# new tokens — update this doc / exports
```

### Provision more users
```bash
docker compose exec api python scripts/create_user.py --email someone@example.com --name someone
# prints user_id + token
```

### Drop a user's data without dropping the user
```bash
docker exec curia-postgres psql -U curia -d curia -c "
  DELETE FROM source WHERE user_id = '<user_id>';
  DELETE FROM show_idea WHERE user_id = '<user_id>';
  DELETE FROM episode WHERE user_id = '<user_id>';
  DELETE FROM jobs WHERE user_id = '<user_id>';"
```

### Re-run a stuck job
```bash
docker exec curia-postgres psql -U curia -d curia -c "
  UPDATE jobs SET status='queued', locked_at=NULL, locked_by=NULL
  WHERE status='running' AND locked_at < now() - interval '15 min';"
# Worker picks it up on its next dispatch cycle.
```

### Render a judge prompt for inspection (without running it)
```bash
docker compose exec api python -c "
import asyncio
from core.kb import load_kb
from optimization.rubrics import generate_judge_prompt

async def main():
    kb = await load_kb('3d7e8e41-8908-413c-beac-74c143cdd29c')
    print(generate_judge_prompt('transcript', kb, output='<TRANSCRIPT>'))

asyncio.run(main())"
```

This is the **single most useful QA tool** — it shows you exactly what the LLM judge sees for a given user. If output quality looks off, render this and inspect the rubric.

---

## What's expected to fail without API keys

The pipeline runs with stubs but specific things degrade:

| Missing key | What still works | What degrades |
|---|---|---|
| no `ANTHROPIC_API_KEY` | nothing (LLMs are load-bearing) | source ingestion fails after scrape; idea gen fails; episode gen fails |
| no `VOYAGE_API_KEY` | full pipeline runs | embeddings are zero vectors → cluster scores meaningless → idea generation produces 1 cluster of all sources |
| no `ELEVENLABS_API_KEY` | full pipeline runs | audio is 1 second of silence per transcript line (~50s total silent MP3) |

So `ANTHROPIC_API_KEY` is required to test anything end-to-end. Other two are optional.

---

## Cheat sheet — most useful commands

```bash
# Health
curl -s http://localhost:8000/health

# Logs
docker compose logs -f api worker

# Live view of source statuses
watch -n 2 "docker exec curia-postgres psql -U curia -d curia -c 'SELECT status, COUNT(*) FROM source GROUP BY status;'"

# Live view of jobs
watch -n 2 "docker exec curia-postgres psql -U curia -d curia -c 'SELECT type, status, COUNT(*) FROM jobs GROUP BY type, status;'"

# Stop everything
docker compose down

# Stop + nuke data
docker compose down -v
```

---

## Troubleshooting

**`docker compose exec api ...` says "service not running"**
→ `docker compose up -d api worker`

**Source stuck in `transforming`**
→ Likely missing or invalid `ANTHROPIC_API_KEY`. Check worker logs:
`docker compose logs --tail=50 worker`

**Job in `failed` status**
→ `docker exec curia-postgres psql -U curia -d curia -c "SELECT type, last_error FROM jobs WHERE status='failed' ORDER BY updated_at DESC LIMIT 3;"`

**Need to swap a model on the fly**
→ Edit `config/models.yaml` (it's bind-mounted into the containers — no rebuild needed). Restart api+worker to pick up: `docker compose restart api worker`.
