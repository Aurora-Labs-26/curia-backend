"""
tests/test_prettify.py
Rule-based prettifier (core/scraper/prettify.py) — encoding fixes, boilerplate
stripping, paragraph dedup, whitespace normalization, safety valve. Pure unit
tests, no mocks needed.
"""

from core.scraper.prettify import prettify

# A paragraph long enough (>100 chars) to participate in dedup.
PARA = (
    "The economics of attention have shifted dramatically over the past decade, "
    "and nobody in the industry seems willing to admit what that means for publishers."
)


class TestEncoding:
    def test_html_entities(self):
        assert prettify("AT&amp;T bought Time&nbsp;Warner &quot;quietly&quot;.") == \
            'AT&T bought Time Warner "quietly".'

    def test_double_encoded_entities(self):
        assert prettify("Ben &amp;amp; Jerry") == "Ben & Jerry"

    def test_mojibake_quotes_and_dashes(self):
        assert prettify("Itâ€™s doneâ€” finally") == "It's done— finally"

    def test_zero_width_chars_removed(self):
        assert prettify("hel​lo wor﻿ld") == "hello world"

    def test_nbsp_becomes_space(self):
        assert prettify("one two") == "one two"


class TestBoilerplate:
    def test_short_chrome_lines_dropped(self):
        text = f"{PARA}\n\nSubscribe to our newsletter\n\nShare this article\n\n{PARA} Second thought."
        out = prettify(text)
        assert "Subscribe" not in out
        assert "Share this" not in out
        assert "economics of attention" in out

    def test_long_prose_mentioning_newsletter_survives(self):
        line = (
            "The newsletter economy has fundamentally changed how independent writers "
            "reach their readers, and Substack is the clearest example of that shift."
        )
        assert line in prettify(f"{PARA}\n\n{line}")

    def test_cookie_banner_dropped(self):
        out = prettify(f"We use cookies to improve your experience\n\n{PARA}")
        assert "cookies" not in out
        assert "economics" in out


class TestDedup:
    def test_duplicate_long_paragraph_removed(self):
        out = prettify(f"{PARA}\n\nSomething different entirely happened next in the market.\n\n{PARA}")
        assert out.count("economics of attention") == 1

    def test_short_repeats_kept(self):
        # short repeated section markers are legitimate structure
        out = prettify(f"***\n\n{PARA}\n\n***\n\nAnother thought about something new here.")
        assert out.count("***") == 2


class TestWhitespace:
    def test_blank_line_runs_collapse(self):
        out = prettify(f"{PARA}\n\n\n\n\nNext paragraph about something else entirely here.")
        assert "\n\n\n" not in out

    def test_space_runs_collapse(self):
        assert prettify("one     two\tthree") == "one two three"


class TestSafety:
    def test_empty_input(self):
        assert prettify("") == ""
        assert prettify(None) == ""

    def test_over_stripping_returns_original(self):
        # a document that is ~all boilerplate would be nearly emptied → valve
        text = "\n\n".join(["Subscribe to our newsletter"] * 20)
        assert prettify(text) == text.strip()

    def test_idempotent(self):
        text = f"{PARA}\n\nItâ€™s a &quot;test&quot; of the â€œsystemâ€\x9d.\n\nSubscribe now"
        once = prettify(text)
        assert prettify(once) == once

    def test_preserves_order_and_words(self):
        a = "First the researchers measured the baseline across all twelve cohorts in the study."
        b = "Then they compared the intervention group against it over eighteen months of data."
        out = prettify(f"{a}\n\n{b}")
        assert out.index("First") < out.index("Then")
        assert a in out and b in out
