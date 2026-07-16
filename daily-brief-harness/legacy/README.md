# Legacy modules

These modules are **not imported by the live app** (nothing in `app/main.py` or
anywhere else under `app/` references this package). They're kept purely as
reference — real, working integration code that will very likely be needed
again once the two-phase Pre-Opt + Per-User Brief Cache pipeline (`app/services/preopt_runner.py`
+ `app/services/user_brief_runner.py` — the entire Daily Brief feature going
forward) gets wired up to real production scheduling, TTS, storage, and push.

- **`tts_service.py`** — Smallest.ai Lightning TTS integration (chunked synthesis → WAV).
- **`storage_service.py`** — R2/S3-compatible audio upload via `boto3`.
- **`push_service.py`** — Firebase FCM "brief ready" push notification.
- **`calendar_service.py`** — Google Calendar service-account read (today's schedule).
- **`db_service.py`** — asyncpg helpers for the old production `public.daily_briefs`
  table. This table/schema belonged to the retired single-whole-transcript
  pipeline; if a real production DB is wired up for the two-phase pipeline,
  it should target `harness.daily_briefs` (see `db/001_schema.sql`) instead of
  reviving this table.
- **`brief_runner.py`** — the old production batch-runner orchestration
  (single whole-brief Stage 4/5 call, no per-article segment cache, no
  separate intro/glimpse/outro). **Will not import as-is** — it depends on
  `news_service.fetch_articles_for_profile`, `pipeline.OutlineRequest`,
  `pipeline.TranscriptRequest`, `pipeline.run_outline_step`,
  `pipeline.run_transcript_step`, and `UserProfile.subtopics`, all of which
  were permanently deleted (not archived) from `app/services/` when System A
  (the old single-profile dashboard) and this old production path were
  retired. Treat it as inert reference text for the orchestration shape, not
  a revivable module — the actual real-TTS/storage/push wiring in the other
  four files here is the reusable part.

## Reviving any of these

`google-auth`, `google-api-python-client`, `boto3`, and `firebase-admin` were
removed from the top-level `requirements.txt` (they were only used by these
archived modules plus the now-deleted `history_service.py`). Reinstall
whichever ones the revived module needs before reviving it.
