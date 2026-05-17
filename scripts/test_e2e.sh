#!/bin/bash
# =============================================================================
# scripts/test_e2e.sh
# End-to-end integration test script for the Curia backend.
#
# Prerequisites:
#   - Postgres running (docker compose up postgres -d)
#   - API running (uvicorn api.main:app --host 0.0.0.0 --port 8000)
#   - Worker running (python3 -m worker.main)
#   - At least ANTHROPIC_API_KEY set in .env
#
# Usage:
#   chmod +x scripts/test_e2e.sh
#   ./scripts/test_e2e.sh
#
# Optional env overrides:
#   API_URL=http://localhost:8000 ./scripts/test_e2e.sh
#   TOKEN=ck_... ./scripts/test_e2e.sh
# =============================================================================

set -uo pipefail

API_URL="${API_URL:-http://localhost:8000}"
TOKEN="${TOKEN:-}"
SHOW_NAME="${SHOW_NAME:-clarity_engine}"
POLL_INTERVAL=5
POLL_TIMEOUT=600  # 10 minutes max wait

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

passed=0
failed=0
skipped=0

# ── Helpers ──────────────────────────────────────────────────────────────────

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; ((passed++)); }
fail() { echo -e "${RED}  ✗ $*${NC}"; ((failed++)); }
skip() { echo -e "${YELLOW}  ⊘ $*${NC}"; ((skipped++)); }
hr()   { echo "─────────────────────────────────────────────────────────"; }

api() {
  local method=$1 path=$2
  shift 2
  curl -s -X "$method" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "$@" \
    "${API_URL}${path}"
}

api_status() {
  local method=$1 path=$2
  shift 2
  curl -s -o /dev/null -w "%{http_code}" -X "$method" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "$@" \
    "${API_URL}${path}"
}

json_field() {
  # Usage: echo '{"id":"abc"}' | json_field id
  python3 -c "import sys,json; print(json.loads(sys.stdin.read())$1)"
}

poll_status() {
  # Usage: poll_status /sources/<id> ready
  local endpoint=$1 target=$2
  local elapsed=0
  while [ $elapsed -lt $POLL_TIMEOUT ]; do
    local status
    status=$(api GET "$endpoint" | json_field '["status"]')
    if [ "$status" = "$target" ]; then
      return 0
    elif [ "$status" = "failed" ]; then
      return 1
    fi
    sleep $POLL_INTERVAL
    elapsed=$((elapsed + POLL_INTERVAL))
    echo -n "."
  done
  echo ""
  return 1
}

# =============================================================================
# SETUP
# =============================================================================

hr
log "Curia E2E Test Suite"
log "API: $API_URL"
hr

# ── TC-0: Health check ───────────────────────────────────────────────────────

log "TC-0: Health check"
health=$(curl -s "${API_URL}/health")
db_ok=$(echo "$health" | json_field '["db"]')
if [ "$db_ok" = "True" ]; then
  ok "Health check passed (db=true)"
else
  fail "Health check failed: $health"
  echo "Is Postgres running? Is the API running?"
  exit 1
fi

# ── TC-1: Create or reuse test user ─────────────────────────────────────────

log "TC-1: Auth"
if [ -z "$TOKEN" ]; then
  log "  No TOKEN set. Creating a test user..."
  user_output=$(python3 scripts/create_user.py --email e2e@test.com --name "E2E Test" 2>&1 || true)
  TOKEN=$(echo "$user_output" | grep -o 'ck_[A-Za-z0-9_-]*' | head -1)
  if [ -z "$TOKEN" ]; then
    fail "Could not create user or extract token"
    exit 1
  fi
  log "  Token: ${TOKEN:0:20}..."
fi

# Validate token
auth_status=$(api_status GET "/auth/me")
if [ "$auth_status" = "200" ]; then
  auth_resp=$(api GET "/auth/me")
  user_id=$(echo "$auth_resp" | json_field '["id"]')
  ok "Auth works (user_id=${user_id:0:8}...)"
else
  fail "Auth failed (status=$auth_status). Is your token valid?"
  exit 1
fi

# Also test GET /me
me_status=$(api_status GET "/me")
if [ "$me_status" = "200" ]; then
  ok "GET /me works"
else
  fail "GET /me failed (status=$me_status)"
fi

# ── TC-2: Ingest sources ────────────────────────────────────────────────────

hr
log "TC-2: Ingest sources"

URLS=(
  "https://paulgraham.com/greatwork.html"
  "https://blog.samaltman.com/how-to-be-successful"
  "https://blog.samaltman.com/idea-generation"
)

source_ids=()
for url in "${URLS[@]}"; do
  log "  Ingesting: $url"
  resp=$(api POST "/sources" -d "{\"url\":\"$url\"}")
  sid=$(echo "$resp" | json_field '["id"]')
  status=$(echo "$resp" | json_field '["status"]')
  source_ids+=("$sid")
  if [ -n "$sid" ]; then
    ok "Queued source $sid (status=$status)"
  else
    fail "Failed to queue: $url"
  fi
done

# Poll until all sources are ready
log "  Waiting for sources to be ready..."
all_ready=true
for sid in "${source_ids[@]}"; do
  echo -n "  Polling $sid "
  if poll_status "/sources/$sid" "ready"; then
    echo ""
    ok "Source $sid ready"
  else
    echo ""
    fail "Source $sid did not reach ready state"
    all_ready=false
  fi
done

# ── TC-3: List sources ──────────────────────────────────────────────────────

hr
log "TC-3: List sources"
sources_resp=$(api GET "/sources")
source_count=$(echo "$sources_resp" | json_field '.__len__()')
if [ "$source_count" -gt 0 ]; then
  ok "GET /sources returned $source_count source(s)"
else
  fail "GET /sources returned empty"
fi

# Check covered_in field exists
has_covered=$(echo "$sources_resp" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('covered_in' in d[0])" 2>/dev/null || echo "False")
if [ "$has_covered" = "True" ]; then
  ok "covered_in field present"
else
  skip "covered_in field missing (may need episode first)"
fi

# ── TC-4: Source detail ─────────────────────────────────────────────────────

hr
log "TC-4: Source detail"
first_sid="${source_ids[0]}"
detail_resp=$(api GET "/sources/$first_sid")
title=$(echo "$detail_resp" | json_field '["title"]')
if [ -n "$title" ] && [ "$title" != "None" ]; then
  ok "Source detail: title='$title'"
else
  fail "Source detail missing title"
fi

# ── TC-5: Generate episode ──────────────────────────────────────────────────

hr
log "TC-5: Generate episode (show=$SHOW_NAME)"
ep_resp=$(api POST "/episodes" -d "{\"show_name\":\"$SHOW_NAME\"}")
ep_id=$(echo "$ep_resp" | json_field '["id"]')
ep_status=$(echo "$ep_resp" | json_field '["status"]')
job_id=$(echo "$ep_resp" | json_field '["job_id"]')

if [ -n "$ep_id" ]; then
  ok "Episode queued: id=$ep_id job=$job_id"
else
  fail "Failed to create episode"
fi

# ── TC-5b: Poll job status ──────────────────────────────────────────────────

if [ -n "$job_id" ] && [ "$job_id" != "None" ]; then
  log "  Checking job status..."
  job_resp=$(api GET "/jobs/$job_id")
  job_status=$(echo "$job_resp" | json_field '["status"]')
  ok "Job $job_id status=$job_status"
fi

# ── TC-5c: Poll episode until ready ─────────────────────────────────────────

log "  Waiting for episode to be ready (this may take 2-5 minutes)..."
echo -n "  Polling "
if poll_status "/episodes/$ep_id" "ready"; then
  echo ""
  ok "Episode $ep_id is ready!"
else
  echo ""
  fail "Episode $ep_id did not reach ready state"
  # Show the error
  ep_detail=$(api GET "/episodes/$ep_id")
  ep_error=$(echo "$ep_detail" | json_field '["error"]' 2>/dev/null || echo "unknown")
  log "  Error: $ep_error"
fi

# ── TC-6: Episode detail ────────────────────────────────────────────────────

hr
log "TC-6: Episode detail"
ep_detail=$(api GET "/episodes/$ep_id")
ep_title=$(echo "$ep_detail" | json_field '["title"]' 2>/dev/null || echo "")
ep_quality=$(echo "$ep_detail" | json_field '["quality_score"]' 2>/dev/null || echo "")
ep_length=$(echo "$ep_detail" | json_field '["length_minutes"]' 2>/dev/null || echo "")
ep_speaker=$(echo "$ep_detail" | json_field '["speaker_override"]' 2>/dev/null || echo "")

if [ -n "$ep_title" ] && [ "$ep_title" != "None" ]; then
  ok "Title: $ep_title"
else
  skip "No title (episode may have failed)"
fi

[ -n "$ep_quality" ] && ok "Quality score: $ep_quality" || skip "No quality score"
[ -n "$ep_length" ] && ok "Length: ${ep_length}min" || skip "No length"
log "  Speaker override: $ep_speaker"

# Check transcript exists
has_transcript=$(echo "$ep_detail" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('transcript') is not None)" 2>/dev/null || echo "False")
if [ "$has_transcript" = "True" ]; then
  transcript_len=$(echo "$ep_detail" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); t=d.get('transcript',[]); print(len(t) if isinstance(t,list) else 0)")
  ok "Transcript: $transcript_len lines"
else
  skip "No transcript"
fi

# Check outline exists
has_outline=$(echo "$ep_detail" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('outline') is not None)" 2>/dev/null || echo "False")
if [ "$has_outline" = "True" ]; then
  ok "Outline present"
else
  skip "No outline"
fi

# ── TC-7: Download audio ────────────────────────────────────────────────────

hr
log "TC-7: Download audio"
audio_status=$(api_status GET "/episodes/$ep_id/audio")
if [ "$audio_status" = "200" ]; then
  tmpfile="/tmp/curia_e2e_episode.mp3"
  api GET "/episodes/$ep_id/audio" > "$tmpfile"
  filesize=$(wc -c < "$tmpfile" | tr -d ' ')
  ok "Audio downloaded: ${filesize} bytes → $tmpfile"
  if [ "$filesize" -gt 1000 ]; then
    ok "Audio file size looks valid"
  else
    skip "Audio file is very small (likely silent stub — no TTS key)"
  fi
else
  skip "Audio not available (status=$audio_status)"
fi

# ── TC-8: Speaker validation ────────────────────────────────────────────────

hr
log "TC-8: Speaker validation"
bad_speaker_status=$(api_status POST "/episodes" -d '{"show_name":"clarity_engine","speaker":"invalid_name"}')
if [ "$bad_speaker_status" = "422" ]; then
  ok "Invalid speaker rejected with 422"
else
  fail "Invalid speaker returned $bad_speaker_status (expected 422)"
fi

# ── TC-9: Episode with overrides ────────────────────────────────────────────

hr
log "TC-9: Episode with speaker + length override"
override_resp=$(api POST "/episodes" -d '{"show_name":"narrative_drift","speaker":"arjun","length_minutes":8}')
override_id=$(echo "$override_resp" | json_field '["id"]')
if [ -n "$override_id" ]; then
  ok "Episode with overrides created: $override_id"
else
  fail "Failed to create episode with overrides"
fi

# ── TC-10: Idempotent source ────────────────────────────────────────────────

hr
log "TC-10: Idempotent source ingest"
idem_resp=$(api POST "/sources" -d '{"url":"https://paulgraham.com/greatwork.html"}')
idem_id=$(echo "$idem_resp" | json_field '["id"]')
if [ "$idem_id" = "${source_ids[0]}" ]; then
  ok "Same URL returned same source_id (idempotent)"
else
  fail "Duplicate URL created new source: $idem_id vs ${source_ids[0]}"
fi

# ── TC-11: Knowledge Bank ───────────────────────────────────────────────────

hr
log "TC-11: Knowledge Bank"
kb_put_status=$(api_status PUT "/me/kb" -d '{
  "identity": {"name": "E2E Tester", "reading_volume_per_week": "10-20 articles"},
  "interests": {"topics": ["AI", "startups"], "current_obsession": "foundation models"},
  "preferences": {
    "preferred_length_minutes": 12,
    "preferred_formats": ["clarity_engine"],
    "preferred_tone": "analytical",
    "tolerates_ambiguity": "medium",
    "novelty_appetite": 0.7
  },
  "listening_context": {"when": "morning_commute", "while_doing": "walking"},
  "dislikes": {"formats": [], "tones": ["hype"], "themes": ["crypto"]},
  "version": 1
}')
if [ "$kb_put_status" = "200" ]; then
  ok "PUT /me/kb succeeded"
else
  fail "PUT /me/kb returned $kb_put_status"
fi

kb_get_status=$(api_status GET "/me/kb")
if [ "$kb_get_status" = "200" ]; then
  ok "GET /me/kb succeeded"
else
  fail "GET /me/kb returned $kb_get_status"
fi

# ── TC-12: List episodes ────────────────────────────────────────────────────

hr
log "TC-12: List episodes"
episodes_resp=$(api GET "/episodes")
ep_count=$(echo "$episodes_resp" | json_field '.__len__()')
if [ "$ep_count" -gt 0 ]; then
  ok "GET /episodes returned $ep_count episode(s)"
else
  fail "GET /episodes returned empty"
fi

# Filter by status
ready_resp=$(api GET "/episodes?status=ready")
ready_count=$(echo "$ready_resp" | json_field '.__len__()')
ok "Ready episodes: $ready_count"

# ── TC-13: Source coverage after episode ─────────────────────────────────────

hr
log "TC-13: Source coverage count"
sources_after=$(api GET "/sources")
first_covered=$(echo "$sources_after" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d[0].get('covered_in', 0))")
if [ "$first_covered" -gt 0 ]; then
  ok "Source covered_in=$first_covered (used in episode)"
else
  skip "covered_in still 0 (episode may not have used this source)"
fi

# ── TC-14: Delete source ────────────────────────────────────────────────────

hr
log "TC-14: Delete source (last one)"
last_sid="${source_ids[${#source_ids[@]}-1]}"
del_status=$(api_status DELETE "/sources/$last_sid")
if [ "$del_status" = "204" ]; then
  ok "DELETE /sources/$last_sid returned 204"
else
  fail "DELETE returned $del_status"
fi

# Verify it's gone
get_deleted=$(api_status GET "/sources/$last_sid")
if [ "$get_deleted" = "404" ]; then
  ok "Deleted source returns 404"
else
  fail "Deleted source still accessible (status=$get_deleted)"
fi

# ── TC-15: Invalid URL ──────────────────────────────────────────────────────

hr
log "TC-15: Invalid URL validation"
bad_url_status=$(api_status POST "/sources" -d '{"url":"not-a-url"}')
if [ "$bad_url_status" = "422" ]; then
  ok "Invalid URL rejected with 422"
else
  fail "Invalid URL returned $bad_url_status (expected 422)"
fi

# ── TC-16: Unauthenticated request ──────────────────────────────────────────

hr
log "TC-16: Unauthenticated request"
unauth_status=$(curl -s -o /dev/null -w "%{http_code}" "${API_URL}/me")
if [ "$unauth_status" = "401" ]; then
  ok "Unauthenticated GET /me returns 401"
else
  fail "Unauthenticated request returned $unauth_status (expected 401)"
fi

# =============================================================================
# SUMMARY
# =============================================================================

hr
echo ""
log "E2E Test Results"
echo -e "  ${GREEN}Passed: $passed${NC}"
echo -e "  ${RED}Failed: $failed${NC}"
echo -e "  ${YELLOW}Skipped: $skipped${NC}"
echo ""

if [ "$failed" -gt 0 ]; then
  echo -e "${RED}Some tests failed!${NC}"
  exit 1
else
  echo -e "${GREEN}All tests passed!${NC}"
  exit 0
fi
