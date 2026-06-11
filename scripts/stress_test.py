"""
scripts/stress_test.py
Stress harness for the deployed AWS stack. Three modes + a live watcher.

  python scripts/stress_test.py reads --requests 2000 --concurrency 50
      Hammer the read path (GET /health, /sources, /episodes). Free.
      Reports RPS, error rate, latency p50/p95/p99.

  python scripts/stress_test.py failburst --count 20
      Burst-save bot-walled URLs (unique per run). Near-free: they permanent-fail
      at scrape time. Stresses SQS receive/fail/audit churn on the interactive lane.

  python scripts/stress_test.py genburst --count 10
      Burst-save real, scrapeable URLs -> full pipeline per save (ingest +
      chained episode). COSTS MONEY (~$0.2-0.5 per save in LLM/TTS).
      This is the autoscaling + rate-limit test.

  python scripts/stress_test.py watch
      Live dashboard: queue depths, worker task counts, source/episode states
      for the smoke user. Run in a second terminal during bursts.

Token is self-served from Secrets Manager (curia/smoke-test-token).
API override: CURIA_SMOKE_API env var.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import string
import time

import boto3
import httpx

REGION = "us-east-1"
API = os.getenv("CURIA_SMOKE_API", "http://curia-alb-23430048.us-east-1.elb.amazonaws.com")
QUEUES = {
    "interactive": "https://sqs.us-east-1.amazonaws.com/617341601034/curia-interactive",
    "background": "https://sqs.us-east-1.amazonaws.com/617341601034/curia-background",
}
DLQS = {lane: url + "-dlq" for lane, url in QUEUES.items()}

# Distinct, scraper-friendly article URLs for genburst (plain HTML, no bot walls).
GEN_URLS = [
    "https://paulgraham.com/greatwork.html",
    "https://paulgraham.com/think.html",
    "https://paulgraham.com/words.html",
    "https://paulgraham.com/ds.html",
    "https://paulgraham.com/love.html",
    "https://paulgraham.com/hwh.html",
    "https://paulgraham.com/mean.html",
    "https://paulgraham.com/kids.html",
    "https://paulgraham.com/lesson.html",
    "https://paulgraham.com/genius.html",
    "https://paulgraham.com/wealth.html",
    "https://paulgraham.com/taste.html",
    "https://paulgraham.com/better.html",
    "https://paulgraham.com/newideas.html",
    "https://paulgraham.com/useful.html",
    "https://paulgraham.com/noob.html",
    "https://paulgraham.com/early.html",
    "https://paulgraham.com/seesv.html",
    "https://paulgraham.com/conformism.html",
    "https://paulgraham.com/fn.html",
]


def get_token() -> str:
    sm = boto3.client("secretsmanager", region_name=REGION)
    return sm.get_secret_value(SecretId="curia/smoke-test-token")["SecretString"]


# ---------------------------------------------------------------------------
# Mode: reads
# ---------------------------------------------------------------------------


async def mode_reads(requests: int, concurrency: int) -> None:
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    paths = ["/health", "/sources", "/episodes", "/episodes", "/sources"]
    latencies: list[float] = []
    errors: dict[str, int] = {}
    sem = asyncio.Semaphore(concurrency)

    async def one(client: httpx.AsyncClient, i: int) -> None:
        path = paths[i % len(paths)]
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.get(path, headers=headers if path != "/health" else None)
                latencies.append((time.perf_counter() - t0) * 1000)
                if r.status_code != 200:
                    errors[f"HTTP {r.status_code} {path}"] = errors.get(f"HTTP {r.status_code} {path}", 0) + 1
            except Exception as exc:
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1

    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as client:
        await asyncio.gather(*[one(client, i) for i in range(requests)])
    wall = time.perf_counter() - t0

    ok = len(latencies)
    print(f"\n=== READ PATH: {requests} requests @ {concurrency} concurrent ===")
    print(f"wall: {wall:.1f}s  →  {requests / wall:.0f} req/s")
    print(f"ok: {ok}  errors: {sum(errors.values())} {dict(errors) if errors else ''}")
    if latencies:
        latencies.sort()
        p = lambda q: latencies[min(int(q * ok), ok - 1)]
        print(f"latency ms — p50: {p(0.50):.0f}  p95: {p(0.95):.0f}  p99: {p(0.99):.0f}  max: {latencies[-1]:.0f}")
        print(f"mean: {statistics.mean(latencies):.0f}ms")


# ---------------------------------------------------------------------------
# Mode: failburst / genburst
# ---------------------------------------------------------------------------


async def _burst(urls: list[str], label: str) -> list[str]:
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    created: list[str] = []
    errors = 0

    async def save(client: httpx.AsyncClient, url: str) -> None:
        nonlocal errors
        try:
            r = await client.post("/sources", headers=headers, json={"url": url})
            if r.status_code == 202:
                created.append(r.json()["id"])
            else:
                errors += 1
                print(f"  save failed HTTP {r.status_code}: {url[-40:]}")
        except Exception as exc:
            errors += 1
            print(f"  save error {type(exc).__name__}: {url[-40:]}")

    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as client:
        await asyncio.gather(*[save(client, u) for u in urls])
    print(f"\n=== {label}: {len(created)} accepted, {errors} errors in {time.perf_counter() - t0:.1f}s ===")
    print("now run:  python scripts/stress_test.py watch")
    return created


async def mode_failburst(count: int) -> None:
    # Unique wiki URLs (bot-walled from datacenter IPs) — each permanent-fails fast.
    salt = "".join(random.choices(string.ascii_lowercase, k=4))
    urls = [f"https://en.wikipedia.org/wiki/Stress_test_{salt}_{i}" for i in range(count)]
    await _burst(urls, f"FAILBURST x{count}")


async def mode_genburst(count: int) -> None:
    if count > len(GEN_URLS):
        raise SystemExit(f"max {len(GEN_URLS)} distinct URLs available")
    est = count * 0.35
    print(f"genburst x{count} — estimated LLM/TTS cost ≈ ${est:.2f}. Ctrl-C within 5s to abort.")
    await asyncio.sleep(5)
    # Delete prior smoke sources for these URLs so dedup doesn't no-op the run.
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as client:
        existing = (await client.get("/sources", headers=headers)).json()
        for s in existing:
            if s.get("url") in GEN_URLS[:count]:
                await client.delete(f"/sources/{s['id']}", headers=headers)
    await _burst(GEN_URLS[:count], f"GENBURST x{count}")


# ---------------------------------------------------------------------------
# Mode: watch
# ---------------------------------------------------------------------------


def mode_watch() -> None:
    sqs = boto3.client("sqs", region_name=REGION)
    ecs = boto3.client("ecs", region_name=REGION)
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    def qdepth(url: str) -> tuple[int, int]:
        a = sqs.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])

    with httpx.Client(base_url=API, timeout=20.0) as client:
        while True:
            try:
                svcs = ecs.describe_services(
                    cluster="curia",
                    services=["curia-worker-interactive", "curia-worker-background"],
                )["services"]
                tasks = {s["serviceName"].replace("curia-worker-", ""): f"{s['runningCount']}/{s['desiredCount']}" for s in svcs}
                qi, qi_inflight = qdepth(QUEUES["interactive"])
                qb, qb_inflight = qdepth(QUEUES["background"])
                dlq_i, _ = qdepth(DLQS["interactive"])
                dlq_b, _ = qdepth(DLQS["background"])
                sources = client.get("/sources", headers=headers).json()
                episodes = client.get("/episodes", headers=headers).json()
                s_states: dict[str, int] = {}
                for s in sources:
                    s_states[s["status"]] = s_states.get(s["status"], 0) + 1
                e_states: dict[str, int] = {}
                for e in episodes:
                    e_states[e["status"]] = e_states.get(e["status"], 0) + 1
                print(
                    f"{time.strftime('%H:%M:%S')} | "
                    f"int q={qi}+{qi_inflight}inflight tasks={tasks.get('interactive')} | "
                    f"bg q={qb}+{qb_inflight}inflight tasks={tasks.get('background')} | "
                    f"DLQ i={dlq_i} b={dlq_b} | src={s_states} | ep={e_states}"
                )
            except Exception as exc:
                print(f"{time.strftime('%H:%M:%S')} | watch error: {exc}")
            time.sleep(10)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("reads")
    r.add_argument("--requests", type=int, default=2000)
    r.add_argument("--concurrency", type=int, default=50)
    f = sub.add_parser("failburst")
    f.add_argument("--count", type=int, default=20)
    g = sub.add_parser("genburst")
    g.add_argument("--count", type=int, default=10)
    sub.add_parser("watch")
    args = ap.parse_args()

    if args.mode == "reads":
        asyncio.run(mode_reads(args.requests, args.concurrency))
    elif args.mode == "failburst":
        asyncio.run(mode_failburst(args.count))
    elif args.mode == "genburst":
        asyncio.run(mode_genburst(args.count))
    elif args.mode == "watch":
        mode_watch()


if __name__ == "__main__":
    main()
