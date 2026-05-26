#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/e2e_test.sh — End-to-end pipeline test
#
# Tests: add source → ingest → generate episode → R2 upload → presigned audio
#
# Usage:
#   ./scripts/e2e_test.sh <source_url> [api_url] [firebase_token]
#
# Examples:
#   ./scripts/e2e_test.sh "https://eugeneyan.com/writing/qa-evals/"
#   ./scripts/e2e_test.sh "https://example.com/article" "https://my-api.railway.app"
#
# If api_url is omitted, uses CURIA_API_URL env var or defaults to prod.
# If firebase_token is omitted, uses CURIA_TEST_TOKEN env var.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SOURCE_URL="${1:?Usage: $0 <source_url> [api_url] [token]}"
API="${2:-${CURIA_API_URL:-https://curia-backend-production.up.railway.app}}"
TOKEN="${3:-${CURIA_TEST_TOKEN:-}}"

if [ -z "$TOKEN" ]; then
  echo -e "${RED}✗ No Firebase token. Pass as arg 3 or set CURIA_TEST_TOKEN.${NC}"
  exit 1
fi

AUTH="Authorization: Bearer $TOKEN"
PASS=0
FAIL=0
START_TIME=$(date +%s)

step() { echo -e "\n${CYAN}── $1 ──${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}✗ $1${NC}"; FAIL=$((FAIL+1)); }
info() { echo -e "${YELLOW}  $1${NC}"; }

# ── 0. Health check ──────────────────────────────────────────────────────────
step "Health check"
HEALTH=$(curl -sf "$API/health" 2>/dev/null || echo '{"status":"down"}')
DB_OK=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('db',False))" 2>/dev/null)
if [ "$DB_OK" = "True" ]; then ok "API healthy, DB connected"; else fail "API unhealthy: $HEALTH"; exit 1; fi

# ── 1. Auth check ────────────────────────────────────────────────────────────
step "Auth check"
ME=$(curl -sf -H "$AUTH" "$API/me" 2>/dev/null || echo '{}')
EMAIL=$(echo "$ME" | python3 -c "import sys,json; print(json.load(sys.stdin).get('email',''))" 2>/dev/null)
if [ -n "$EMAIL" ]; then ok "Authenticated as $EMAIL"; else fail "Auth failed — token may be expired"; exit 1; fi

# ── 2. Add source ────────────────────────────────────────────────────────────
step "Add source: $SOURCE_URL"
SRC_RESP=$(curl -sf -X POST "$API/sources" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"url\": \"$SOURCE_URL\", \"auto_generate\": false}" 2>/dev/null || echo '{}')
SOURCE_ID=$(echo "$SRC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -n "$SOURCE_ID" ] && [ "$SOURCE_ID" != "None" ]; then
  ok "Source created: $SOURCE_ID"
else
  fail "Source creation failed: $SRC_RESP"
  exit 1
fi

# ── 3. Poll ingestion ────────────────────────────────────────────────────────
step "Waiting for ingestion"
INGEST_START=$(date +%s)
INGEST_TIMEOUT=120
while true; do
  ELAPSED=$(( $(date +%s) - INGEST_START ))
  if [ "$ELAPSED" -gt "$INGEST_TIMEOUT" ]; then
    fail "Ingestion timed out after ${INGEST_TIMEOUT}s"
    exit 1
  fi
  SRC_STATUS=$(curl -sf -H "$AUTH" "$API/sources/$SOURCE_ID" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "error")
  info "source status=$SRC_STATUS (${ELAPSED}s)"
  if [ "$SRC_STATUS" = "ready" ]; then ok "Ingestion complete (${ELAPSED}s)"; break; fi
  if [ "$SRC_STATUS" = "failed" ]; then fail "Ingestion failed"; exit 1; fi
  sleep 10
done

# ── 4. Snapshot existing episode IDs ─────────────────────────────────────────
EXISTING_IDS=$(curl -sf -H "$AUTH" "$API/episodes?limit=50" 2>/dev/null \
  | python3 -c "import sys,json; print(' '.join(e['id'] for e in json.load(sys.stdin)))" 2>/dev/null || echo "")

# ── 5. Trigger episode generation ────────────────────────────────────────────
step "Triggering generate_from_source"
GEN_RESP=$(curl -sf -X POST "$API/generate-from-source" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"source_id\": \"$SOURCE_ID\", \"standalone\": true}" 2>/dev/null || echo '{}')
JOB_ID=$(echo "$GEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
if [ -n "$JOB_ID" ] && [ "$JOB_ID" != "None" ]; then
  ok "Job enqueued: $JOB_ID"
else
  fail "generate_from_source failed: $GEN_RESP"
  exit 1
fi

# ── 6. Poll for NEW episode ──────────────────────────────────────────────────
step "Waiting for episode generation"
GEN_START=$(date +%s)
GEN_TIMEOUT=600
EPISODE_ID=""
while true; do
  ELAPSED=$(( $(date +%s) - GEN_START ))
  if [ "$ELAPSED" -gt "$GEN_TIMEOUT" ]; then
    fail "Episode generation timed out after ${GEN_TIMEOUT}s"
    exit 1
  fi

  EP_DATA=$(curl -sf -H "$AUTH" "$API/episodes?limit=10" 2>/dev/null || echo '[]')
  # Find episode that wasn't in the snapshot
  MATCH=$(echo "$EP_DATA" | python3 -c "
import sys,json
existing=set('$EXISTING_IDS'.split())
eps=json.load(sys.stdin)
for e in eps:
    if e['id'] not in existing:
        print(f'{e[\"id\"]}|{e[\"status\"]}|{e.get(\"title\") or \"(generating)\"}')
        break
" 2>/dev/null)

  if [ -z "$MATCH" ]; then
    info "new episode not yet created (${ELAPSED}s)"
    sleep 15
    continue
  fi

  EP_ID=$(echo "$MATCH" | cut -d'|' -f1)
  EP_STATUS=$(echo "$MATCH" | cut -d'|' -f2)
  EP_TITLE=$(echo "$MATCH" | cut -d'|' -f3)
  info "episode=$EP_STATUS — $EP_TITLE (${ELAPSED}s)"

  if [ "$EP_STATUS" = "ready" ]; then
    EPISODE_ID="$EP_ID"
    ok "Episode ready (${ELAPSED}s): $EP_TITLE"
    break
  fi
  if [ "$EP_STATUS" = "failed" ]; then
    EPISODE_ID="$EP_ID"
    fail "Episode failed"
    curl -sf -H "$AUTH" "$API/episodes/$EP_ID" 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  error: {d.get(\"error\",\"unknown\")}')" 2>/dev/null
    exit 1
  fi
  sleep 15
done

# ── 7. Check episode detail ──────────────────────────────────────────────────
step "Checking episode detail"
DETAIL=$(curl -sf -H "$AUTH" "$API/episodes/$EPISODE_ID" 2>/dev/null || echo '{}')
echo "$DETAIL" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for k in ('id','status','title','audio_path'):
    print(f'  {k}: {d.get(k, \"NULL\")}')
for k,v in d.items():
    if 'audio' in k.lower() and k != 'audio_path':
        print(f'  {k}: {str(v)[:80] if v else \"NULL\"}')
" 2>/dev/null

# ── 8. Test audio endpoint ───────────────────────────────────────────────────
step "Testing audio endpoint"
AUDIO_RESP=$(curl -sf -w '\n%{http_code}' -H "$AUTH" "$API/episodes/$EPISODE_ID/audio" 2>/dev/null || echo -e '\n000')
AUDIO_CODE=$(echo "$AUDIO_RESP" | tail -1)
AUDIO_BODY=$(echo "$AUDIO_RESP" | head -1)

if [ "$AUDIO_CODE" = "200" ]; then
  HAS_URL=$(echo "$AUDIO_BODY" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    url=d.get('url','')
    if 'X-Amz-Signature' in url:
        print('presigned')
    elif url:
        print('url')
    else:
        print('no_url')
except: print('not_json')
" 2>/dev/null)
  if [ "$HAS_URL" = "presigned" ]; then
    ok "Audio endpoint returned presigned R2 URL"
    # Try downloading the first few bytes
    PRESIGNED=$(echo "$AUDIO_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['url'])" 2>/dev/null)
    DL_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -r 0-1023 "$PRESIGNED" 2>/dev/null || echo "000")
    if [ "$DL_CODE" = "200" ] || [ "$DL_CODE" = "206" ]; then
      ok "Audio downloadable from R2 (HTTP $DL_CODE)"
    else
      fail "Audio download from R2 failed (HTTP $DL_CODE)"
    fi
  else
    info "Audio returned but not a presigned URL (type=$HAS_URL)"
  fi
elif [ "$AUDIO_CODE" = "410" ]; then
  fail "Audio file missing on disk (no R2 upload — check CURIA_STORAGE_BACKEND on worker)"
elif [ "$AUDIO_CODE" = "409" ]; then
  fail "Episode not ready"
else
  fail "Audio endpoint returned HTTP $AUDIO_CODE"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
TOTAL_TIME=$(( $(date +%s) - START_TIME ))
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}✓ $PASS passed${NC}  ${RED}✗ $FAIL failed${NC}  ⏱ ${TOTAL_TIME}s total"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
