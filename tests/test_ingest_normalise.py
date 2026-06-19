"""
tests/test_ingest_normalise.py
Unit tests for the pure normalise_url() function in core/ingest.py.
No DB, no network — pure URL parsing logic.
"""

import pytest

from core.ingest import normalise_url


class TestNormaliseUrl:
    def test_strips_whitespace(self):
        assert normalise_url("  https://example.com/path  ") == "https://example.com/path"

    def test_lowercases_scheme_and_netloc(self):
        result = normalise_url("HTTPS://EXAMPLE.COM/Path")
        assert result.startswith("https://example.com/")

    def test_strips_trailing_slash(self):
        assert normalise_url("https://example.com/article/") == "https://example.com/article"

    def test_root_path_kept_as_slash(self):
        result = normalise_url("https://example.com")
        assert result == "https://example.com/"

    # --- Substack rewrites ---

    def test_open_substack_rewritten(self):
        url = "https://open.substack.com/pub/author/p/some-post"
        result = normalise_url(url)
        assert "open.substack.com" not in result
        assert "substack.com" in result

    def test_substack_strips_extra_params(self):
        url = "https://author.substack.com/p/hello?r=abc&publication_id=123&post_id=456&isFreemail=true"
        result = normalise_url(url)
        assert "r=" not in result
        assert "publication_id=" not in result
        assert "post_id=" not in result
        assert "isFreemail=" not in result

    # --- UTM stripping ---

    def test_strips_utm_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&utm_campaign=launch"
        result = normalise_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "utm_campaign" not in result

    def test_strips_fbclid_gclid(self):
        url = "https://example.com/page?fbclid=abc123&gclid=def456"
        result = normalise_url(url)
        assert "fbclid" not in result
        assert "gclid" not in result

    def test_strips_ref_and_source(self):
        url = "https://example.com/page?ref=email&source=newsletter"
        result = normalise_url(url)
        assert "ref=" not in result
        assert "source=" not in result

    def test_preserves_non_tracking_params(self):
        url = "https://example.com/search?q=hello&page=2&utm_source=twitter"
        result = normalise_url(url)
        assert "q=hello" in result
        assert "page=2" in result
        assert "utm_source" not in result

    # --- Fragment stripping ---

    def test_strips_fragment(self):
        url = "https://example.com/article#section-1"
        result = normalise_url(url)
        assert "#" not in result

    # --- Idempotence ---

    def test_idempotent(self):
        url = "https://example.com/article?utm_source=x&q=keep"
        first = normalise_url(url)
        second = normalise_url(first)
        assert first == second

    # --- Non-substack domain leaves substack params alone ---

    def test_non_substack_keeps_r_param(self):
        url = "https://example.com/page?r=abc"
        result = normalise_url(url)
        assert "r=abc" in result
