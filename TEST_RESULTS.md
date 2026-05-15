# Curia v2 — Test Results

Run started: 2026-05-12  
Tester: Claude (claude-sonnet-4-6) + Bhabani  
Environment: local (no Docker), edge-tts, no Voyage key

---

## Wave 1 — Foundation

### TC-1: Auth & Role Gates

| Sub-test | Result | Notes |
|----------|--------|-------|
| 1.1 Valid user token | PASS | `{"role":"user","id":"5112b35e..."}` |
| 1.2 Valid QA token | PASS | `{"role":"qa","id":"3836f5fc..."}` |
| 1.3 Missing token | PASS | 401 |
| 1.4 Garbage token | PASS | 401 |
| 1.5 User hits admin endpoint | PASS | 403 |
| 1.6 QA hits admin endpoint | PASS | 200 |
| 1.7 QA reads another user's KB | PASS | Returns full KB object |
| 1.8 Health check | PASS | `{"status":"ok","db":true}` |

**TC-1: ALL PASS**

---

### TC-2: Source Ingest — Happy Path

| Sub-test | Result | Notes |
|----------|--------|-------|
| 2.1 Submit a URL | PASS | `{id, status: "queued", job_id}` returned |
| 2.2 Status transitions | FAIL | Polling script crashed — JSONDecodeError on control characters in insight text. Source confirmed `ready` via direct GET. Status did transition correctly. |
| 2.3 All 7 insights present | FAIL | `human_stakes`, `core_tensions`, `counterpoints` are null on Wikipedia/Attention source |
| 2.4 Title extracted | PASS | `"title": "Attention"` |
| 2.5 Appears in source list | PASS | Found in list with status `ready` |
| 2.6 Submit same URL again | PASS | Returns same ID, `job_id: null` — idempotent |

**Issues found:**
- **[BUG]** TC-2.2: Polling inline script breaks when insight text contains control characters (newlines embedded in JSON). The source does reach `ready` — this is a test script issue, not a pipeline bug.
- **[BUG]** TC-2.3: 3 of 7 insight fields (`human_stakes`, `core_tensions`, `counterpoints`) are null. Not a crash — the LLM produced null for these. May be a prompt issue or short article. Wikipedia/Attention is a long article so this warrants investigation.

---

### TC-3: Source Ingest — Failure Modes

| Sub-test | Result | Notes |
|----------|--------|-------|
| 3.1 Invalid URL (not-a-url) | PASS | 422 with `url_parsing` error |
| 3.2 Missing URL field | PASS | 422 with `missing` field error |
| 3.3 Unreachable URL | FAIL | Status is `ready` not `failed`. Error message stored in `full_text`, not `error` column. `error` field is null. |
| 3.4 DELETE source | PASS | 204 on delete, 404 on subsequent GET |

**Issues found:**
- **[BUG]** TC-3.3: When a URL is unreachable, the pipeline still marks the source `ready` instead of `failed`. The error is stored in `full_text` ("Failed to extract content: Cannot connect to host...") but the `error` column is null and `status` is `ready`. This means the UX cannot distinguish a good source from a scrape-failed source by status alone. The LLM then generates placeholder insights like "The article content could not be retrieved due to a connection error" — misleading downstream.

---

## Wave 2 — Knowledge + Ideas

### TC-4: Knowledge Bank

| Sub-test | Result | Notes |
|----------|--------|-------|
| 4.1 GET /me/kb | PASS | Returns full UserKB structure |
| 4.2 PUT /me/kb with `user_kb` wrapper | FAIL | 422 `extra_forbidden` — route takes `UserKB` directly, not wrapped |
| 4.2 PUT /me/kb (corrected — direct UserKB) | FAIL | 422 `string_type` on `reading_volume_per_week` — field is `str`, not `int` |
| 4.2 PUT /me/kb (corrected — str value) | PASS | KB updated successfully |
| 4.3 GET /me/kb (verify persisted) | PASS | All fields persisted correctly |

**Issues found:**
- **[DOCS]** TC-4: `PUT /me/kb` takes the `UserKB` schema directly (not wrapped in `{"user_kb": ...}`). The TEST_PLAN.md uses the wrong wrapper. Test plan needs correcting.
- **[DOCS]** TC-4: `reading_volume_per_week` is a `str` field, not `int`. TEST_PLAN.md should document this. Unintuitive — field name implies a number.

---

### TC-5: Rubric — Personalization

| Sub-test | Result | Notes |
|----------|--------|-------|
| 5.1 GET /me/rubric/transcript | PASS | Returns judge prompt with `USER PREFERENCES` section |
| 5.2 KB values reflected in rubric | PASS | Tone `analytical`, length `10 min`, interests `memory/neuroscience/AI`, obsession `how attention works`, dislike `dispassionate` all present |

**TC-5: ALL PASS**

---

### TC-6: Idea Generation

| Sub-test | Result | Notes |
|----------|--------|-------|
| 6.1 POST /ideas/generate | PASS | `{job_id, status: "queued"}` |
| 6.2 GET /ideas — ideas exist | PASS | 2 ideas present (both `standalone` type — no clusters, expected with no Voyage key) |

**TC-6: PASS (degraded)** — no cluster ideas because Voyage embeddings not configured. Expected behaviour per known issues.

---

## Wave 3 — Episode Generation

*Episodes submitted. Worker processing sequentially. Results pending.*

### TC-7: Episode Generation — Happy Path

Episode ID: `c9f189c3-278b-48c1-90d3-3806d7909f05`  
Show: `clarity_engine` | Idea: memory reconstruction

| Sub-test | Result | Notes |
|----------|--------|-------|
| 7.1 Episode created (202) | PASS | `{id, status: "queued", job_id}` |
| 7.2 Outline generated | PASS | Worker logged outline with 6 segments before failure |
| 7.3 Transcript generated | PASS | 51 lines, judge score 0.00, re-roll triggered |
| 7.4 Judge + re-roll ran | PASS | Re-rolled once (score < threshold), then proceeded to synthesis |
| 7.5 Audio synthesis | FAIL | `edge_tts synthesis failed: Cannot connect to host speech.platform.bing.com:443 [Connection reset by peer]` |
| 7.6 Episode reaches ready | FAIL | Status `failed` — blocked by BUG-05 |
| 7.7 Audio downloadable | FAIL | No audio produced |

**Root cause:** edge-tts makes live HTTPS calls to `speech.platform.bing.com`. Microsoft is
resetting the connection — likely rate-limiting after rapid sequential calls during Wave 3
(10+ episodes submitted in quick succession).

---

### TC-12: Format Remixes

| Format | Episode ID | Result |
|--------|-----------|--------|
| narrative_drift | `e257d754-ac34-4a0e-999f-25064dc469bc` | FAIL — same edge_tts connection error |
| clarity_engine | `0ebad88f-f638-4179-84b8-834ac7480cfb` | FAIL — same edge_tts connection error |
| momentum_loop | `9121af02-3f97-4cd3-9644-50574839d0f7` | FAIL — synthesizing at time of check, likely same |
| exploration_engine | `58b50469-922a-4318-87e4-159e6fdf6720` | PENDING — still transcribing |

---

### TC-13: Direct Per-Episode Overrides

| Override | Episode ID | Result |
|----------|-----------|--------|
| length_minutes=5 | `49f934cc-9615-4ab2-90d3-5f655ea26634` | PENDING — queued |
| speaker=arjun | `b13c95ac-2363-43ef-be07-e2edf6f63af2` | PENDING — queued |
| length=8 + speaker=emeka | `5e2ceb0f-dd1e-4a18-a598-c31d365febe9` | PENDING — queued |
| length=1 (validation) | — | FAIL — returned 202, not 422. `ge=3` constraint not enforced. |
| length=60 (validation) | — | FAIL — returned 202, not 422. `le=30` constraint not enforced. |
| speaker=morgan (unknown) | `590d3438-e9a2-4ba0-9a13-e8b5170e745e` | PENDING — queued |

**Issues found:**
- **[BUG]** TC-13.6: `length_minutes` field constraints `ge=3, le=30` declared in `CreateEpisodeRequest` via `Field(ge=3, le=30)` but FastAPI is NOT enforcing them — both `length_minutes=1` and `length_minutes=60` return 202. The episodes are created and queued. Needs investigation — likely a Pydantic v2 / FastAPI version compatibility issue with `ge`/`le` on `Optional[int]`.

---

### TC-17: Failure States

| Sub-test | Result | Notes |
|----------|--------|-------|
| 17.1 Unknown show name | PENDING | Episode `14a74e9e` queued behind edge_tts backlog |
| 17.2 Unknown speaker | PENDING | Episode `590d3438` queued behind edge_tts backlog |
| 17.3 Bad episode doesn't block queue | PARTIAL | Bad episodes are queued; good episodes also queued — blocking is due to edge_tts failure rate, not job isolation failure |

---

## Wave 4 — Inspection

### TC-9: Admin / QA Inspection

| Sub-test | Result | Notes |
|----------|--------|-------|
| 9.1 Admin lists users | PASS | 3 users: qa, user, default |
| 9.2 Admin views specific user | PASS | Full user object returned |
| 9.3 Admin views user KB | PASS | KB fields correctly visible to QA role |

**TC-9: ALL PASS**

---

### TC-10: Multi-user Isolation

| Sub-test | Result | Notes |
|----------|--------|-------|
| 10.1 User only sees own episodes | PASS | 20 episodes, all belonging to user |
| 10.2 QA sees no user episodes via /episodes | PASS | 0 episodes returned for QA token |
| 10.3 User and QA see separate sources | PASS | user=5, qa=0 — no cross-contamination |
| 10.4 QA cannot fetch user episode by ID | PASS | 404 returned |

**TC-10: ALL PASS**

---

### TC-11: Edge Cases

| Sub-test | Result | Notes |
|----------|--------|-------|
| 11.1 Episode with no sources | SKIP | Requires fresh user; current user has 5 sources. Needs a dedicated test user with empty state. |
| 11.2 Ideas with < 3 sources — only standalones | PASS | All 4 ideas are `standalone` type (no clusters) — expected, no Voyage key |
| 11.6 Duplicate idea generation | PARTIAL | Two different job_ids created — dedup not at the job level. Need to wait for both jobs to complete and verify ideas list doesn't double. |

---

### TC-16: Source → Episode Lineage

*Blocked — no episodes have reached `ready` status due to edge_tts failures (BUG-05). Cannot verify `source_ids` on completed episodes.*

---

## Wave 4 — Improvement Notes (from TC-9/10/11)

- **TC-11.1 requires a dedicated test user** — add a `FRESH_USER_TOKEN` to the test setup with zero sources. Currently impossible to test "no sources" path without creating a new account.
- **TC-11.6 needs a wait step** — submitting two idea generation jobs and immediately checking idea count doesn't verify dedup. Need to poll until both jobs complete, then check.

---

## Observations & Improvement Notes

### Testing strategy improvements

1. **Control characters in JSON polling** — inline python one-liners break when API responses contain embedded newlines/tabs in text fields. Use `jq` or pipe through a robust JSON extractor instead of raw `python3 -c`. Add to agent prompt template.

2. **Worker is single-threaded** — all episode jobs queue behind each other. With 10+ episodes submitted in Wave 3, each takes 5–15 min. Total Wave 3 wall time: 2–3 hours sequentially. Strategy should note this and suggest batching episodes across separate test users to parallelize at the DB/worker level.

3. **TC-2.2 polling interval** — 8s polling interval is too short to capture intermediate states (scraping, transforming, embedding). By the time the second poll fires, the source was already `ready`. Recommend polling every 3s with a 60s max window to catch transitions.

4. **TEST_PLAN.md: PUT /me/kb schema is wrong** — the plan wraps the body in `{"user_kb": {...}}` but the route takes `UserKB` directly. This will cause TC-4 to fail for any agent running the plan verbatim.

5. **TEST_PLAN.md: reading_volume_per_week type** — documented as a number in examples but it's a `str` field. Should be `"10 articles"` not `10`.

### Bugs found (no code changes — logged only)

| ID     | Severity | Description                                                                                                                                                                                                                                                                                                                  |
| ------ | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| BUG-01 | Medium   | Failed URL scrape sets `status=ready` not `status=failed`. Error buried in `full_text`, `error` column is null. UX cannot distinguish a failed scrape from a successful one by status.                                                                                                                                       |
| BUG-02 | Low      | 3/7 insight fields null on Wikipedia/Attention source (`human_stakes`, `core_tensions`, `counterpoints`). LLM prompt may not be extracting all fields for long-form reference articles.                                                                                                                                      |
| BUG-03 | High     | `length_minutes` `ge=3, le=30` constraints declared on `CreateEpisodeRequest` but NOT enforced by FastAPI. Values of 1 and 60 both return 202. Likely Pydantic v2 `Optional[int]` + `Field(ge=, le=)` interaction.                                                                                                           |
| BUG-04 | Low      | TC-2.2 inline polling script breaks on control characters (embedded newlines) in JSON response text. Test script issue only, not a pipeline bug.                                                                                                                                                                             |
| BUG-05 | Critical | edge_tts synthesis fails with `Connection reset by peer` to `speech.platform.bing.com:443` when many episodes are synthesized in rapid succession. Microsoft is rate-limiting or blocking burst connections. All Wave 3 episodes that reached synthesis stage failed. This blocks TC-7, TC-12, TC-13, TC-16 from completing. |
