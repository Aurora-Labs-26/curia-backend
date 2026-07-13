"""
core/taxonomy/buckets.py
The topic-bucket taxonomy used for source tagging (see "topics v1.md").

Base: IAB Content Taxonomy Tier 1 → Tier 2, with 'Pets' excluded, plus 4 custom
Tier-1s (marked custom=True) for Curia's longform-essay corpus.

Pure data + derived lookup maps. No LLM, no network. Label strings are FROZEN
identifiers — renaming one requires migrating stored `source.topics` rows.
Numeric IDs (Tier-1 `23`, Tier-2 `"23.1"`) are the LLM wire format and never change.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Taxonomy — order defines Tier-1 IDs (1-based); Tier-2 IDs are "N.i" (1-based).
# ---------------------------------------------------------------------------

TAXONOMY: dict[str, dict] = {
    "Automotive": {"custom": False, "tier2": [
        "Auto Body Styles", "Auto Type", "Car Culture", "Dash Cam Videos",
        "Motorcycles", "Road-Side Assistance", "Scooters", "Auto Buying and Selling",
        "Auto Insurance", "Auto Parts", "Auto Recalls", "Auto Repair", "Auto Safety",
        "Auto Shows", "Auto Technology", "Auto Rentals",
    ]},
    "Books and Literature": {"custom": False, "tier2": [
        "Art and Photography Books", "Biographies", "Children's Literature",
        "Comics and Graphic Novels", "Cookbooks", "Fiction", "Poetry",
        "Travel Books", "Young Adult Literature",
    ]},
    "Business and Finance": {"custom": False, "tier2": [
        "Business", "Economy", "Industries",
    ]},
    "Careers": {"custom": False, "tier2": [
        "Apprenticeships", "Career Advice", "Career Planning", "Job Search",
        "Remote Working", "Vocational Training",
    ]},
    "Education": {"custom": False, "tier2": [
        "Adult Education", "Private School", "Secondary Education",
        "Special Education", "College Education", "Early Childhood Education",
        "Educational Assessment", "Homeschooling", "Homework and Study",
        "Language Learning", "Online Education", "Primary Education",
    ]},
    "Events and Attractions": {"custom": False, "tier2": [
        "Amusement and Theme Parks", "Fashion Events",
        "Historic Site and Landmark Tours", "Malls & Shopping Centers",
        "Museums & Galleries", "Musicals", "National & Civic Holidays",
        "Nightclubs", "Outdoor Activities", "Parks & Nature",
        "Party Supplies and Decorations", "Awards Shows",
        "Personal Celebrations & Life Events", "Political Event",
        "Religious Events", "Sporting Events", "Theater Venues and Events",
        "Zoos & Aquariums", "Bars & Restaurants", "Business Expos & Conferences",
        "Casinos & Gambling", "Cinemas and Events", "Comedy Events",
        "Concerts & Music Events", "Fan Conventions",
    ]},
    "Family and Relationships": {"custom": False, "tier2": [
        "Bereavement", "Dating", "Divorce", "Eldercare",
        "Marriage and Civil Unions", "Parenting", "Single Life",
    ]},
    "Fine Art": {"custom": False, "tier2": [
        "Costume", "Dance", "Design", "Digital Arts", "Fine Art Photography",
        "Modern Art", "Opera", "Theater",
    ]},
    "Food & Drink": {"custom": False, "tier2": [
        "Alcoholic Beverages", "Vegan Diets", "Vegetarian Diets", "World Cuisines",
        "Barbecues and Grilling", "Cooking", "Desserts and Baking", "Dining Out",
        "Food Allergies", "Food Movements", "Healthy Cooking and Eating",
        "Non-Alcoholic Beverages",
    ]},
    "Healthy Living": {"custom": False, "tier2": [
        "Children's Health", "Fitness and Exercise", "Men's Health", "Nutrition",
        "Senior Health", "Weight Loss", "Wellness", "Women's Health",
    ]},
    "Hobbies & Interests": {"custom": False, "tier2": [
        "Antiquing and Antiques", "Magic and Illusion", "Model Toys",
        "Musical Instruments", "Paranormal Phenomena", "Radio Control",
        "Sci-fi and Fantasy", "Workshops and Classes", "Arts and Crafts",
        "Beekeeping", "Birdwatching", "Cigars", "Collecting", "Content Production",
        "Games and Puzzles", "Genealogy and Ancestry",
    ]},
    "Home & Garden": {"custom": False, "tier2": [
        "Gardening", "Remodeling & Construction", "Smart Home", "Home Appliances",
        "Home Entertaining", "Home Improvement", "Home Security",
        "Indoor Environmental Quality", "Interior Decorating", "Landscaping",
        "Outdoor Decorating",
    ]},
    "Medical Health": {"custom": False, "tier2": [
        "Diseases and Conditions", "Medical Tests", "Pharmaceutical Drugs",
        "Surgery", "Vaccines", "Cosmetic Medical Services",
    ]},
    "Movies": {"custom": False, "tier2": [
        "Action and Adventure Movies", "Romance Movies", "Science Fiction Movies",
        "Indie and Arthouse Movies", "Animation Movies", "Comedy Movies",
        "Crime and Mystery Movies", "Documentary Movies", "Drama Movies",
        "Family and Children Movies", "Fantasy Movies", "Horror Movies",
        "World Movies",
    ]},
    "Music and Audio": {"custom": False, "tier2": [
        "Adult Contemporary Music", "Adult Album Alternative", "Alternative Music",
        "Children's Music", "Classic Hits", "Classical Music", "College Radio",
        "Comedy", "Contemporary Hits/Pop/Top 40", "Country Music",
        "Dance and Electronic Music", "World/International Music",
        "Songwriters/Folk", "Gospel Music", "Hip Hop Music",
        "Inspirational/New Age Music", "Jazz", "Oldies/Adult Standards", "Reggae",
        "Blues", "Religious", "R&B/Soul/Funk", "Rock Music",
        "Soundtracks TV and Showtunes", "Sports Radio", "Talk Radio",
        "Urban Contemporary Music", "Variety",
    ]},
    "News and Politics": {"custom": False, "tier2": [
        "Crime", "Disasters", "International News", "Law", "Local News",
        "National News", "Politics", "Weather",
    ]},
    "Personal Finance": {"custom": False, "tier2": [
        "Consumer Banking", "Financial Assistance", "Financial Planning",
        "Frugal Living", "Insurance", "Personal Debt", "Personal Investing",
        "Personal Taxes", "Retirement Planning", "Home Utilities",
    ]},
    "Pop Culture": {"custom": False, "tier2": [
        "Celebrity Deaths", "Celebrity Families", "Celebrity Homes",
        "Celebrity Pregnancy", "Celebrity Relationships", "Celebrity Scandal",
        "Celebrity Style", "Humor and Satire",
    ]},
    "Real Estate": {"custom": False, "tier2": [
        "Apartments", "Retail Property", "Vacation Properties",
        "Developmental Sites", "Hotel Properties", "Houses", "Industrial Property",
        "Land and Farms", "Office Property", "Real Estate Buying and Selling",
        "Real Estate Renting and Leasing",
    ]},
    "Religion & Spirituality": {"custom": False, "tier2": [
        "Agnosticism", "Spirituality", "Astrology", "Atheism", "Buddhism",
        "Christianity", "Hinduism", "Islam", "Judaism", "Sikhism",
    ]},
    "Science": {"custom": False, "tier2": [
        "Biological Sciences", "Chemistry", "Environment", "Genetics",
        "Geography", "Geology", "Physics", "Space and Astronomy",
    ]},
    "Shopping": {"custom": False, "tier2": [
        "Coupons and Discounts", "Flower Shopping", "Gifts and Greetings Cards",
        "Grocery Shopping", "Holiday Shopping", "Household Supplies",
        "Lotteries and Scratchcards", "Sales and Promotions",
        "Children's Games and Toys",
    ]},
    "Sports": {"custom": False, "tier2": [
        "American Football", "Boxing", "Cheerleading", "College Sports", "Cricket",
        "Cycling", "Darts", "Disabled Sports", "Diving", "Equine Sports",
        "Extreme Sports", "Australian Rules Football", "Fantasy Sports",
        "Field Hockey", "Figure Skating", "Fishing Sports", "Golf", "Gymnastics",
        "Hunting and Shooting", "Ice Hockey", "Inline Skating", "Lacrosse",
        "Auto Racing", "Martial Arts", "Olympic Sports",
        "Poker and Professional Gambling", "Rodeo", "Rowing", "Rugby", "Sailing",
        "Skiing", "Snooker/Pool/Billiards", "Soccer", "Badminton", "Softball",
        "Squash", "Swimming", "Table Tennis", "Tennis", "Track and Field",
        "Volleyball", "Walking", "Water Polo", "Weightlifting", "Baseball",
        "Wrestling", "Basketball", "Beach Volleyball", "Bodybuilding", "Bowling",
        "Sports Equipment",
    ]},
    "Style & Fashion": {"custom": False, "tier2": [
        "Beauty", "Women's Fashion", "Body Art", "Children's Clothing",
        "Designer Clothing", "Fashion Trends", "High Fashion", "Men's Fashion",
        "Personal Care", "Street Style",
    ]},
    "Technology & Computing": {"custom": False, "tier2": [
        "Artificial Intelligence", "Augmented Reality", "Computing",
        "Consumer Electronics", "Robotics", "Virtual Reality",
    ]},
    "Television": {"custom": False, "tier2": [
        "Animation TV", "Soap Opera TV", "Special Interest TV", "Sports TV",
        "Children's TV", "Comedy TV", "Drama TV", "Factual TV", "Holiday TV",
        "Music TV", "Reality TV", "Science Fiction TV",
    ]},
    "Travel": {"custom": False, "tier2": [
        "Travel Accessories", "Travel Locations",
        "Travel Preparation and Advice", "Travel Type",
    ]},
    "Video Gaming": {"custom": False, "tier2": [
        "Console Games", "eSports", "Mobile Games", "PC Games",
        "Video Game Genres",
    ]},
    # ── Custom Tier-1s (not IAB) — Curia's essay corpus ─────────────────────
    "Philosophy": {"custom": True, "tier2": [
        "Ethics & Morality", "Epistemology & Rationality", "Political Philosophy",
        "Philosophy of Mind", "Metaphysics", "Applied Philosophy",
    ]},
    "Psychology & Self": {"custom": True, "tier2": [
        "Cognitive Science", "Behavioral Psychology",
        "Mental Models & Decision-Making", "Productivity & Habits",
        "Emotions & Wellbeing", "Social Psychology",
    ]},
    "History": {"custom": True, "tier2": [
        "Ancient History", "Modern History", "History of Science & Technology",
        "Economic History", "Military History", "Biographical History",
    ]},
    "Media & Journalism": {"custom": True, "tier2": [
        "Journalism & Publishing", "Social Media & Platforms", "Internet Culture",
        "Advertising & Attention Economy", "Creator Economy",
    ]},
}

# ---------------------------------------------------------------------------
# Derived ID maps (built once at import)
# ---------------------------------------------------------------------------

TIER1_BY_ID: dict[int, str] = {}          # 23 → "Sports"
TIER1_ID: dict[str, int] = {}             # "Sports" → 23
TIER2_BY_ID: dict[str, tuple[str, str]] = {}   # "23.33" → ("Sports", "Soccer")

for _i, (_t1, _spec) in enumerate(TAXONOMY.items(), start=1):
    TIER1_BY_ID[_i] = _t1
    TIER1_ID[_t1] = _i
    for _j, _t2 in enumerate(_spec["tier2"], start=1):
        TIER2_BY_ID[f"{_i}.{_j}"] = (_t1, _t2)


def is_valid(tier1: str, tier2: str | None = None) -> bool:
    if tier1 not in TAXONOMY:
        return False
    if tier2 is None:
        return True
    return tier2 in TAXONOMY[tier1]["tier2"]


def render_taxonomy() -> str:
    """ID-coded taxonomy for the judge prompt. Static — compute once, cache."""
    lines = []
    for i, (t1, spec) in enumerate(TAXONOMY.items(), start=1):
        subs = ", ".join(f"{i}.{j} {t2}" for j, t2 in enumerate(spec["tier2"], start=1))
        lines.append(f"{i} {t1}: {subs}")
    return "\n".join(lines)


TAXONOMY_PROMPT: str = render_taxonomy()

# ---------------------------------------------------------------------------
# Deterministic pins — publisher-declared structure ONLY (no content matching).
# ---------------------------------------------------------------------------

# Exact normalized hostname (lowercase, no "www.") → (tier1, tier2-or-None).
# Single-topic sites only — a hostname that publishes across topics must NOT
# be here (learned pins handle those case-by-case with evidence thresholds).
SEED_DOMAIN_PINS: dict[str, tuple[str, str | None]] = {
    "espn.com": ("Sports", None),
    "bleacherreport.com": ("Sports", None),
    "skysports.com": ("Sports", None),
    "theathletic.com": ("Sports", None),
    "techcrunch.com": ("Technology & Computing", None),
    "theverge.com": ("Technology & Computing", None),
    "arstechnica.com": ("Technology & Computing", None),
    "engadget.com": ("Technology & Computing", None),
    "tomshardware.com": ("Technology & Computing", None),
    "ign.com": ("Video Gaming", None),
    "polygon.com": ("Video Gaming", None),
    "kotaku.com": ("Video Gaming", None),
    "gamespot.com": ("Video Gaming", None),
    "eurogamer.net": ("Video Gaming", None),
    "pitchfork.com": ("Music and Audio", None),
    "billboard.com": ("Music and Audio", None),
    "webmd.com": ("Medical Health", None),
    "mayoclinic.org": ("Medical Health", None),
    "healthline.com": ("Medical Health", None),
    "nasa.gov": ("Science", "Space and Astronomy"),
    "scientificamerican.com": ("Science", None),
    "quantamagazine.org": ("Science", None),
    "vogue.com": ("Style & Fashion", None),
    "elle.com": ("Style & Fashion", None),
    "eater.com": ("Food & Drink", None),
    "seriouseats.com": ("Food & Drink", None),
    "bonappetit.com": ("Food & Drink", None),
    "caranddriver.com": ("Automotive", None),
    "motortrend.com": ("Automotive", None),
    "jalopnik.com": ("Automotive", None),
    "politico.com": ("News and Politics", "Politics"),
    "lithub.com": ("Books and Literature", None),
    "zillow.com": ("Real Estate", None),
    "nerdwallet.com": ("Personal Finance", None),
    "investopedia.com": ("Personal Finance", None),
}

# Exact URL path segment → (tier1, tier2-or-None). The publisher's own section
# label for the article — beats a domain pin on conflict. Unambiguous slugs only.
SECTION_SLUGS: dict[str, tuple[str, str | None]] = {
    "sport": ("Sports", None),
    "sports": ("Sports", None),
    "soccer": ("Sports", "Soccer"),
    "football": ("Sports", "Soccer"),      # non-US publishers; US NFL uses /nfl/
    "nfl": ("Sports", "American Football"),
    "nba": ("Sports", "Basketball"),
    "mlb": ("Sports", "Baseball"),
    "nhl": ("Sports", "Ice Hockey"),
    "tennis": ("Sports", "Tennis"),
    "golf": ("Sports", "Golf"),
    "cricket": ("Sports", "Cricket"),
    "tech": ("Technology & Computing", None),
    "technology": ("Technology & Computing", None),
    "politics": ("News and Politics", "Politics"),
    "business": ("Business and Finance", "Business"),
    "economy": ("Business and Finance", "Economy"),
    "science": ("Science", None),
    "health": ("Healthy Living", None),
    "food": ("Food & Drink", None),
    "travel": ("Travel", None),
    "music": ("Music and Audio", None),
    "movies": ("Movies", None),
    "film": ("Movies", None),
    "books": ("Books and Literature", None),
    "style": ("Style & Fashion", None),
    "fashion": ("Style & Fashion", None),
    "realestate": ("Real Estate", None),
    "education": ("Education", None),
}

# Fail at import if any pin target has a typo — a bad pin is worse than no pin.
for _host, (_t1, _t2) in SEED_DOMAIN_PINS.items():
    assert is_valid(_t1, _t2), f"SEED_DOMAIN_PINS[{_host!r}] → invalid label ({_t1!r}, {_t2!r})"
for _slug, (_t1, _t2) in SECTION_SLUGS.items():
    assert is_valid(_t1, _t2), f"SECTION_SLUGS[{_slug!r}] → invalid label ({_t1!r}, {_t2!r})"
