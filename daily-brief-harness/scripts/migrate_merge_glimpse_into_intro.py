"""
One-off data migration: merge "glimpse" content into "intro" ahead of
db/006_drop_glimpse_columns.sql. MUST run and be verified before that
migration — once the columns are dropped there is no recovering
not-yet-merged glimpse content short of a full Postgres restore. See
the plan for the full design rationale.

Usage:
    python -m scripts.migrate_merge_glimpse_into_intro
"""

import asyncio

from app.services import harness_db


async def main() -> None:
    pool = await harness_db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Step 1: capture the currently-frozen run set.
            gold_rows = await conn.fetch(
                "SELECT run_id, profile_label FROM harness.eval_gold_runs WHERE is_active = true"
            )
            gold_run_ids = [r["run_id"] for r in gold_rows]
            print(f"Step 1: {len(gold_run_ids)} active gold runs captured: "
                  f"{[r['profile_label'] for r in gold_rows]}")

            # Step 2: unfreeze them — required, the trigger blocks step 4 otherwise.
            if gold_run_ids:
                await conn.execute(
                    "UPDATE harness.eval_gold_runs SET is_active = false WHERE run_id = ANY($1::uuid[])",
                    gold_run_ids,
                )
            print("Step 2: unfrozen.")

            # Step 3: merge + delete "glimpse" golden scripts into "intro", per gold run.
            merged_count = 0
            fallback_count = 0
            for run_id in gold_run_ids:
                glimpse_row = await conn.fetchrow(
                    """
                    SELECT id, script_text FROM harness.eval_gold_scripts
                    WHERE run_id = $1 AND segment_type = 'glimpse'
                      AND article_url IS NULL AND superseded_at IS NULL
                    """,
                    run_id,
                )
                if not glimpse_row:
                    continue

                intro_row = await conn.fetchrow(
                    """
                    SELECT id, script_text FROM harness.eval_gold_scripts
                    WHERE run_id = $1 AND segment_type = 'intro'
                      AND article_url IS NULL AND superseded_at IS NULL
                    """,
                    run_id,
                )
                if intro_row:
                    merged_text = f"{intro_row['script_text'].strip()} {glimpse_row['script_text'].strip()}"
                    await conn.execute(
                        "UPDATE harness.eval_gold_scripts SET superseded_at = now() WHERE id = $1",
                        intro_row["id"],
                    )
                    merged_count += 1
                else:
                    # No human-edited intro exists for this profile — fall back to
                    # the raw generated intro text as the base.
                    generated_intro = await conn.fetchval(
                        "SELECT intro_text FROM harness.eval_meta_segments WHERE run_id = $1", run_id
                    )
                    merged_text = f"{generated_intro.strip()} {glimpse_row['script_text'].strip()}"
                    fallback_count += 1

                await conn.execute(
                    """
                    INSERT INTO harness.eval_gold_scripts (run_id, segment_type, article_url, script_text)
                    VALUES ($1, 'intro', NULL, $2)
                    """,
                    run_id, merged_text,
                )
                await conn.execute("DELETE FROM harness.eval_gold_scripts WHERE id = $1", glimpse_row["id"])

            print(f"Step 3: merged {merged_count} (had existing intro edit), "
                  f"{fallback_count} (fell back to generated intro text).")

            # Step 4: concatenate intro_text += glimpse_text across ALL eval_meta_segments rows.
            result = await conn.execute(
                "UPDATE harness.eval_meta_segments "
                "SET intro_text = trim(intro_text) || ' ' || trim(glimpse_text)"
            )
            print(f"Step 4: {result}")

            # Step 5: refreeze the originally-captured run_ids.
            if gold_run_ids:
                await conn.execute(
                    "UPDATE harness.eval_gold_runs SET is_active = true WHERE run_id = ANY($1::uuid[])",
                    gold_run_ids,
                )
            print("Step 5: refrozen.")

            # Step 6: verification output.
            print("\n--- Step 6: verification ---")
            for run_id, label in [(r["run_id"], r["profile_label"]) for r in gold_rows]:
                intro_text = await conn.fetchval(
                    "SELECT intro_text FROM harness.eval_meta_segments WHERE run_id = $1", run_id
                )
                word_count = len(intro_text.split())
                print(f"{label:20s} new intro word count: {word_count}")

            n_glimpse_remaining = await conn.fetchval(
                "SELECT count(*) FROM harness.eval_gold_scripts WHERE segment_type = 'glimpse'"
            )
            print(f"eval_gold_scripts rows with segment_type='glimpse' remaining: {n_glimpse_remaining} (expect 0)")

            n_active_intro = await conn.fetchval(
                "SELECT count(*) FROM harness.eval_gold_scripts "
                "WHERE segment_type = 'intro' AND superseded_at IS NULL AND run_id = ANY($1::uuid[])",
                gold_run_ids,
            )
            print(f"Active 'intro' golden scripts among gold runs: {n_active_intro} (expect {len(gold_run_ids)})")

            n_active_gold = await conn.fetchval(
                "SELECT count(*) FROM harness.eval_gold_runs WHERE is_active = true"
            )
            print(f"Active gold runs: {n_active_gold} (expect {len(gold_run_ids)})")


asyncio.run(main())
