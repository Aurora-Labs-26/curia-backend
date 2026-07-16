"""
Seeds ~18 simulated users for the harness's per-user "Generate Brief" flow —
just enough to exercise/demonstrate cross-user cache-hit behavior (see the
implementation plan's verification steps), not the eventual ~100-user
production scale.

Requires db/001_schema.sql and db/002_seed_topics.sql to already be applied
(see scripts/bootstrap_db.py) — this script looks up the 7 system topics by
name and fails loudly if none exist.

Usage:
    python -m scripts.seed_users
"""

import asyncio
import random

from app.services import cache_service

random.seed(42)  # reproducible across runs

CITIES = [
    "Mumbai", "Bangalore", "Delhi", "New York", "London",
    "Toronto", "Singapore", "Sydney", "Dubai",
]
SCHEDULED_TIMES = ["06:30", "07:00", "07:30", "08:00"]
CUSTOM_TOPIC_POOL = [
    "Manchester United", "NVIDIA earnings", "Formula 1",
    "Taylor Swift", "Bitcoin price", "Champions League",
]

NUM_USERS = 18


async def main() -> None:
    system_topics = await cache_service.list_system_topics()
    if not system_topics:
        raise RuntimeError("No system topics found — run `python -m scripts.bootstrap_db` first.")
    topic_id_by_name = {t["name"]: str(t["id"]) for t in system_topics}
    topic_names = list(topic_id_by_name.keys())

    custom_topic_id_cache: dict = {}

    async def get_custom_topic_id(name: str) -> str:
        if name not in custom_topic_id_cache:
            custom_topic_id_cache[name] = await cache_service.get_or_create_custom_topic(name)
        return custom_topic_id_cache[name]

    created = []

    for n in range(NUM_USERS):
        email = f"user{n}@example.com"

        # Guarantee (by construction, not left to random chance) at least 2
        # users with an IDENTICAL chosen-topic set, for the cross-user
        # cache-hit demonstration in the plan's verification steps.
        if n in (0, 1):
            chosen_names = ["Tech", "Business"]
        else:
            chosen_names = random.sample(topic_names, random.randint(2, 4))

        # Guarantee at least 2 users sharing a city, for the local
        # cache-hit demonstration.
        location_name = "Mumbai" if n in (2, 3) else random.choice(CITIES)
        scheduled_time = random.choice(SCHEDULED_TIMES)
        custom_names = random.sample(CUSTOM_TOPIC_POOL, random.randint(0, 2))

        user_id = await cache_service.create_user(email, location_name, scheduled_time)

        for name in chosen_names:
            await cache_service.link_user_topic(user_id, topic_id_by_name[name], "chosen")
        for name in custom_names:
            custom_topic_id = await get_custom_topic_id(name)
            await cache_service.link_user_topic(user_id, custom_topic_id, "custom")

        created.append({
            "email": email, "location_name": location_name,
            "chosen": chosen_names, "custom": custom_names,
        })
        print(f"Seeded {email} — location={location_name} chosen={chosen_names} custom={custom_names}")

    print(f"\nDone. Seeded {len(created)} users.")


if __name__ == "__main__":
    asyncio.run(main())
