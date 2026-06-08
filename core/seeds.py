"""
core/seeds.py
Seed URL registry — the three example links shown to first-time users.

Each seed URL has a canonical normalized form. When a user submits one of
these URLs, the ingest handler short-circuits: instead of running the full
scrape → transform → embed → generate pipeline, it copies the pre-computed
source rows and attaches the pre-baked episode instantly.

Run `scripts/setup_seeds.py` once (locally or in CI) to populate the DB.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedEntry:
    url: str           # canonical URL (normalized, no trailing slash)
    format_name: str   # backend format name for the pre-baked episode


SEED_ENTRIES: list[SeedEntry] = [
    SeedEntry(
        url="http://www.incompleteideas.net/IncIdeas/BitterLesson.html",
        format_name="exploration_engine",   # strong thesis + counterpoint material
    ),
    SeedEntry(
        url="http://paulgraham.com/ds.html",
        format_name="momentum_loop",        # punchy single insight — do the unscalable thing
    ),
    SeedEntry(
        url="https://www.nngroup.com/articles/ten-usability-heuristics/",
        format_name="clarity_engine",       # dense reference unpacked step-by-step
    ),
]

# Fast lookup: normalized URL → SeedEntry
SEED_URL_MAP: dict[str, SeedEntry] = {e.url: e for e in SEED_ENTRIES}

# Also index without scheme variations for robustness
_SEED_URL_STRIPPED: dict[str, SeedEntry] = {}
for _entry in SEED_ENTRIES:
    _stripped = _entry.url.replace("http://", "").replace("https://", "").rstrip("/")
    _SEED_URL_STRIPPED[_stripped] = _entry


def find_seed(url: str) -> SeedEntry | None:
    """Return the SeedEntry for a URL if it is a known seed URL, else None."""
    if url in SEED_URL_MAP:
        return SEED_URL_MAP[url]
    stripped = url.replace("http://", "").replace("https://", "").rstrip("/")
    return _SEED_URL_STRIPPED.get(stripped)
