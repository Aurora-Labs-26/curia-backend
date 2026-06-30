"""
tests/test_validator.py
Comprehensive tests for core/scraper/validator.py — URL validation, paywall detection, Twitter detection.
All pure functions, no mocks needed.
"""

import pytest

from core.scraper.validator import validate_url, is_likely_paywalled, is_twitter_url


# ---------------------------------------------------------------------------
# validate_url
# ---------------------------------------------------------------------------


class TestValidateUrl:
    # --- Basic validity ---

    def test_valid_article_url(self):
        r = validate_url("https://example.com/article/great-post")
        assert r.valid is True

    def test_valid_http_url(self):
        r = validate_url("http://blog.example.org/post")
        assert r.valid is True

    # --- Empty / whitespace ---

    def test_empty_string(self):
        r = validate_url("")
        assert r.valid is False
        assert "Empty" in r.reason

    def test_whitespace_only(self):
        r = validate_url("   ")
        assert r.valid is False

    def test_none_value(self):
        r = validate_url(None)
        assert r.valid is False

    # --- Scheme ---

    def test_ftp_rejected(self):
        r = validate_url("ftp://example.com/file.txt")
        assert r.valid is False
        assert "Non-HTTP" in r.reason

    def test_javascript_rejected(self):
        r = validate_url("javascript:alert(1)")
        assert r.valid is False

    def test_no_scheme(self):
        r = validate_url("example.com/article")
        assert r.valid is False

    # --- No hostname ---

    def test_no_hostname(self):
        r = validate_url("http://")
        assert r.valid is False

    # --- Private IP ---

    def test_localhost_rejected(self):
        r = validate_url("http://localhost/admin")
        assert r.valid is False
        assert "Private" in r.reason

    def test_127_ip_rejected(self):
        r = validate_url("http://127.0.0.1/api")
        assert r.valid is False

    def test_10_ip_rejected(self):
        r = validate_url("http://10.0.0.1/internal")
        assert r.valid is False

    def test_192_168_rejected(self):
        r = validate_url("http://192.168.1.1/admin")
        assert r.valid is False

    def test_172_16_rejected(self):
        r = validate_url("http://172.16.0.1/page")
        assert r.valid is False

    # --- File extensions ---

    def test_pdf_rejected(self):
        r = validate_url("https://example.com/whitepaper.pdf")
        assert r.valid is False
        assert "File download" in r.reason

    def test_zip_rejected(self):
        r = validate_url("https://example.com/archive.zip")
        assert r.valid is False

    def test_jpg_rejected(self):
        r = validate_url("https://example.com/photo.jpg")
        assert r.valid is False

    def test_mp4_rejected(self):
        r = validate_url("https://example.com/video.mp4")
        assert r.valid is False

    def test_pdf_with_query_params_rejected(self):
        r = validate_url("https://example.com/paper.pdf?dl=1")
        assert r.valid is False

    def test_extension_case_insensitive(self):
        r = validate_url("https://example.com/file.PDF")
        assert r.valid is False

    # --- Video sites ---

    def test_youtube_allowed(self):
        # YouTube is allowed — transcript is extracted via youtube-transcript-api
        r = validate_url("https://youtube.com/watch?v=abc123")
        assert r.valid is True

    def test_youtu_be_allowed(self):
        r = validate_url("https://youtu.be/abc123")
        assert r.valid is True

    def test_vimeo_rejected(self):
        r = validate_url("https://vimeo.com/12345")
        assert r.valid is False

    def test_tiktok_rejected(self):
        r = validate_url("https://tiktok.com/@user/video/123")
        assert r.valid is False

    # --- Social media ---

    def test_instagram_rejected(self):
        r = validate_url("https://instagram.com/p/abc123")
        assert r.valid is False
        assert "Social" in r.reason

    def test_facebook_rejected(self):
        r = validate_url("https://facebook.com/post/123")
        assert r.valid is False

    def test_threads_rejected(self):
        r = validate_url("https://threads.net/@user/post/abc")
        assert r.valid is False

    def test_pinterest_rejected(self):
        r = validate_url("https://pinterest.com/pin/123")
        assert r.valid is False

    # --- Twitter/X explicitly allowed ---

    def test_twitter_allowed(self):
        r = validate_url("https://twitter.com/user/status/123")
        assert r.valid is True

    def test_x_com_allowed(self):
        r = validate_url("https://x.com/user/status/123")
        assert r.valid is True

    # --- Shopping ---

    def test_amazon_rejected(self):
        r = validate_url("https://amazon.com/dp/B08XYZ123")
        assert r.valid is False
        assert "Shopping" in r.reason

    def test_amazon_in_rejected(self):
        r = validate_url("https://amazon.in/product/abc")
        assert r.valid is False

    def test_ebay_rejected(self):
        r = validate_url("https://ebay.com/itm/123")
        assert r.valid is False

    def test_etsy_rejected(self):
        r = validate_url("https://etsy.com/listing/123")
        assert r.valid is False

    # --- Adult content ---

    def test_adult_site_rejected(self):
        r = validate_url("https://pornhub.com/view")
        assert r.valid is False
        assert "Adult" in r.reason

    def test_onlyfans_rejected(self):
        r = validate_url("https://onlyfans.com/user")
        assert r.valid is False

    # --- Search engines ---

    def test_google_search_rejected(self):
        r = validate_url("https://google.com/search?q=hello")
        assert r.valid is False
        assert "Search engine" in r.reason

    def test_bing_search_rejected(self):
        r = validate_url("https://bing.com/search?q=test")
        assert r.valid is False

    def test_duckduckgo_rejected(self):
        r = validate_url("https://duckduckgo.com/?q=test")
        assert r.valid is False

    # --- Messaging ---

    def test_whatsapp_rejected(self):
        r = validate_url("https://wa.me/123456")
        assert r.valid is False
        assert "Messaging" in r.reason

    def test_telegram_rejected(self):
        r = validate_url("https://t.me/channel")
        assert r.valid is False

    def test_discord_rejected(self):
        r = validate_url("https://discord.gg/invite")
        assert r.valid is False

    def test_slack_rejected(self):
        r = validate_url("https://slack.com/workspace")
        assert r.valid is False

    # --- Whitespace handling ---

    def test_strips_whitespace(self):
        r = validate_url("  https://example.com/article  ")
        assert r.valid is True


# ---------------------------------------------------------------------------
# is_likely_paywalled
# ---------------------------------------------------------------------------


class TestIsLikelyPaywalled:
    def test_wsj_paywalled(self):
        assert is_likely_paywalled("wsj.com") is True

    def test_nytimes_paywalled(self):
        assert is_likely_paywalled("nytimes.com") is True

    def test_www_prefix_stripped(self):
        assert is_likely_paywalled("www.wsj.com") is True

    def test_case_insensitive(self):
        assert is_likely_paywalled("WSJ.COM") is True

    def test_non_paywalled(self):
        assert is_likely_paywalled("example.com") is False

    def test_bloomberg_paywalled(self):
        assert is_likely_paywalled("bloomberg.com") is True

    def test_ft_paywalled(self):
        assert is_likely_paywalled("ft.com") is True

    def test_theatlantic_paywalled(self):
        assert is_likely_paywalled("theatlantic.com") is True


# ---------------------------------------------------------------------------
# is_twitter_url
# ---------------------------------------------------------------------------


class TestIsTwitterUrl:
    def test_twitter_com(self):
        assert is_twitter_url("https://twitter.com/user/status/123") is True

    def test_x_com(self):
        assert is_twitter_url("https://x.com/user/status/123") is True

    def test_non_twitter(self):
        assert is_twitter_url("https://example.com") is False

    def test_empty_url(self):
        assert is_twitter_url("") is False

    def test_malformed_url(self):
        assert is_twitter_url("not-a-url") is False

    def test_case_insensitive(self):
        assert is_twitter_url("https://TWITTER.COM/user") is True
