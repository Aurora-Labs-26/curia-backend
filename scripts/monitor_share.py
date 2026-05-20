import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

POLL_SECONDS = 1.5


def short_id(value) -> str:
    return str(value)[:8]


def source_label(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}"


def clean_payload(payload) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def format_generate_payload(payload: dict) -> str:
    return (
        f"source={short_id(payload.get('source_id'))} "
        f"standalone={payload.get('standalone')} "
        f"show={payload.get('show_name') or 'default'} "
        f"host={payload.get('speaker') or 'default'} "
        f"length={payload.get('length_minutes') or 'default'} "
        f"angle={'yes' if payload.get('angle_override') else 'no'}"
    )


async def seed_seen(conn: asyncpg.Connection, since: datetime | None) -> tuple[dict, dict, dict, set]:
    seen_sources: dict[str, str] = {}
    seen_jobs: dict[str, str] = {}
    seen_episodes: dict[str, str] = {}
    seen_ideas: set[str] = set()

    if since is not None:
        return seen_sources, seen_jobs, seen_episodes, seen_ideas

    for row in await conn.fetch("SELECT id, status FROM source"):
        seen_sources[str(row["id"])] = row["status"]
    for row in await conn.fetch("SELECT id, status FROM jobs"):
        seen_jobs[str(row["id"])] = row["status"]
    for row in await conn.fetch("SELECT id, status FROM episode"):
        seen_episodes[str(row["id"])] = row["status"]
    for row in await conn.fetch("SELECT id FROM show_idea"):
        seen_ideas.add(str(row["id"]))

    return seen_sources, seen_jobs, seen_episodes, seen_ideas


async def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor Curia share-sheet flow.")
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Print existing rows too. By default, only rows created after monitor start are shown.",
    )
    args = parser.parse_args()

    dsn = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn)
    since = None if args.include_existing else datetime.now(timezone.utc)
    seen_sources, seen_jobs, seen_episodes, seen_ideas = await seed_seen(conn, since)

    mode = "including existing rows" if args.include_existing else "new rows only"
    print(f"[SHARE MONITOR] started ({mode})", flush=True)

    while True:
        try:
            since_filter = "WHERE created_at >= $1" if since else ""
            params = [since] if since else []

            sources = await conn.fetch(
                f"""
                SELECT id, url, status, title, created_at
                FROM source
                {since_filter}
                ORDER BY created_at ASC
                LIMIT 100
                """,
                *params,
            )
            for row in sources:
                key = str(row["id"])
                value = row["status"]
                if seen_sources.get(key) != value:
                    print(
                        f"[SOURCE] id={short_id(key)} -> {value} | {source_label(row['url'])}",
                        flush=True,
                    )
                    seen_sources[key] = value

            jobs = await conn.fetch(
                f"""
                SELECT id, type, status, attempts, payload, last_error, created_at
                FROM jobs
                {since_filter}
                ORDER BY created_at ASC
                LIMIT 150
                """,
                *params,
            )
            for row in jobs:
                key = str(row["id"])
                value = row["status"]
                if seen_jobs.get(key) == value:
                    continue
                payload = clean_payload(row["payload"])
                extra = ""
                if row["type"] == "ingest":
                    extra = (
                        f" source={short_id(payload.get('source_id'))} "
                        f"auto_generate={payload.get('auto_generate')} "
                        f"url={source_label(payload.get('url'))}"
                    )
                elif row["type"] == "generate_from_source":
                    extra = " " + format_generate_payload(payload)
                elif row["type"] == "generate_episode":
                    extra = f" episode={short_id(payload.get('episode_id'))}"
                if row["last_error"]:
                    extra += f" error={str(row['last_error'])[:100]}"
                print(f"[JOB:{row['type']}] id={short_id(key)} -> {value}{extra}", flush=True)
                seen_jobs[key] = value

            ideas = await conn.fetch(
                f"""
                SELECT id, angle, idea_type, format, source_ids, generated, created_at
                FROM show_idea
                {since_filter}
                ORDER BY created_at ASC
                LIMIT 100
                """,
                *params,
            )
            for row in ideas:
                key = str(row["id"])
                if key in seen_ideas:
                    continue
                seen_ideas.add(key)
                source_count = len(row["source_ids"] or [])
                print(
                    f"[IDEA] id={short_id(key)} format={row['format']} "
                    f"type={row['idea_type']} sources={source_count} generated={row['generated']}",
                    flush=True,
                )
                print(f"       angle: {(row['angle'] or '')[:140]}", flush=True)

            episodes = await conn.fetch(
                f"""
                SELECT id, status, error, show_name, editorial_direction,
                       length_minutes, speaker_override, source_ids, title, created_at
                FROM episode
                {since_filter}
                ORDER BY created_at ASC
                LIMIT 100
                """,
                *params,
            )
            for row in episodes:
                key = str(row["id"])
                value = row["status"]
                if seen_episodes.get(key) == value:
                    continue
                host = row["speaker_override"] or "default"
                length = row["length_minutes"] or "default"
                source_count = len(row["source_ids"] or [])
                err = f" error={str(row['error'])[:100]}" if row["error"] else ""
                print(
                    f"[EPISODE] id={short_id(key)} -> {value} "
                    f"show={row['show_name'] or 'default'} host={host} "
                    f"length={length}m sources={source_count}{err}",
                    flush=True,
                )
                if row["title"]:
                    print(f"          title: {row['title'][:140]}", flush=True)
                if row["editorial_direction"]:
                    print(f"          angle: {row['editorial_direction'][:140]}", flush=True)
                seen_episodes[key] = value

        except Exception as exc:
            print(f"[SHARE MONITOR ERROR] {exc}", flush=True)

        await asyncio.sleep(POLL_SECONDS)


asyncio.run(main())
