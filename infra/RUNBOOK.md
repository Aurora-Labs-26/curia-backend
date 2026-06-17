# AWS Migration Runbook — Railway → AWS (us-east-1)

> Living document. **Every resource created in the console gets recorded here** (name, settings,
> IDs) so the later Terraform pass is a translation job, not archaeology. Fill in `<>` values
> as they're created. Decisions + rationale live in `CHANGETHOUGHT.md`; this is the *what/how*.
>
> Target shape: ECS Fargate (api + worker) · ALB · RDS Postgres+pgvector · SQS (2 queues,
> Phase 4) · Secrets Manager · **audio on S3 (`curia-audio`, migrated from R2)** ·
> region **us-east-1**.

## Fill-in registry (record as created)

| Item | Value |
|---|---|
| AWS account id | `617341601034` (IAM user `arihant-admin`) |
| VPC id | `vpc-00f7a27c2ba2c9c64` ✅ |
| Public subnets (a/b) | `subnet-008042216c2a7a250` / `subnet-0ab47aad43e89b74c` ✅ |
| Private subnets (a/b) | `subnet-09486e011b065be50` / `subnet-01f57f28e54443b32` ✅ |
| IGW / NAT / EIP | `igw-09c19af824808b36a` / `nat-09ec94f3b1ecb67ba` / `eipalloc-06f5b64a8fb0dd632` ✅ |
| Route tables (pub/pri) | `rtb-083a902186be5c234` / `rtb-08f33cc62c5b5320b` ✅ |
| SG: alb / api / worker / rds | `sg-00e28b95bda656e99` / `sg-06f0ee61e6cdaf8fe` / `sg-0ec8c10ff15be4918` / `sg-090970688d9a143f4` ✅ |
| Temp RDS access rule | 5432 from `12.74.59.161/32` (laptop TCP egress — **delete at cutover**). ⚠️ Network is CGNAT: TCP egress IP ≠ HTTPS egress IP and may rotate; if laptop→RDS times out later, re-discover via the temp-world-rule + `SELECT inet_client_addr()` trick and update this rule. |
| RDS | `curia-db` · Postgres 16.11 · db.t4g.small · gp3 20→100GB · **available ✅** · `curia-db.c6jukswcg119.us-east-1.rds.amazonaws.com` · pgvector 0.8.0 enabled ✅ |
| Secrets created | `curia/db-master-password` · `curia/database-url` · `curia/anthropic-api-key` · `curia/openai-api-key` · `curia/firecrawl-api-key` · `curia/smallest-api-key` · `curia/firebase-service-account` (repaired escaped JSON, project `aurora-labs-73eca`) ✅ all |
| Prod env truths (from Railway export) | embeddings = **OpenAI text-embedding-3-small** (not Voyage); R2 bucket = `curia-storage`; `INTERNAL_SECRET` + `JINA_API_KEY` present on Railway but **unreferenced by v2.7-final code**; ⚠️ **no `HUME_API_KEY` anywhere** yet v2.7 binds all speakers → hume-octave (TTS would stub-silence on deploy — resolve before Phase 3); Railway `CURIA_ENV="production"` ≠ yaml key `prod` (harmless today — overrides equal defaults; ECS uses `prod` which matches) |
| DB master password | Secrets Manager `curia/db-master-password` ✅ |
| DB subnet group | `curia-db-subnets` (public subnets, temp public access) ✅ |
| ECR repo URI | `617341601034.dkr.ecr.us-east-1.amazonaws.com/curia` ✅ created |
| S3 audio bucket | `curia-audio` ✅ created, public access blocked |
| ALB DNS / domain | `curia-alb-23430048.us-east-1.elb.amazonaws.com` ✅ (HTTP :80 listener; 443+cert pending domain decision) |
| ALB / target group ARNs | `...loadbalancer/app/curia-alb/56b2ac3ea6270a4c` / `...targetgroup/curia-api-tg/3970159a0f5d7855` |
| IAM roles | `curia-ecs-execution-role` (+secrets read) · `curia-api-task-role` (S3 read) · `curia-worker-task-role` (S3 rw) ✅ |
| ECS | cluster `curia` ✅ · task defs `curia-api:1`, `curia-worker:1` ✅ · log groups `/ecs/curia-{api,worker}` (30d retention) ✅ |
| Services (2026-06-11) | `curia-api` desired=1 **LIVE** — `/health` → `{"status":"ok","db":true}` through the ALB ✅ · `curia-worker` desired=2 running ✅ (friend-testing mode) |
| Duplicate-push guard | All 40 copied `fcm_token`s **nulled on RDS** (2026-06-11) so the worker's APScheduler crons (05:30/14:30 UTC) can't double-push real users alongside Railway. Testers re-register tokens naturally on sign-in → they get real pushes from AWS. ⚠️ Cutover re-sync restores real tokens — by then Railway's worker must be stopped. |
| Testing mode | AWS stack = **disposable sandbox** until cutover: anything testers create on it (sources/episodes/progress) is overwritten by the final Railway re-sync. Test URL (HTTP): `http://curia-alb-23430048.us-east-1.elb.amazonaws.com` — iOS dev builds may need an ATS exception or the CloudFront-HTTPS fallback (config drafted, not created). |
| CloudFront (2026-06-11) | dist `EV5Q65103PXXD` → **`https://dotqnozbrkm2q.cloudfront.net`** = HTTPS for iOS, no domain needed. API passthrough: CachingDisabled + AllViewerExceptHostHeader (forwards Authorization), all HTTP methods, redirect-to-https, WebSocket-capable. Origin = ALB over HTTP:80. Audio still served direct from S3 presigned (not via this dist). Optional hardening later: restrict ALB SG to CloudFront managed prefix list. Custom domain later = add Alternate Domain Name + ACM cert to this same dist (no app rebuild if DNS-fronted). |
| SQS queues (2026-06-11) | `https://sqs.us-east-1.amazonaws.com/617341601034/curia-interactive` + `…/curia-background` (visibility 900s, long-poll 20s, maxReceive 3 → `…-dlq` each, DLQ retention 14d) ✅ · IAM: api=send, worker=send+consume ✅ |

---

## Phase 0 — Prep

1. **IAM + CLI**: IAM user w/ admin (or PowerUser) → access key → `aws configure`
   (region `us-east-1`, output `json`) → verify `aws sts get-caller-identity`.
2. **ECR repo**: `aws ecr create-repository --repository-name curia --region us-east-1`
3. **Railway env export**: dashboard → Variables → copy ALL (source of truth; local `.env`
   only has 4 vars). Map each to a secret/env in the task defs (`infra/ecs-task-def-*.json`).
4. Record account id above.

## Phase 1 — VPC, RDS, Secrets

### 1a. VPC (console → VPC → "Create VPC" → **VPC and more** wizard)
- Name `curia`, CIDR `10.0.0.0/16`, **2 AZs**, 2 public + 2 private subnets,
  **NAT gateway: 1 per AZ → choose "in 1 AZ"** (cost), VPC endpoints: **S3 Gateway** (free).
- The wizard creates route tables correctly (public→IGW, private→NAT). Record IDs above.

### 1b. Security groups (console → VPC → Security groups, all in the curia VPC)
| SG | Inbound | Notes |
|---|---|---|
| `curia-alb-sg` | 443 from 0.0.0.0/0 (+80 redirect) | internet-facing |
| `curia-api-sg` | 8000 from `curia-alb-sg` | |
| `curia-worker-sg` | none | outbound only |
| `curia-rds-sg` | 5432 from `curia-api-sg`, `curia-worker-sg`; **temp:** 5432 from `<MY_IP>/32` | remove temp rule after Phase 2 |

### 1c. RDS (console → RDS → Create database)
- Standard create · **PostgreSQL 16.x** · template Dev/Test · identifier `curia-db`
- user `curia` / strong password → store in Secrets Manager immediately
- **db.t4g.small**, gp3 20GB autoscaling on
- Connectivity: curia VPC, **public access YES (temporary, for Phase 2 migration)**, SG `curia-rds-sg`
- Additional config: **initial database name `curia`**, automated backups ON
- After Phase 2: flip public access to **No**.
- Post-create, connect and run: `CREATE EXTENSION IF NOT EXISTS vector;`

### 1d. Secrets Manager (one secret per key; names match task defs)
`curia/database-url`, `curia/anthropic-api-key`, `curia/hume-api-key`, `curia/voyage-api-key`,
`curia/elevenlabs-api-key`, `curia/firebase-service-account` (full JSON string) — values from
the Railway export. No R2/S3 keys: S3 access is via ECS task roles (`blob.py` falls back to
the boto3 default credential chain when `CURIA_S3_ACCESS_KEY`/`AWS_ACCESS_KEY_ID` are unset).
- `database-url` = `postgresql://curia:<PASSWORD>@<RDS_ENDPOINT>:5432/curia`

### 1e. S3 bucket (audio)
- Bucket `curia-audio`, us-east-1, **Block all public access ON** (clients use presigned URLs),
  default encryption (SSE-S3). No bucket policy needed — access via task-role IAM.
- Env (already in task defs): `CURIA_STORAGE_BACKEND=s3`, `CURIA_S3_BUCKET=curia-audio`,
  `CURIA_S3_REGION=us-east-1`, **no `CURIA_S3_ENDPOINT`** (unset = real S3; set = R2).
- Later (post-cutover, optional): CloudFront in front for India/US edge latency + egress cost.

## Phase 2 — Data migration (Railway → RDS)

```bash
# 1. Dump from Railway (URL from Railway dashboard → Postgres → Connect)
pg_dump "<RAILWAY_DATABASE_URL>" -Fc -f curia-railway.dump

# 2. Prepare RDS
psql "postgresql://curia:<PW>@<RDS_ENDPOINT>:5432/curia" -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 3. Restore
pg_restore --no-owner --no-acl -d "postgresql://curia:<PW>@<RDS_ENDPOINT>:5432/curia" curia-railway.dump

# 4. Stamp/verify migrations (from repo root, DATABASE_URL pointed at RDS)
DATABASE_URL="postgresql://curia:<PW>@<RDS_ENDPOINT>:5432/curia" python -m alembic upgrade head

# 5. Verify
psql "...rds..." -c "SELECT count(*) FROM users; SELECT count(*) FROM source; SELECT count(*) FROM episode; SELECT count(*) FROM jobs;"
# compare counts against the same queries run on Railway
```
- **Cutover note:** do a final re-dump/restore (or accept the delta) right before DNS cutover in
  Phase 5; until then Railway stays live and RDS is a trailing copy.

### 2b. Audio objects: R2 → S3
`audio_url` in the DB stores **keys** (`audio/<episode_id>.mp3`, set in `studio/generator.py:963`),
not full URLs — so moving providers = copy objects + flip env. No DB changes.
```bash
# R2 exposes an S3 API — sync directly (or use rclone)
aws s3 sync s3://<R2_BUCKET>/audio s3://curia-audio/audio \
  --source-region auto --endpoint-url <R2_ENDPOINT_FOR_SOURCE_VIA_PROFILE>
# simplest reliable recipe: rclone with an "r2" remote and an "s3" remote:
#   rclone sync r2:<R2_BUCKET>/audio s3:curia-audio/audio --progress
# verify: object counts match
aws s3 ls s3://curia-audio/audio/ --recursive | wc -l
```
- Re-sync the delta right before cutover (new episodes keep landing on R2 until env flips).

## Phase 3 — ECS live (still on Postgres queue; zero code changes)

> **TTS: RESOLVED** — merged `origin/v2.7-final` (2026-06-11), which already binds
> `smallest-lightning` (kenji→william, arjun→zorin, emeka→vanessa) with Hume kept as a
> commented fallback. Local branch is now origin + our infra work; build the image from here.
>
> **⚠️ NEVER run `alembic upgrade head` from this branch against the restored DB.** The DB is
> at `0028_daily_briefs` (from v2.7.2's chain); this branch tops at `0027` and will fail with
> "Can't locate revision". Schema is a superset of what this code needs — no migration required.
>
> **Scheduler note:** this branch's worker now runs APScheduler daily-notification crons
> (`core/notifications.py`, started in `worker/main.py`); duplicate sends across multiple
> worker tasks are prevented by `notification_log` UNIQUE(user_id, type, sent_date).

### 3a. Build + push image
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker build --platform linux/amd64 -t curia .
docker tag curia:latest <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/curia:latest
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/curia:latest
```
(⚠️ `--platform linux/amd64` — Apple Silicon builds arm64 by default; match it to the
Fargate CPU architecture chosen in the task def, or use Fargate ARM64 and skip the flag.)

### 3b. IAM roles
- `curia-ecs-execution-role`: managed `AmazonECSTaskExecutionRolePolicy` + read access to the
  `curia/*` secrets (inline policy, `secretsmanager:GetSecretValue`).
- `curia-api-task-role`: `s3:GetObject` on `arn:aws:s3:::curia-audio/*` (presigned URLs are
  signed with the role's creds — the role must hold GetObject or the URLs 403).
  ⚠️ URLs signed with task-role *temporary* creds expire when the session token does, which can
  be sooner than `expires_in=3600`. Fine here — the app fetches a fresh URL per play
  (`PlaybackContext`) — but if long-lived URLs are ever needed, sign with a dedicated IAM user.
- `curia-worker-task-role`: `s3:PutObject` + `s3:GetObject` on `arn:aws:s3:::curia-audio/*`.
- Phase 4 adds SQS permissions to both.

### 3c. Cluster + services
- Cluster `curia` (Fargate).
- Register task defs from `infra/ecs-task-def-api.json` / `-worker.json` (fill placeholders):
  `aws ecs register-task-definition --cli-input-json file://infra/ecs-task-def-api.json`
- **ALB** `curia-alb` (public subnets, `curia-alb-sg`) → target group `curia-api-tg`
  (IP type, port 8000, health check path `/health`) → listener 443 w/ ACM cert (or 80 to start).
- Service `curia-api`: desired 1, private subnets, `curia-api-sg`, attach ALB target group.
- Service `curia-worker`: desired 1, private subnets, `curia-worker-sg`, no LB.
- ⚠️ Fargate caps `stopTimeout` at **120s**; an in-flight 10-min episode may be killed on
  deploy/scale-in. Postgres queue: `reap_stale` recovers it (≤30 min). SQS (Phase 4):
  visibility timeout recovers it. Acceptable; do not fight it.

### 3d. Smoke test
- `curl http://<ALB_DNS>/health` → `{"status":"ok","db":true}`
- Create user via one-off task or temp public exec; `POST /sources` end-to-end; watch
  CloudWatch log groups `/ecs/curia-api`, `/ecs/curia-worker`.

## Phase 4 — SQS swap ✅ SHIPPED 2026-06-11 (branch `v2.7-sqs`)

**Live state:** API `curia-api:2` publishes to SQS · workers `curia-worker-interactive:1` /
`curia-worker-background:1` (autoscaling: interactive 1–4 @ backlog 1, background 1–6 @ backlog 3)
· old `curia-worker` parked at 0 (rollback path: `curia-api:1` + rescale it).
**E2E proven:** save URL → interactive ingest (<1 min) → chained episode on background →
ready in ~3 min → 10.4MB MP3 on S3 → presigned playback (HTTP 206, valid MP3). Failure path
proven by Wikipedia-403 run: 3 visibility-spaced retries → hard stop → audit `failed`.
**Resolved (2026-06-11):** DLQ-empty was CORRECT — the 403 raised `PermanentError`
(`JOB_PERMANENT_FAIL`, one attempt, message deleted by design; DLQ is only for retried-out
jobs; audit "attempts=3" is `fail_permanently` stamping attempts=max). The real bug found:
sources weren't marked `failed` on PermanentError at attempt 1 → perpetual Queued card; fixed
in `worker/handlers/ingest.py`, stuck row healed, workers rolled.

### Original design (implemented)
- 2 queues + 2 DLQs: `curia-interactive` (visibility 180s, maxReceiveCount 3),
  `curia-background` (visibility **900s** > 600s handler timeout, maxReceiveCount 3).
- Code: `lane` param + boto3 in `core/queue.py` (same signatures); `worker/main.py` long-poll +
  delete-on-ack; idempotency guards in `worker/handlers/*` (check `source/episode.status`);
  keep `jobs` table as write-only audit.
- IAM: api task role `sqs:SendMessage`; worker task role `ReceiveMessage/DeleteMessage/ChangeMessageVisibility`.
- Worker autoscaling: target-tracking on backlog-per-task (interactive 0.5, background 1.5;
  min 2/1, max ≈ LLM concurrency ceiling).

## Smoke suite — run anytime to verify the stack

```bash
CURIA_SMOKE=1 .venv/bin/pytest tests/test_aws_smoke.py -v                    # infra+API (~5s, free)
CURIA_SMOKE=1 CURIA_SMOKE_FULL=1 .venv/bin/pytest tests/test_aws_smoke.py -v # + live pipeline (~10 min, LLM cents)
```
Needs AWS creds; the API token self-serves from Secrets Manager (`curia/smoke-test-token`,
user `claude-e2e-test`). Covers: services at desired counts, **running digests == ECR :latest**
(deploy-freshness — caught a stale API on its first run), queue visibility/redrive config,
DLQ depth (failing = alert), backlog sanity, task-def env drift (backend/lane/URLs), S3
public-block, secrets present, auth edges, list endpoints, 404/422 validation, presigned audio
serves real MP3 bytes; FULL adds save→episode e2e (+API idempotency, S3 object check) and the
terminal-state invariant for permanently failing URLs (regression for the perpetual-Queued bug).
Post-cutover: update `CURIA_SMOKE_API` to the HTTPS domain.

## Phase 5 — Cutover + observability
- Final data sync → flip RDS public access off → point frontend at AWS
  (custom domain via Route53+ACM on ALB preferred; else new EAS build with new URL) →
  decommission Railway after soak.
- Alarms: ALB 5xx, target unhealthy, RDS CPU/connections/storage, DLQ depth > 0,
  queue oldest-message age, worker heartbeat (no `JOB_START`/poll logs for N minutes).
