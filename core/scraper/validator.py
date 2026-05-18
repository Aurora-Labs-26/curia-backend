"""
core/scraper/validator.py
Regex-based URL validation. Rejects URLs that will never yield useful article content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""


# ── Blocked domain patterns ──────────────────────────────────────────────────

_VIDEO = re.compile(
    r"(youtube\.com|youtu\.be|vimeo\.com|tiktok\.com|twitch\.tv|dailymotion\.com|rumble\.com)",
    re.IGNORECASE,
)

# Twitter/X is allowed — threads can be good content
_SOCIAL = re.compile(
    r"(instagram\.com|facebook\.com|fb\.com|snapchat\.com|threads\.net|pinterest\.com)",
    re.IGNORECASE,
)

_SHOPPING = re.compile(
    r"(amazon\.(com|co|in|de|fr|co\.uk|co\.jp)|ebay\.com|walmart\.com|flipkart\.com"
    r"|alibaba\.com|aliexpress\.com|etsy\.com|shopify\.com|target\.com|bestbuy\.com"
    r"|wish\.com|shein\.com|myntra\.com|nykaa\.com|ajio\.com)",
    re.IGNORECASE,
)

_ADULT = re.compile(
    r"(pornhub\.com|xvideos\.com|xnxx\.com|onlyfans\.com|redtube\.com"
    r"|chaturbate\.com|xhamster\.com|brazzers\.com|bangbros\.com"
    r"|rule34\.xxx|nhentai\.net|hentai|porn)",
    re.IGNORECASE,
)

_SEARCH = re.compile(
    r"(google\.com/search|bing\.com/search|duckduckgo\.com/\?q|yahoo\.com/search)",
    re.IGNORECASE,
)

_MESSAGING = re.compile(
    r"(wa\.me|web\.whatsapp\.com|t\.me|telegram\.org|discord\.(gg|com)|slack\.com)",
    re.IGNORECASE,
)

_FILE_EXTENSIONS = re.compile(
    r"\.(pdf|zip|rar|7z|tar|gz|exe|dmg|iso|apk"
    r"|jpg|jpeg|png|gif|bmp|svg|webp|ico"
    r"|mp3|mp4|avi|mov|mkv|wav|flac|ogg"
    r"|csv|xlsx|xls|doc|docx|ppt|pptx)(\?.*)?$",
    re.IGNORECASE,
)

_PRIVATE_IP = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$"
)


# ── Validator ─────────────────────────────────────────────────────────────────


def validate_url(url: str) -> ValidationResult:
    """
    Check if a URL is worth scraping. Returns ValidationResult(valid, reason).
    """
    if not url or not url.strip():
        return ValidationResult(False, "Empty URL")

    url = url.strip()

    # Must be http(s)
    if not url.startswith(("http://", "https://")):
        return ValidationResult(False, "Non-HTTP URL — only http:// and https:// are supported")

    try:
        parsed = urlparse(url)
    except Exception:
        return ValidationResult(False, "Could not parse URL")

    if not parsed.hostname:
        return ValidationResult(False, "No hostname in URL")

    hostname = parsed.hostname.lower()
    full_url = url.lower()

    # Private/localhost
    if _PRIVATE_IP.match(hostname):
        return ValidationResult(False, "Private/localhost URLs are not allowed")

    # File downloads
    if _FILE_EXTENSIONS.search(parsed.path):
        return ValidationResult(False, "File download URL — not an article")

    # Video sites
    if _VIDEO.search(hostname):
        return ValidationResult(False, "Video site — cannot extract article content")

    # Social media (except Twitter/X)
    if _SOCIAL.search(hostname):
        return ValidationResult(False, "Social media — not an article source")

    # Shopping
    if _SHOPPING.search(hostname):
        return ValidationResult(False, "Shopping site — not an article source")

    # Adult content
    if _ADULT.search(hostname):
        return ValidationResult(False, "Adult content site — not allowed")

    # Search engines
    if _SEARCH.search(full_url):
        return ValidationResult(False, "Search engine results page — not an article")

    # Messaging
    if _MESSAGING.search(hostname):
        return ValidationResult(False, "Messaging app link — not an article")

    return ValidationResult(True)


# ── Paywall detection ─────────────────────────────────────────────────────────

_PAYWALL_DOMAINS = {
    "wsj.com", "ft.com", "economist.com", "nytimes.com",
    "washingtonpost.com", "bloomberg.com", "theathletic.com",
    "thetimes.co.uk", "telegraph.co.uk", "hbr.org",
    "barrons.com", "businessinsider.com", "seekingalpha.com",
    "foreignaffairs.com", "newyorker.com", "wired.com",
    "theatlantic.com", "thedailybeast.com",
}


def is_likely_paywalled(hostname: str) -> bool:
    """Check if a domain is known to have a paywall."""
    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname in _PAYWALL_DOMAINS


# ── Twitter detection ─────────────────────────────────────────────────────────

_TWITTER = re.compile(r"(twitter\.com|x\.com)", re.IGNORECASE)


def is_twitter_url(url: str) -> bool:
    """Check if URL is a Twitter/X link."""
    try:
        hostname = urlparse(url).hostname or ""
        return bool(_TWITTER.search(hostname))
    except Exception:
        return False
