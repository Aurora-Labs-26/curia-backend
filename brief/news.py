import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import html
import re
import email.utils
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from loguru import logger

# BEATS — the 7 broad news beats (replaces the old 21-category IAB-style
# content taxonomy, which was built for classifying content, not for
# surfacing news: too granular for users to meaningfully pick from, and too narrow
# individually to justify a dedicated GNews query each).
#
# Every beat maps to Google's own curated topic-vertical feed(s) ("topic_codes" —
# hits /rss/headlines/section/topic/{CODE}, the same feed behind news.google.com's
# own nav tabs: continuously refreshed and recency-heavy by construction — verified
# BUSINESS's topic feed was 92% within 30h, oldest item 35h) EXCEPT Lifestyle,
# which has no matching Google vertical and falls back to a permanent curated
# OR-keyword search ("main_terms") as its only source, always.
BEATS: Dict[str, Dict[str, Any]] = {
    "Tech": {"topic_codes": ["TECHNOLOGY"]},
    "Business": {"topic_codes": ["BUSINESS"]},
    "World": {"topic_codes": ["WORLD"]},
    "Science & Health": {"topic_codes": ["SCIENCE", "HEALTH"]},
    "Culture": {"topic_codes": ["ENTERTAINMENT"]},
    "Lifestyle": {
        "topic_codes": [],  # no matching Google topic vertical — always keyword search
        "main_terms": ["travel", "food", "restaurant", "recipe", "fashion", '"home improvement"', "gardening", '"real estate"', "relationships", "parenting", "wellness", "fitness", "shopping"],
    },
    "Sports": {"topic_codes": ["SPORTS"]},
}

# ---------------------------------------------------------------------------
# COUNTRY-SPECIFIC GEO EDITIONS — a dedicated ISO-3166-1-alpha-2 country
# code, used to pick the national edition for "mixed"/"national"
# BEAT_GEO_MODE beats. Separate from the freeform location name
# (harness.users.location_name) used for local-news resolution below.
# hl is that edition's interface language: English where Google News actually
# serves an English edition for that country, otherwise the country's
# dominant language (Google News has no en-DE/en-FR/en-JP editions, etc).
# ---------------------------------------------------------------------------
GLOBAL_GEO = ("US", "en-US", "US:en")

COUNTRY_GEO_PARAMS: Dict[str, tuple] = {
    "US": GLOBAL_GEO,
    "IN": ("IN", "en-IN", "IN:en"),
    "GB": ("GB", "en-GB", "GB:en"),
    "CA": ("CA", "en-CA", "CA:en"),
    "AU": ("AU", "en-AU", "AU:en"),
    "IE": ("IE", "en-IE", "IE:en"),
    "NZ": ("NZ", "en-NZ", "NZ:en"),
    "SG": ("SG", "en-SG", "SG:en"),
    "ZA": ("ZA", "en-ZA", "ZA:en"),
    "AE": ("AE", "en-AE", "AE:en"),
    "PK": ("PK", "en-PK", "PK:en"),
    "NG": ("NG", "en-NG", "NG:en"),
    "PH": ("PH", "en-PH", "PH:en"),
    "MY": ("MY", "en-MY", "MY:en"),
    "KE": ("KE", "en-KE", "KE:en"),
    "DE": ("DE", "de", "DE:de"),
    "FR": ("FR", "fr", "FR:fr"),
    "ES": ("ES", "es", "ES:es"),
    "IT": ("IT", "it", "IT:it"),
    "BR": ("BR", "pt-419", "BR:pt-419"),
    "MX": ("MX", "es-419", "MX:es-419"),
    "JP": ("JP", "ja", "JP:ja"),
    "KR": ("KR", "ko", "KR:ko"),
}

# Per-beat geo-fetch mode — exactly 3 buckets, governing each beat's own
# topic-feed/search fetch below.
#   "global"   — single global (US) edition only. World is global by
#                definition; Tech/Science & Health/Sports news is dominated
#                by a small set of players covered everywhere already.
#   "mixed"    — global (US) edition AND the user's national edition, merged
#                (callers own URL/title dedup across the combined results).
#                Business and Culture both carry heavy region-specific
#                coverage — local market moves, home-market earnings/IPOs,
#                regional box office and streaming news — that the
#                global-only US edition routinely misses.
#   "national" — the user's national edition only, no global component.
#                Lifestyle (food/travel/fashion/etc.) is inherently local-
#                flavored and has no topic-vertical feed to begin with
#                (keyword search only) — global US lifestyle content isn't
#                the point here the way global market/tech/sports news is.
# "mixed" and "national" both fall back to DEFAULT_NATIONAL_GEO (India) when
# no usable country was supplied, rather than silently collapsing to global —
# see _geo_variants_for_beat.
BEAT_GEO_MODE: Dict[str, str] = {
    "Tech": "global",
    "Business": "mixed",
    "World": "global",
    "Science & Health": "global",
    "Culture": "mixed",
    "Lifestyle": "national",
    "Sports": "global",
}

# Fallback national edition for "mixed"/"national" beats when no usable
# country was supplied — mirrors the dashboard's own default Country
# selection and constitution.md's "default location is India".
DEFAULT_NATIONAL_GEO = COUNTRY_GEO_PARAMS["IN"]

# Per-QUERY article request cap — every distinct query a beat (or a custom
# topic) issues gets this full budget, independently of whatever else is
# running alongside it:
#   beat's own topic feed(s) (or base keyword search, for beats with no
#     topic code)                             -> BEAT_ARTICLE_CAP, always
#   each custom topic (independent of beats)   -> its own BEAT_ARTICLE_CAP
BEAT_ARTICLE_CAP = 50

# ---------------------------------------------------------------------------
# COMPATIBILITY CLUSTERS — DEACTIVATED. This was curated at the old, narrow
# IAB-category level: pairs of categories thematically adjacent enough to combine
# into one query without collision noise (tech+business, careers+education, etc).
# The new Beats are deliberately broad (7 total, replacing 21 narrow categories),
# so there's no narrower adjacent pairing left to curate — combining two already-
# broad beats would just reintroduce the keyword-collision noise this was built to
# avoid. Left here commented out rather than deleted in case beats are ever split
# back into narrower sub-categories where clustering would make sense again.
#
# IAB_COMPATIBILITY_CLUSTERS: List[frozenset] = [
#     frozenset({"Technology & Computing", "Business and Finance"}),
#     frozenset({"Movies", "Television", "Music and Audio", "Events, Attractions & Pop Culture"}),
#     frozenset({"Real Estate", "Home & Garden"}),
#     frozenset({"Careers", "Education"}),
#     frozenset({"Style & Fashion", "Shopping"}),
#     frozenset({"Family and Relationships", "Healthy Living & medical health", "Religion & Spirituality"}),
#     frozenset({"Food & Drink", "Travel", "Hobbies & Interests"}),
# ]
# ---------------------------------------------------------------------------

class NewsService:
    def __init__(self):
        pass

    def _get_geo_params(self, is_global: bool, location: str) -> tuple:
        """Determines the geographical perspectives for the RSS query."""
        if is_global:
            return "US", "en-US", "US:en"

        # Token match, not substring — the old `"in" in loc_lower` resolved
        # "Berlin" and "China" to the India edition. Legacy fallback only:
        # rows saved through the city picker carry a real country code and
        # never reach this (see geo_for_country).
        tokens = {t.strip(".,") for t in location.lower().split()}
        if tokens & {"india", "in", "bangalore", "bengaluru", "delhi", "mumbai", "hyderabad", "chennai"}:
            return "IN", "en-IN", "IN:en"

        return "US", "en-US", "US:en"

    def _geo_variants_for_beat(self, beat: str, country: str) -> List[tuple]:
        """(gl, hl, ceid) editions to fetch for this beat's own topic-feed/
        search fetch, per its BEAT_GEO_MODE:
          global   -> [GLOBAL_GEO] always.
          mixed    -> [GLOBAL_GEO, national], unless national == GLOBAL_GEO
                      (the user's country IS the US), in which case there's
                      nothing distinct to split — just [GLOBAL_GEO].
          national -> [national] only, no global component.
        `national` is the user's COUNTRY_GEO_PARAMS edition if `country` was
        supplied and recognized, else DEFAULT_NATIONAL_GEO (India) — mixed/
        national modes always resolve to an actual national edition rather
        than silently collapsing to global when country is unset.
        Orthogonal to local-news resolution, which uses a freeform location
        name instead — see _get_geo_params/resolve_local_geo."""
        mode = BEAT_GEO_MODE.get(beat, "global")
        if mode == "global":
            return [GLOBAL_GEO]

        national = COUNTRY_GEO_PARAMS.get(country.upper()) if country else None
        if not national:
            national = DEFAULT_NATIONAL_GEO

        if mode == "national":
            return [national]

        # mixed
        if national == GLOBAL_GEO:
            return [GLOBAL_GEO]
        return [GLOBAL_GEO, national]

    def _fetch_rss_url(self, url: str, max_results: int = 15) -> List[Dict[str, Any]]:
        """Performs a high-performance XML GET and parses RSS items, filtering to the last 30 hours."""
        articles = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=30)
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            items = root.findall(".//item")

            for item in items:
                if len(articles) >= max_results:
                    break

                title = item.find("title").text if item.find("title") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                description = item.find("description").text if item.find("description") is not None else ""
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""

                # Filter by date — skip anything older than 20 hours
                if pub_date:
                    try:
                        parsed_dt = email.utils.parsedate_to_datetime(pub_date)
                        if parsed_dt < cutoff:
                            continue
                    except Exception:
                        pass  # unparseable date: include with benefit of the doubt

                title_clean = title.strip()

                # Derive source from the RSS <source> element; fall back to
                # guessing from the title's "Headline - Publisher" suffix
                # (title itself is left untouched); null if neither is present.
                source_elem = item.find("source")
                source = source_elem.text.strip() if source_elem is not None and source_elem.text else ""
                if not source and " - " in title_clean:
                    source = title_clean.rsplit(" - ", 1)[1].strip()
                source = source or None

                # Clean description snippet (Google News description embeds HTML and duplicate title/source links)
                description_clean = description
                if description.startswith("<a href="):
                    description_clean = re.sub(r'<[^>]+>', '', description)
                    description_clean = html.unescape(description_clean)
                    if " - " in description_clean:
                        description_clean = description_clean.rsplit(" - ", 1)[0]

                articles.append({
                    "title": title_clean,
                    "description": description_clean or "No description available.",
                    "source": source,
                    "url": link,
                    "published_date": pub_date
                })
        except Exception as e:
            logger.error(f"Failed to fetch or parse RSS from URL: {url}. Error: {e}")
        return articles

    def geo_for_country(self, country_code: str, location_name: str = "") -> tuple:
        """News edition from a real ISO country code (the geocoder's, stored
        on the user row since 0038). Unknown/missing code falls back to the
        legacy keyword sniff of the location string."""
        cc = (country_code or "").upper()
        if cc in COUNTRY_GEO_PARAMS:
            return COUNTRY_GEO_PARAMS[cc]
        return self._get_geo_params(False, location_name)

    def resolve_local_geo(self, location_name: str) -> tuple:
        """Public wrapper around _get_geo_params for a freeform place name
        (e.g. "Mumbai") — used by callers outside this module (user_brief_runner)
        that need local-news geo resolution without a full UserProfile."""
        return self._get_geo_params(False, location_name)

    def fetch_articles_for_beat(self, beat: str, country: str = "", max_results: int = BEAT_ARTICLE_CAP) -> List[Dict[str, Any]]:
        """Fetches one Beat's own topic-feed/base-query articles only — no
        dedup (callers own dedup). Each returned dict is tagged `topic=beat`.
        Called by preopt_runner, once per system topic (Beat), at the full
        BEAT_ARTICLE_CAP, independent of any user profile.
        """
        cfg = BEATS[beat]
        topic_codes = cfg["topic_codes"]
        geo_variants = self._geo_variants_for_beat(beat, country)
        fetched: List[Dict[str, Any]] = []

        if topic_codes:
            # Topic-feed fetch — split evenly across every (topic code x geo
            # edition) combination, same logic as the original inline block.
            combos = [(code, geo) for code in topic_codes for geo in geo_variants]
            per_combo_cap = max_results // len(combos)
            remainder = max_results % len(combos)
            for i, (code, (v_gl, v_hl, v_ceid)) in enumerate(combos):
                cap = per_combo_cap + (1 if i < remainder else 0)
                topic_url = f"https://news.google.com/rss/headlines/section/topic/{code}?hl={v_hl}&gl={v_gl}&ceid={v_ceid}"
                logger.info(f"Fetching topic-feed RSS for beat [{beat}] code [{code}] edition [{v_ceid}] (cap {cap}) via URL: {topic_url}")
                fetched.extend(self._fetch_rss_url(topic_url, max_results=cap))
        else:
            # No matching Google topic vertical for this beat (Lifestyle) —
            # its one permanent curated query stands in for the topic feed.
            base_query = " OR ".join(cfg["main_terms"])
            fetched.extend(self._fetch_query_across_geos(
                base_query, geo_variants, max_results, f"base RSS feed for beat [{beat}]"
            ))

        for art in fetched:
            art["topic"] = beat
        return fetched

    def fetch_articles_for_topic(
        self, topic_query: str, geo_gl: str, geo_hl: str, geo_ceid: str, max_results: int, tag: str
    ) -> List[Dict[str, Any]]:
        """Fetches one arbitrary keyword-search query, single edition, no
        dedup (callers own dedup). Generalizes what used to be the inline
        custom-topics loop body (hardcoded GLOBAL_GEO / "Custom: {topic}")
        to an explicit geo triple + tag. Each returned dict is tagged `topic=tag`.

        Reused by:
          - user_brief_runner, once per user custom topic (GLOBAL_GEO, "Custom: {name}", 20 articles)
          - user_brief_runner, once for local news (resolve_local_geo(location_name), "Local: {location_name}", 20 articles)
        """
        search_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(topic_query)}&hl={geo_hl}&gl={geo_gl}&ceid={geo_ceid}"
        logger.info(f"Fetching topic-query RSS search for [{topic_query}] tag [{tag}] (cap {max_results}) via URL: {search_url}")
        fetched = self._fetch_rss_url(search_url, max_results=max_results)
        for art in fetched:
            art["topic"] = tag
        return fetched

    def _fetch_query_across_geos(self, query: str, geo_variants: List[tuple], total_cap: int, log_label: str) -> List[Dict[str, Any]]:
        """Splits `total_cap` evenly across each (gl, hl, ceid) in `geo_variants` and
        issues one search request per edition, returning the combined raw results
        (caller is responsible for dedup). Used for any keyword search that
        needs to respect a beat's BEAT_GEO_MODE editions — Lifestyle's base
        query, currently the only caller."""
        results = []
        per_variant_cap = total_cap // len(geo_variants)
        remainder = total_cap % len(geo_variants)
        for i, (v_gl, v_hl, v_ceid) in enumerate(geo_variants):
            cap = per_variant_cap + (1 if i < remainder else 0)
            search_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl={v_hl}&gl={v_gl}&ceid={v_ceid}"
            logger.info(f"Fetching {log_label} edition [{v_ceid}] (cap {cap}) via URL: {search_url}")
            results.extend(self._fetch_rss_url(search_url, max_results=cap))
        return results

news_service = NewsService()
