# Next To-Dos

## UX
- Design and build the user-facing interface
  - Surface idea list with angle + recommended format prominently
  - Let user pick an idea, confirm or change format, trigger generation
  - Show generation progress (briefing → outline → transcript → audio)
  - Playback UI for generated episodes

## Audio
- Add intro music per show/format
- Add outro music per show/format
- Add optional mid-episode break music
- Wire into `synthesize_and_stitch` — inject music segments around spoken audio

## Fixes
- Fix duplicate ideas in `save_ideas` — deduplicate before writing
- Re-embed the 3 missing sources (Dostoevsky, intimacy article, Putting Ideas into Words) — run embed manually or fix background task lifecycle in `ingest.py`
- Rename `show_name` parameter to `profile_name` in `generator.py` `generate_outline` and `generate_transcript`
- Update compare UI to reflect new pipeline — show `format`, `speaker`, briefing as formatted JSON not prose

## Bookmark / Ingest trigger
- Think through what happens when a new bookmark is added
  - Does it trigger idea regeneration automatically?
  - Does it get added to the pool and flagged as "new" for the next generation run?
  - Should a single new source be able to surface as a solo idea immediately?
  - Does it re-cluster everything or just evaluate the new source against existing clusters?
