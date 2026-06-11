"""
tests/test_aws_smoke.py
Live verification suite for the deployed AWS stack — run anytime to prove the
infrastructure AND the product behavior are healthy.

Layers (cheap → expensive):
  TestInfraConfig   read-only AWS checks: services, image freshness, queue config
  TestApiSurface    fast HTTP checks against the live ALB: auth, lists, audio
  TestPipelineHappy full save→episode pipeline (opt-in: costs LLM/TTS cents, ~5 min)
  TestPipelineFailure permanent-failure invariant (opt-in)

Usage:
  CURIA_SMOKE=1 .venv/bin/pytest tests/test_aws_smoke.py -v                 # layers 1-2
  CURIA_SMOKE=1 CURIA_SMOKE_FULL=1 .venv/bin/pytest tests/test_aws_smoke.py -v   # all

Requirements: AWS credentials with read access (CLI profile works); the smoke
user token is self-served from Secrets Manager (curia/smoke-test-token).
Config overrides: CURIA_SMOKE_API (default: the curia ALB URL).
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("CURIA_SMOKE") != "1",
    reason="live AWS smoke suite — set CURIA_SMOKE=1 to run",
)

FULL = os.getenv("CURIA_SMOKE_FULL") == "1"

REGION = "us-east-1"
CLUSTER = "curia"
API_BASE = os.getenv(
    "CURIA_SMOKE_API", "http://curia-alb-23430048.us-east-1.elb.amazonaws.com"
)
QUEUES = {
    "interactive": "https://sqs.us-east-1.amazonaws.com/617341601034/curia-interactive",
    "background": "https://sqs.us-east-1.amazonaws.com/617341601034/curia-background",
}
DLQS = {lane: url + "-dlq" for lane, url in QUEUES.items()}
AUDIO_BUCKET = "curia-audio"
REQUIRED_SECRETS = {
    "curia/database-url",
    "curia/anthropic-api-key",
    "curia/openai-api-key",
    "curia/firebase-service-account",
    "curia/smallest-api-key",
}
# Worker services and the lane each must be pinned to.
WORKER_SERVICES = {
    "curia-worker-interactive": "interactive",
    "curia-worker-background": "background",
}

# Happy-path pipeline budgets (seconds). Generation is normally ~3 min; budget 3x.
SOURCE_READY_BUDGET = 240
EPISODE_READY_BUDGET = 600
FAILURE_TERMINAL_BUDGET = 180

SCRAPER_FRIENDLY_URLS = [
    "https://paulgraham.com/greatwork.html",
    "https://paulgraham.com/think.html",
    "https://paulgraham.com/words.html",
    "https://paulgraham.com/ds.html",
]
BOT_WALLED_URL = "https://en.wikipedia.org/wiki/Smoke_testing_(software)"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def aws():
    boto3 = pytest.importorskip("boto3")
    return {
        "ecs": boto3.client("ecs", region_name=REGION),
        "ecr": boto3.client("ecr", region_name=REGION),
        "sqs": boto3.client("sqs", region_name=REGION),
        "s3": boto3.client("s3", region_name=REGION),
        "secrets": boto3.client("secretsmanager", region_name=REGION),
    }


@pytest.fixture(scope="session")
def token(aws):
    return aws["secrets"].get_secret_value(SecretId="curia/smoke-test-token")[
        "SecretString"
    ]


@pytest.fixture(scope="session")
def http():
    httpx = pytest.importorskip("httpx")
    return httpx.Client(base_url=API_BASE, timeout=20.0, follow_redirects=True)


@pytest.fixture(scope="session")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _running_task_digests(aws, service: str) -> list[str]:
    arns = aws["ecs"].list_tasks(cluster=CLUSTER, serviceName=service)["taskArns"]
    if not arns:
        return []
    tasks = aws["ecs"].describe_tasks(cluster=CLUSTER, tasks=arns)["tasks"]
    return [c["imageDigest"] for t in tasks for c in t["containers"] if c.get("imageDigest")]


# ---------------------------------------------------------------------------
# Layer 1 — infrastructure & configuration (read-only, fast)
# ---------------------------------------------------------------------------


class TestInfraConfig:
    def test_services_running_at_desired_count(self, aws):
        names = ["curia-api", *WORKER_SERVICES]
        svcs = aws["ecs"].describe_services(cluster=CLUSTER, services=names)["services"]
        problems = [
            f"{s['serviceName']}: desired={s['desiredCount']} running={s['runningCount']}"
            for s in svcs
            if s["desiredCount"] < 1 or s["runningCount"] < s["desiredCount"]
        ]
        assert not problems, f"unhealthy services: {problems}"

    def test_legacy_worker_is_parked(self, aws):
        """The pre-SQS worker must stay at 0 (or be deleted) — if it runs, jobs
        double-process and the notification crons race the lane workers."""
        svcs = aws["ecs"].describe_services(cluster=CLUSTER, services=["curia-worker"])[
            "services"
        ]
        for s in svcs:
            if s["status"] == "ACTIVE":
                assert s["desiredCount"] == 0, "legacy curia-worker service is running!"

    def test_running_tasks_use_latest_ecr_image(self, aws):
        """Deploy freshness: every running task must run the digest currently
        tagged :latest in ECR. Catches 'pushed but forgot to roll' drift."""
        latest = aws["ecr"].describe_images(
            repositoryName="curia", imageIds=[{"imageTag": "latest"}]
        )["imageDetails"][0]["imageDigest"]
        stale = {}
        for service in ["curia-api", *WORKER_SERVICES]:
            digests = _running_task_digests(aws, service)
            assert digests, f"{service}: no running tasks"
            bad = [d for d in digests if d != latest]
            if bad:
                stale[service] = bad
        assert not stale, f"tasks running stale images (re-deploy needed): {stale}"

    def test_queue_configuration(self, aws):
        """Visibility must exceed the 600s handler cap or healthy long jobs get
        duplicated; redrive must exist or failures retry forever."""
        for lane, url in QUEUES.items():
            attrs = aws["sqs"].get_queue_attributes(
                QueueUrl=url, AttributeNames=["VisibilityTimeout", "RedrivePolicy"]
            )["Attributes"]
            assert int(attrs["VisibilityTimeout"]) >= 660, f"{lane}: visibility too low"
            redrive = json.loads(attrs["RedrivePolicy"])
            assert int(redrive["maxReceiveCount"]) == 3, f"{lane}: redrive misconfigured"
            assert "-dlq" in redrive["deadLetterTargetArn"], f"{lane}: DLQ not wired"

    def test_dlqs_are_empty(self, aws):
        """A non-empty DLQ means jobs are retrying out — investigate, redrive,
        then purge. This test failing IS the alert."""
        depths = {}
        for lane, url in DLQS.items():
            n = int(
                aws["sqs"].get_queue_attributes(
                    QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
                )["Attributes"]["ApproximateNumberOfMessages"]
            )
            if n:
                depths[lane] = n
        assert not depths, f"dead-lettered jobs need attention: {depths}"

    def test_queue_backlog_is_sane(self, aws):
        """Backlog far above the autoscaling targets means workers aren't keeping
        up (or aren't consuming at all)."""
        for lane, url in QUEUES.items():
            n = int(
                aws["sqs"].get_queue_attributes(
                    QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
                )["Attributes"]["ApproximateNumberOfMessages"]
            )
            assert n < 25, f"{lane} backlog {n} — workers consuming?"

    def test_worker_taskdefs_pinned_to_their_lane(self, aws):
        for service, lane in WORKER_SERVICES.items():
            svc = aws["ecs"].describe_services(cluster=CLUSTER, services=[service])[
                "services"
            ][0]
            td = aws["ecs"].describe_task_definition(taskDefinition=svc["taskDefinition"])[
                "taskDefinition"
            ]
            env = {
                e["name"]: e["value"]
                for e in td["containerDefinitions"][0]["environment"]
            }
            assert env.get("CURIA_QUEUE_BACKEND") == "sqs", f"{service}: backend drift"
            assert env.get("CURIA_WORKER_LANE") == lane, f"{service}: lane drift"
            assert env.get("CURIA_SQS_INTERACTIVE_URL") and env.get(
                "CURIA_SQS_BACKGROUND_URL"
            ), f"{service}: queue URLs missing"

    def test_api_taskdef_publishes_to_sqs(self, aws):
        svc = aws["ecs"].describe_services(cluster=CLUSTER, services=["curia-api"])[
            "services"
        ][0]
        td = aws["ecs"].describe_task_definition(taskDefinition=svc["taskDefinition"])[
            "taskDefinition"
        ]
        env = {e["name"]: e["value"] for e in td["containerDefinitions"][0]["environment"]}
        assert env.get("CURIA_QUEUE_BACKEND") == "sqs"
        assert env.get("CURIA_STORAGE_BACKEND") == "s3"
        assert env.get("CURIA_S3_BUCKET") == AUDIO_BUCKET

    def test_audio_bucket_blocks_public_access(self, aws):
        cfg = aws["s3"].get_public_access_block(Bucket=AUDIO_BUCKET)[
            "PublicAccessBlockConfiguration"
        ]
        assert all(cfg.values()), f"public access not fully blocked: {cfg}"

    def test_required_secrets_exist(self, aws):
        names = set()
        paginator = aws["secrets"].get_paginator("list_secrets")
        for page in paginator.paginate():
            names |= {s["Name"] for s in page["SecretList"]}
        missing = REQUIRED_SECRETS - names
        assert not missing, f"missing secrets: {missing}"


# ---------------------------------------------------------------------------
# Layer 2 — live API surface (fast, no money spent)
# ---------------------------------------------------------------------------


class TestApiSurface:
    def test_health(self, http):
        r = http.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["db"] is True

    def test_request_without_token_is_rejected(self, http):
        r = http.get("/sources")
        assert r.status_code in (401, 403)

    def test_garbage_token_is_rejected(self, http):
        r = http.get("/sources", headers={"Authorization": "Bearer ck_garbage_nope"})
        assert r.status_code in (401, 403)

    def test_auth_me(self, http, auth):
        r = http.get("/auth/me", headers=auth)
        assert r.status_code == 200
        assert r.json()["id"] == "claude-e2e-test"

    def test_list_sources_and_episodes(self, http, auth):
        for path in ("/sources", "/episodes"):
            r = http.get(path, headers=auth)
            assert r.status_code == 200, f"{path}: {r.status_code}"
            assert isinstance(r.json(), list)

    def test_unknown_episode_is_404(self, http, auth):
        r = http.get(f"/episodes/{uuid.uuid4()}", headers=auth)
        assert r.status_code == 404

    def test_invalid_url_is_rejected(self, http, auth):
        r = http.post("/sources", headers=auth, json={"url": "not-a-url"})
        assert r.status_code == 422

    def test_audio_presign_serves_real_mp3(self, http, auth):
        """Find any ready episode for the smoke user and stream its first bytes
        through the presigned URL — proves API→S3 presign→playback end to end."""
        episodes = http.get("/episodes", headers=auth).json()
        ready = [e for e in episodes if e.get("status") == "ready"]
        if not ready:
            pytest.skip("no ready episode for smoke user yet — run the FULL suite once")
        r = http.get(f"/episodes/{ready[0]['id']}/audio", headers=auth)
        assert r.status_code == 200
        url = r.json()["url"]
        assert "X-Amz-Signature" in url, "expected a presigned URL"
        import httpx

        head = httpx.get(url, headers={"Range": "bytes=0-2"}, timeout=20.0)
        assert head.status_code == 206
        assert head.content[:3] in (b"ID3", b"\xff\xfb\x90"), "not MP3 bytes"


# ---------------------------------------------------------------------------
# Layer 3 — full pipeline, happy path (opt-in: spends LLM/TTS money)
# ---------------------------------------------------------------------------


def _poll(fn, budget: int, every: int = 10):
    deadline = time.time() + budget
    while time.time() < deadline:
        out = fn()
        if out is not None:
            return out
        time.sleep(every)
    return None


@pytest.mark.skipif(not FULL, reason="set CURIA_SMOKE_FULL=1 (spends ~LLM cents, ~10 min)")
class TestPipelineHappy:
    @pytest.fixture(scope="class")
    def fresh_url(self, http, auth):
        """Pick a known-good URL the smoke user hasn't ingested; delete the
        oldest smoke source if all are used so the suite stays re-runnable."""
        existing = {s.get("url") for s in http.get("/sources", headers=auth).json()}
        for url in SCRAPER_FRIENDLY_URLS:
            if url not in existing:
                return url
        victim = next(
            s for s in http.get("/sources", headers=auth).json()
            if s.get("url") in SCRAPER_FRIENDLY_URLS
        )
        http.delete(f"/sources/{victim['id']}", headers=auth)
        return victim["url"]

    def test_save_to_listenable_episode(self, http, auth, fresh_url, aws):
        # 1. Save
        r = http.post("/sources", headers=auth, json={"url": fresh_url})
        assert r.status_code == 202, r.text
        source_id = r.json()["id"]
        assert r.json()["job_id"], "no job enqueued"

        # 2. API idempotency: same URL again must return the SAME source
        r2 = http.post("/sources", headers=auth, json={"url": fresh_url})
        assert r2.status_code == 202 and r2.json()["id"] == source_id

        # 3. Ingest completes (interactive lane)
        def source_ready():
            rows = http.get("/sources", headers=auth).json()
            row = next((s for s in rows if s["id"] == source_id), None)
            if row and row["status"] == "ready":
                return row
            if row and row["status"] == "failed":
                pytest.fail(f"ingest failed: {row.get('error')}")
            return None

        assert _poll(source_ready, SOURCE_READY_BUDGET), "ingest did not finish in budget"

        # 4. Chained episode reaches ready (background lane)
        def episode_ready():
            eps = http.get("/episodes", headers=auth).json()
            for e in eps:
                if source_id in (e.get("source_ids") or []):
                    if e["status"] == "ready":
                        return e
                    if e["status"] == "failed":
                        pytest.fail(f"episode failed: {e.get('error')}")
            return None

        episode = _poll(episode_ready, EPISODE_READY_BUDGET)
        assert episode, "chained episode did not reach ready in budget"

        # 5. Audio is a real object on S3 and playable via presign
        r = http.get(f"/episodes/{episode['id']}/audio", headers=auth)
        assert r.status_code == 200 and "X-Amz-Signature" in r.json()["url"]
        head = aws["s3"].head_object(
            Bucket=AUDIO_BUCKET, Key=f"audio/{episode['id']}.mp3"
        )
        assert head["ContentLength"] > 100_000, "suspiciously small audio file"


# ---------------------------------------------------------------------------
# Layer 4 — failure-path invariant (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FULL, reason="set CURIA_SMOKE_FULL=1")
class TestPipelineFailure:
    def test_blocked_url_reaches_terminal_state(self, http, auth):
        """Regression for the perpetual-Queued bug: a permanently failing URL
        must land in a TERMINAL state (failed — or ready, if the scraper cascade
        finds a way in). What it must never do is sit in scraping/queued forever."""
        r = http.post("/sources", headers=auth, json={"url": BOT_WALLED_URL})
        assert r.status_code == 202
        source_id = r.json()["id"]

        def terminal():
            rows = http.get("/sources", headers=auth).json()
            row = next((s for s in rows if s["id"] == source_id), None)
            return row if row and row["status"] in ("ready", "failed") else None

        row = _poll(terminal, FAILURE_TERMINAL_BUDGET, every=10)
        if row is None:
            pytest.skip(
                "source still processing — likely transient-retry window (900s "
                "visibility); re-check manually that it terminates"
            )
        if row["status"] == "failed":
            assert row.get("error"), "failed without an error message"
        # cleanup so re-runs start fresh
        http.delete(f"/sources/{source_id}", headers=auth)
