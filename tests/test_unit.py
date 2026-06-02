"""
tests/test_unit.py
Unit tests for pure functions, schemas, and data structures.
No database or network access required.
"""

import json
import pytest
from uuid import UUID


# ═══════════════════════════════════════════════════════════════════════════════
# 1. URL Normalisation (core/ingest.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormaliseUrl:
    """Tests for core.ingest.normalise_url — pure string transform."""

    def _norm(self, url: str) -> str:
        from core.ingest import normalise_url
        return normalise_url(url)

    def test_strips_utm_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&keep=1"
        result = self._norm(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "keep=1" in result

    def test_strips_fbclid(self):
        url = "https://example.com/page?fbclid=abc123"
        result = self._norm(url)
        assert "fbclid" not in result

    def test_strips_gclid(self):
        url = "https://example.com/page?gclid=xyz"
        result = self._norm(url)
        assert "gclid" not in result

    def test_lowercases_scheme_and_host(self):
        url = "HTTPS://Example.COM/Path"
        result = self._norm(url)
        assert result.startswith("https://example.com/")

    def test_preserves_path_case(self):
        url = "https://example.com/CamelCasePath"
        result = self._norm(url)
        assert "/CamelCasePath" in result

    def test_strips_trailing_slash(self):
        url = "https://example.com/article/"
        result = self._norm(url)
        assert result.endswith("/article")

    def test_root_path_stays_slash(self):
        url = "https://example.com/"
        result = self._norm(url)
        assert result.endswith("/")

    def test_strips_fragment(self):
        url = "https://example.com/page#section"
        result = self._norm(url)
        assert "#" not in result

    def test_rewrites_open_substack(self):
        url = "https://open.substack.com/pub/example/p/article-name"
        result = self._norm(url)
        assert "open.substack.com" not in result
        assert "substack.com" in result

    def test_strips_substack_specific_params(self):
        url = "https://example.substack.com/p/article?r=abc&publication_id=123&isFreemail=true"
        result = self._norm(url)
        assert "r=" not in result
        assert "publication_id" not in result
        assert "isFreemail" not in result

    def test_preserves_meaningful_query_params(self):
        url = "https://example.com/search?q=test&page=2"
        result = self._norm(url)
        assert "q=test" in result
        assert "page=2" in result

    def test_strips_whitespace(self):
        url = "  https://example.com/article  "
        result = self._norm(url)
        assert result == self._norm("https://example.com/article")

    def test_idempotent(self):
        url = "https://example.com/article?utm_source=x"
        first = self._norm(url)
        second = self._norm(first)
        assert first == second


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Named Parameter Converter (core/db/connection.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestConvertNamedParams:
    """Tests for the $name → $1 positional param translator."""

    def _convert(self, sql, params):
        from core.db.connection import _convert_named_params
        return _convert_named_params(sql, params)

    def test_basic_conversion(self):
        sql = "SELECT * FROM t WHERE id = $id AND name = $name"
        params = {"id": 1, "name": "test"}
        converted_sql, positional = self._convert(sql, params)
        assert "$1" in converted_sql
        assert "$2" in converted_sql
        assert "$id" not in converted_sql
        assert len(positional) == 2

    def test_repeated_param_reuses_index(self):
        sql = "SELECT * FROM t WHERE a = $x OR b = $x"
        params = {"x": 42}
        converted_sql, positional = self._convert(sql, params)
        # Both occurrences should map to the same positional index
        assert converted_sql.count("$1") == 2
        assert len(positional) == 1
        assert positional[0] == 42

    def test_none_params_returns_empty(self):
        sql = "SELECT 1"
        converted_sql, positional = self._convert(sql, None)
        assert converted_sql == sql
        assert positional == []

    def test_empty_dict_returns_empty(self):
        sql = "SELECT 1"
        converted_sql, positional = self._convert(sql, {})
        assert converted_sql == sql
        assert positional == []

    def test_missing_param_raises_key_error(self):
        sql = "SELECT * FROM t WHERE id = $id"
        params = {"other": 1}
        with pytest.raises(KeyError, match="id"):
            self._convert(sql, params)

    def test_multiple_distinct_params(self):
        sql = "INSERT INTO t (a, b, c) VALUES ($a, $b, $c)"
        params = {"a": 1, "b": "two", "c": 3.0}
        converted_sql, positional = self._convert(sql, params)
        assert len(positional) == 3
        assert 1 in positional
        assert "two" in positional
        assert 3.0 in positional

    def test_underscore_in_param_name(self):
        sql = "SELECT * FROM t WHERE user_id = $user_id"
        params = {"user_id": "abc"}
        converted_sql, positional = self._convert(sql, params)
        assert "$user_id" not in converted_sql
        assert positional == ["abc"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Record Prefix Helpers (core/db/connection.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordPrefixHelpers:

    def test_strip_record_prefix_with_colon(self):
        from core.db.connection import _strip_record_prefix
        assert _strip_record_prefix("source:abc-123") == "abc-123"

    def test_strip_record_prefix_bare_string(self):
        from core.db.connection import _strip_record_prefix
        assert _strip_record_prefix("abc-123") == "abc-123"

    def test_strip_record_prefix_uuid(self):
        from core.db.connection import _strip_record_prefix
        uid = UUID("12345678-1234-1234-1234-123456789012")
        assert _strip_record_prefix(uid) == str(uid)

    def test_split_record_with_colon(self):
        from core.db.connection import _split_record
        table, rid = _split_record("source:abc-123")
        assert table == "source"
        assert rid == "abc-123"

    def test_split_record_bare_id(self):
        from core.db.connection import _split_record
        table, rid = _split_record("abc-123")
        assert table == ""
        assert rid == "abc-123"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Safe URL Masking (core/db/connection.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeUrl:

    def test_masks_password(self):
        from core.db.connection import _safe_url
        result = _safe_url("postgresql://user:secret@localhost:5432/db")
        assert "secret" not in result
        assert "***" in result
        assert "user" in result

    def test_no_password_unchanged(self):
        from core.db.connection import _safe_url
        url = "postgresql://localhost:5432/db"
        assert _safe_url(url) == url


# ═══════════════════════════════════════════════════════════════════════════════
# 5. KB Schema (core/kb/schema.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUserKBSchema:

    def test_empty_kb_valid(self):
        from core.kb.schema import UserKB
        kb = UserKB()
        assert kb.version == 1
        assert kb.identity.name is None
        assert kb.interests.topics == []
        assert kb.preferences.preferred_length_minutes == 11

    def test_full_kb_roundtrip(self):
        from core.kb.schema import UserKB
        data = {
            "identity": {"name": "Alice", "reading_volume_per_week": "10 articles"},
            "interests": {"topics": ["AI", "climate"], "current_obsession": "LLMs"},
            "preferences": {
                "preferred_length_minutes": 15,
                "preferred_formats": ["narrative_drift"],
                "preferred_tone": "analytical",
                "tolerates_ambiguity": "high",
                "novelty_appetite": 0.8,
            },
            "listening_context": {"when": "morning_commute", "while_doing": "walking"},
            "dislikes": {"formats": ["momentum_loop"], "tones": ["punchy"], "themes": ["crypto"]},
            "version": 2,
        }
        kb = UserKB.model_validate(data)
        assert kb.identity.name == "Alice"
        assert kb.interests.current_obsession == "LLMs"
        assert kb.preferences.preferred_tone == "analytical"
        assert kb.preferences.novelty_appetite == 0.8
        assert kb.dislikes.themes == ["crypto"]
        assert kb.version == 2
        # Roundtrip through JSON
        dumped = kb.model_dump()
        kb2 = UserKB.model_validate(dumped)
        assert kb2 == kb

    def test_length_minutes_validation(self):
        from core.kb.schema import Preferences
        with pytest.raises(Exception):  # pydantic ValidationError
            Preferences(preferred_length_minutes=2)  # min 3
        with pytest.raises(Exception):
            Preferences(preferred_length_minutes=31)  # max 30

    def test_novelty_appetite_bounds(self):
        from core.kb.schema import Preferences
        with pytest.raises(Exception):
            Preferences(novelty_appetite=-0.1)
        with pytest.raises(Exception):
            Preferences(novelty_appetite=1.1)

    def test_extra_fields_forbidden(self):
        from core.kb.schema import UserKB
        with pytest.raises(Exception):
            UserKB(unknown_field="nope")

    def test_valid_tones(self):
        from core.kb.schema import Preferences
        for tone in ["dry", "warm", "analytical", "conversational", "literary", "punchy", "dispassionate"]:
            p = Preferences(preferred_tone=tone)
            assert p.preferred_tone == tone

    def test_invalid_tone_rejected(self):
        from core.kb.schema import Preferences
        with pytest.raises(Exception):
            Preferences(preferred_tone="excited")

    def test_valid_format_names(self):
        from core.kb.schema import Preferences
        for fmt in ["narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"]:
            p = Preferences(preferred_formats=[fmt])
            assert fmt in p.preferred_formats

    def test_invalid_format_name_rejected(self):
        from core.kb.schema import Preferences
        with pytest.raises(Exception):
            Preferences(preferred_formats=["invalid_format"])


# ═══════════════════════════════════════════════════════════════════════════════
# 6. KB Store Helpers (core/kb/store.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestKBStoreHelpers:

    def test_empty_kb_returns_defaults(self):
        from core.kb.store import empty_kb
        kb = empty_kb()
        assert kb.version == 1
        assert kb.identity.name is None

    def test_coerce_kb_payload_dict(self):
        from core.kb.store import _coerce_kb_payload
        d = {"identity": {"name": "Test"}}
        assert _coerce_kb_payload(d) == d

    def test_coerce_kb_payload_string(self):
        from core.kb.store import _coerce_kb_payload
        d = {"identity": {"name": "Test"}}
        assert _coerce_kb_payload(json.dumps(d)) == d

    def test_coerce_kb_payload_none(self):
        from core.kb.store import _coerce_kb_payload
        assert _coerce_kb_payload(None) == {}

    def test_coerce_kb_payload_invalid_string(self):
        from core.kb.store import _coerce_kb_payload
        assert _coerce_kb_payload("not json {{{") == {}

    def test_coerce_kb_payload_other_type(self):
        from core.kb.store import _coerce_kb_payload
        assert _coerce_kb_payload(12345) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Format Registry (studio/formats.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormatRegistry:

    def test_all_formats_exist(self):
        from studio.formats import FORMATS
        assert set(FORMATS.keys()) == {
            "narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine", "crossfire"
        }

    def test_get_format_valid(self):
        from studio.formats import get_format
        fmt = get_format("narrative_drift")
        assert fmt.name == "narrative_drift"
        assert fmt.pacing == "slow"

    def test_get_format_invalid_raises(self):
        from studio.formats import get_format
        with pytest.raises(ValueError, match="Unknown format"):
            get_format("nonexistent")

    def test_resolve_format_name_backend(self):
        from studio.formats import resolve_format_name
        assert resolve_format_name("clarity_engine") == "clarity_engine"

    def test_resolve_format_name_slug(self):
        from studio.formats import resolve_format_name
        assert resolve_format_name("slow-burn") == "narrative_drift"
        assert resolve_format_name("sharp-take") == "clarity_engine"
        assert resolve_format_name("live-wire") == "momentum_loop"
        assert resolve_format_name("open-verdict") == "exploration_engine"

    def test_resolve_format_name_unknown_raises(self):
        from studio.formats import resolve_format_name
        with pytest.raises(ValueError, match="Unknown format"):
            resolve_format_name("nonexistent-slug")

    def test_target_lines_calculation(self):
        from studio.formats import get_format, LINES_PER_MINUTE
        fmt = get_format("narrative_drift")
        expected = round(fmt.default_length_minutes * LINES_PER_MINUTE)
        assert fmt.target_lines == expected

    def test_target_lines_per_segment_at_least_two(self):
        from studio.formats import FORMATS
        for fmt in FORMATS.values():
            assert fmt.target_lines_per_segment >= 2

    def test_format_config_to_dict(self):
        from studio.formats import get_format, format_config_to_dict
        fmt = get_format("clarity_engine")
        d = format_config_to_dict(fmt)
        assert d["name"] == "clarity_engine"
        assert "pacing" in d
        assert "rules" in d
        assert "must_do" in d["rules"]
        assert "must_avoid" in d["rules"]
        assert "target_lines" in d
        assert "target_lines_per_segment" in d

    def test_each_format_has_rules(self):
        from studio.formats import FORMATS
        for name, fmt in FORMATS.items():
            assert len(fmt.rules.must_do) > 0, f"{name} missing must_do rules"
            assert len(fmt.rules.must_avoid) > 0, f"{name} missing must_avoid rules"

    def test_each_format_has_structure_pattern(self):
        from studio.formats import FORMATS
        for name, fmt in FORMATS.items():
            assert len(fmt.structure_pattern) > 0, f"{name} missing structure_pattern"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Show Profiles (studio/shows/profiles.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestShowProfiles:

    def test_all_profiles_exist(self):
        from studio.shows.profiles import SHOW_PROFILES
        assert set(SHOW_PROFILES.keys()) == {
            "narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine", "crossfire"
        }

    def test_all_three_speakers_exist(self):
        from studio.shows.profiles import SPEAKER_PROFILES
        assert set(SPEAKER_PROFILES.keys()) == {"kenji", "arjun", "emeka"}

    def test_speaker_has_backstory_and_patterns(self):
        from studio.shows.profiles import SPEAKER_PROFILES
        for name, profile in SPEAKER_PROFILES.items():
            assert len(profile.speakers) > 0, f"{name} has no speakers"
            for s in profile.speakers:
                assert s.backstory, f"Speaker {s.name} missing backstory"
                assert s.speech_patterns, f"Speaker {s.name} missing speech_patterns"

    def test_profile_format_names_valid(self):
        from studio.shows.profiles import SHOW_PROFILES
        from studio.formats import FORMATS
        for name, profile in SHOW_PROFILES.items():
            assert profile.format_name in FORMATS, f"Profile {name} references unknown format {profile.format_name}"

    def test_profile_language(self):
        from studio.shows.profiles import SHOW_PROFILES
        for profile in SHOW_PROFILES.values():
            assert profile.language == "en-US"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Briefing Builder (studio/briefing_builder.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBriefingBuilder:

    def _make_sources(self):
        return [
            {"id": "src-1", "title": "Article One"},
            {"id": "src-2", "title": "Article Two"},
        ]

    def _make_insights(self):
        return {
            "src-1": {
                "key_insights": "Insight A",
                "human_stakes": "Stake A",
                "core_tensions": "Tension A",
                "counterpoints": "Counter A",
                "examples": "Example A",
            },
            "src-2": {
                "key_insights": "Insight B",
                "human_stakes": None,
                "core_tensions": "Tension B",
                "counterpoints": "",
                "examples": "Example B",
            },
        }

    def test_build_source_primitives(self):
        from studio.briefing_builder import build_source_primitives
        sources = self._make_sources()
        insights = self._make_insights()
        result = build_source_primitives(sources, insights)
        assert len(result) == 2
        assert result[0]["title"] == "Article One"
        assert result[0]["key_insights"] == "Insight A"
        assert result[1]["human_stakes"] is None  # None values pass through

    def test_build_source_primitives_strips_prefix(self):
        from studio.briefing_builder import build_source_primitives
        sources = [{"id": "source:abc", "title": "Test"}]
        insights = {"abc": {"key_insights": "Found it"}}
        result = build_source_primitives(sources, insights)
        assert result[0]["key_insights"] == "Found it"

    def test_build_source_primitives_missing_insights(self):
        from studio.briefing_builder import build_source_primitives
        sources = [{"id": "missing", "title": "No Insights"}]
        result = build_source_primitives(sources, {})
        assert result[0]["key_insights"] is None
        assert result[0]["title"] == "No Insights"

    def test_build_briefing_packet_basic(self):
        from studio.briefing_builder import build_briefing_packet
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
        )
        assert packet["format"] == "clarity_engine"
        assert "format_config" in packet
        assert "episode_constraints" in packet
        assert "source_primitives" in packet
        assert len(packet["source_primitives"]) == 2
        assert packet["episode_constraints"]["target_length_minutes"] == 10  # clarity_engine default

    def test_build_briefing_packet_length_override(self):
        from studio.briefing_builder import build_briefing_packet
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
            length_override=20,
        )
        assert packet["episode_constraints"]["target_length_minutes"] == 20

    def test_build_briefing_packet_kb_length_override(self):
        from studio.briefing_builder import build_briefing_packet
        from core.kb.schema import UserKB
        kb = UserKB(preferences={"preferred_length_minutes": 25})
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
            user_kb=kb,
        )
        assert packet["episode_constraints"]["target_length_minutes"] == 25

    def test_build_briefing_packet_explicit_length_beats_kb(self):
        from studio.briefing_builder import build_briefing_packet
        from core.kb.schema import UserKB
        kb = UserKB(preferences={"preferred_length_minutes": 25})
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
            length_override=8,
            user_kb=kb,
        )
        assert packet["episode_constraints"]["target_length_minutes"] == 8

    def test_build_briefing_packet_editorial_direction_default(self):
        from studio.briefing_builder import build_briefing_packet
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
        )
        assert "most interesting thread" in packet["editorial_direction"]

    def test_build_briefing_packet_editorial_direction_custom(self):
        from studio.briefing_builder import build_briefing_packet
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
            editorial_direction="Focus on AI safety",
        )
        assert packet["editorial_direction"] == "Focus on AI safety"

    def test_build_briefing_packet_listener_context_included(self):
        from studio.briefing_builder import build_briefing_packet
        from core.kb.schema import UserKB
        kb = UserKB(
            preferences={"preferred_tone": "dry", "novelty_appetite": 0.9},
            interests={"topics": ["AI", "philosophy"]},
        )
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
            user_kb=kb,
        )
        assert "listener_context" in packet
        assert packet["listener_context"]["preferred_tone"] == "dry"
        assert packet["listener_context"]["novelty_appetite"] == 0.9
        assert "AI" in packet["listener_context"]["active_interests"]

    def test_build_briefing_packet_default_kb_includes_length_context(self):
        from studio.briefing_builder import build_briefing_packet
        from core.kb.schema import UserKB
        kb = UserKB()  # defaults include preferred_length_minutes=11
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
            user_kb=kb,
        )
        # Default KB has preferred_length_minutes=11, so listener_context is present
        assert "listener_context" in packet
        assert packet["listener_context"]["preferred_length_minutes"] == 11

    def test_briefing_packet_to_str_is_json(self):
        from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=self._make_sources(),
            insights=self._make_insights(),
        )
        s = briefing_packet_to_str(packet)
        parsed = json.loads(s)
        assert parsed["format"] == "clarity_engine"

    def test_segment_count_override(self):
        from studio.briefing_builder import build_briefing_packet
        packet = build_briefing_packet(
            format_name="narrative_drift",
            sources=self._make_sources(),
            insights=self._make_insights(),
            segment_count_override=4,
        )
        assert packet["episode_constraints"]["segment_count"] == 4


# ═══════════════════════════════════════════════════════════════════════════════
# 10. KB Listener Context Extraction (studio/briefing_builder.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestKBListenerContext:

    def test_none_kb_returns_none(self):
        from studio.briefing_builder import _kb_listener_context
        assert _kb_listener_context(None) is None

    def test_default_kb_returns_length_only(self):
        from studio.briefing_builder import _kb_listener_context
        from core.kb.schema import UserKB
        # Default KB has preferred_length_minutes=11, which is non-default and included
        ctx = _kb_listener_context(UserKB())
        assert ctx == {"preferred_length_minutes": 11}

    def test_preferred_tone_included(self):
        from studio.briefing_builder import _kb_listener_context
        from core.kb.schema import UserKB
        kb = UserKB(preferences={"preferred_tone": "warm"})
        ctx = _kb_listener_context(kb)
        assert ctx is not None
        assert ctx["preferred_tone"] == "warm"

    def test_non_default_ambiguity_included(self):
        from studio.briefing_builder import _kb_listener_context
        from core.kb.schema import UserKB
        kb = UserKB(preferences={"tolerates_ambiguity": "high"})
        ctx = _kb_listener_context(kb)
        assert ctx["ambiguity_tolerance"] == "high"

    def test_default_ambiguity_excluded(self):
        from studio.briefing_builder import _kb_listener_context
        from core.kb.schema import UserKB
        # "medium" is the default — should NOT appear
        kb = UserKB(preferences={"tolerates_ambiguity": "medium", "preferred_tone": "dry"})
        ctx = _kb_listener_context(kb)
        assert "ambiguity_tolerance" not in ctx

    def test_dislikes_included(self):
        from studio.briefing_builder import _kb_listener_context
        from core.kb.schema import UserKB
        kb = UserKB(dislikes={"themes": ["sports"], "tones": ["punchy"]})
        ctx = _kb_listener_context(kb)
        assert ctx["avoid_themes"] == ["sports"]
        assert ctx["avoid_tones"] == ["punchy"]


# ═══════════════════════════════════════════════════════════════════════════════
# 11. TTS Text Chunking (core/llm_config/adapters/tts.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTTSTextChunking:

    def _chunk(self, text, max_chars=200):
        from core.llm_config.adapters.tts import _chunk_text
        return _chunk_text(text, max_chars)

    def test_short_text_single_chunk(self):
        result = self._chunk("Hello world.", 200)
        assert result == ["Hello world."]

    def test_text_at_limit_single_chunk(self):
        text = "x" * 200
        result = self._chunk(text, 200)
        assert len(result) == 1

    def test_all_chunks_under_limit(self):
        text = "This is a test sentence. " * 20  # ~500 chars
        result = self._chunk(text, 100)
        for chunk in result:
            assert len(chunk) <= 100, f"Chunk too long: {len(chunk)} chars"

    def test_sentence_boundary_splitting(self):
        text = "First sentence. Second sentence. Third sentence."
        result = self._chunk(text, 30)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 30

    def test_long_word_hard_truncated(self):
        text = "a" * 300
        result = self._chunk(text, 200)
        for chunk in result:
            assert len(chunk) <= 200

    def test_empty_text(self):
        result = self._chunk("", 200)
        assert result == [""]

    def test_preserves_all_text(self):
        text = "Hello world. This is a test. Final sentence here."
        result = self._chunk(text, 25)
        reconstructed = " ".join(result)
        # All original words should appear
        for word in text.split():
            assert word.rstrip(".") in reconstructed or word in reconstructed


# ═══════════════════════════════════════════════════════════════════════════════
# 12. WAV Helpers (core/llm_config/adapters/tts.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestWAVHelpers:

    def test_sample_rate_from_format_pcm(self):
        from core.llm_config.adapters.tts import _sample_rate_from_format
        assert _sample_rate_from_format("pcm_22050") == 22050
        assert _sample_rate_from_format("pcm_44100") == 44100
        assert _sample_rate_from_format("pcm_16000") == 16000

    def test_sample_rate_from_format_default(self):
        from core.llm_config.adapters.tts import _sample_rate_from_format
        assert _sample_rate_from_format("mp3") == 22050
        assert _sample_rate_from_format("unknown") == 22050

    def test_write_silent_wav(self, tmp_path):
        from core.llm_config.adapters.tts import _write_silent_wav
        import wave
        out = str(tmp_path / "silence.wav")
        _write_silent_wav(out, duration_seconds=0.5)
        with wave.open(out, "rb") as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == 22050
            expected_frames = int(22050 * 0.5)
            assert w.getnframes() == expected_frames

    def test_write_wav_from_pcm(self, tmp_path):
        from core.llm_config.adapters.tts import _write_wav_from_pcm
        import wave
        # 100 samples of silence
        pcm = b"\x00\x00" * 100
        out = str(tmp_path / "test.wav")
        _write_wav_from_pcm(pcm, 22050, out)
        with wave.open(out, "rb") as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getnframes() == 100


# ═══════════════════════════════════════════════════════════════════════════════
# 13. PermanentError (core/errors.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPermanentError:

    def test_is_value_error(self):
        from core.errors import PermanentError
        assert issubclass(PermanentError, ValueError)

    def test_has_message(self):
        from core.errors import PermanentError
        err = PermanentError("url not found")
        assert str(err) == "url not found"

    def test_catchable_as_value_error(self):
        from core.errors import PermanentError
        with pytest.raises(ValueError):
            raise PermanentError("test")


# ═══════════════════════════════════════════════════════════════════════════════
# 14. API Schemas (api/schemas.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAPISchemas:

    def test_create_source_request_valid(self):
        from api.schemas import CreateSourceRequest
        req = CreateSourceRequest(url="https://example.com/article")
        assert str(req.url) == "https://example.com/article"
        assert req.auto_generate is True

    def test_create_source_request_invalid_url(self):
        from api.schemas import CreateSourceRequest
        with pytest.raises(Exception):
            CreateSourceRequest(url="not-a-url")

    def test_create_source_request_auto_generate_false(self):
        from api.schemas import CreateSourceRequest
        req = CreateSourceRequest(url="https://example.com", auto_generate=False)
        assert req.auto_generate is False

    def test_create_episode_request_defaults(self):
        from api.schemas import CreateEpisodeRequest
        req = CreateEpisodeRequest(show_name="narrative_drift")
        assert req.show_name == "narrative_drift"
        assert req.show_idea_id is None
        assert req.editorial_direction == ""
        assert req.length_minutes is None
        assert req.speaker is None

    def test_create_episode_request_with_overrides(self):
        from api.schemas import CreateEpisodeRequest
        req = CreateEpisodeRequest(
            show_name="clarity_engine",
            length_minutes=15,
            speaker="kenji",
        )
        assert req.length_minutes == 15
        assert req.speaker == "kenji"

    def test_create_episode_request_length_too_short(self):
        from api.schemas import CreateEpisodeRequest
        with pytest.raises(Exception):
            CreateEpisodeRequest(show_name="test", length_minutes=2)

    def test_create_episode_request_length_too_long(self):
        from api.schemas import CreateEpisodeRequest
        with pytest.raises(Exception):
            CreateEpisodeRequest(show_name="test", length_minutes=31)

    def test_create_episode_request_invalid_speaker(self):
        from api.schemas import CreateEpisodeRequest
        with pytest.raises(Exception):
            CreateEpisodeRequest(show_name="test", speaker="invalid_speaker")

    def test_create_episode_request_valid_speakers(self):
        from api.schemas import CreateEpisodeRequest
        for speaker in ["kenji", "arjun", "emeka"]:
            req = CreateEpisodeRequest(show_name="test", speaker=speaker)
            assert req.speaker == speaker

    def test_guideline_update_min_length(self):
        from api.schemas import GuidelineUpdate
        with pytest.raises(Exception):
            GuidelineUpdate(body="short")  # min 10 chars
        g = GuidelineUpdate(body="This is a valid guideline body")
        assert len(g.body) >= 10

    def test_me_response(self):
        from api.schemas import MeResponse
        resp = MeResponse(id="user-1", email="a@b.com", name="Test", role="user")
        assert resp.id == "user-1"
        assert resp.role == "user"

    def test_episode_summary_defaults(self):
        from api.schemas import EpisodeSummary
        from datetime import datetime
        ep = EpisodeSummary(
            id="12345678-1234-1234-1234-123456789012",
            status="ready",
            created_at=datetime.now(),
        )
        assert ep.source_ids == []
        assert ep.source_objects == []
        assert ep.listened is False
        assert ep.play_progress is None


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Auth CurrentUser (api/auth.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCurrentUser:

    def test_is_qa_true(self):
        from api.auth import CurrentUser
        user = CurrentUser(id="u1", role="qa")
        assert user.is_qa is True

    def test_is_qa_false(self):
        from api.auth import CurrentUser
        user = CurrentUser(id="u1", role="user")
        assert user.is_qa is False


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Job Priority Map (core/queue.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestJobPriority:

    def test_ingest_highest_priority(self):
        from core.queue import _JOB_PRIORITY
        assert _JOB_PRIORITY["ingest"] < _JOB_PRIORITY["generate_episode"]

    def test_generate_ideas_before_episode(self):
        from core.queue import _JOB_PRIORITY
        assert _JOB_PRIORITY["generate_ideas"] < _JOB_PRIORITY["generate_episode"]

    def test_generate_from_source_before_episode(self):
        from core.queue import _JOB_PRIORITY
        assert _JOB_PRIORITY["generate_from_source"] < _JOB_PRIORITY["generate_episode"]

    def test_unknown_type_gets_default_10(self):
        from core.queue import _JOB_PRIORITY
        assert _JOB_PRIORITY.get("unknown_type", 10) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 17. Job Dataclass (core/queue.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestJobDataclass:

    def test_job_fields(self):
        from core.queue import Job
        from uuid import uuid4
        job_id = uuid4()
        job = Job(
            id=job_id,
            type="ingest",
            payload={"source_id": "abc"},
            user_id="user-1",
            attempts=1,
            max_attempts=3,
            correlation_id=None,
        )
        assert job.id == job_id
        assert job.type == "ingest"
        assert job.payload["source_id"] == "abc"
        assert job.user_id == "user-1"
        assert job.attempts == 1
        assert job.max_attempts == 3
        assert job.correlation_id is None


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Storage Helpers (core/storage/blob.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStorageHelpers:

    def test_get_storage_backend_default_local(self, monkeypatch):
        from core.storage.blob import get_storage_backend
        monkeypatch.delenv("CURIA_STORAGE_BACKEND", raising=False)
        assert get_storage_backend() == "local"

    def test_get_storage_backend_s3(self, monkeypatch):
        from core.storage.blob import get_storage_backend
        monkeypatch.setenv("CURIA_STORAGE_BACKEND", "s3")
        assert get_storage_backend() == "s3"

    def test_get_blob_url_local(self, monkeypatch):
        from core.storage.blob import get_blob_url
        monkeypatch.setenv("CURIA_STORAGE_BACKEND", "local")
        url = get_blob_url("audio/test.mp3")
        assert url.startswith("file://")
        assert "audio/test.mp3" in url

    def test_get_blob_url_s3_with_endpoint(self, monkeypatch):
        from core.storage.blob import get_blob_url
        monkeypatch.setenv("CURIA_STORAGE_BACKEND", "s3")
        monkeypatch.setenv("CURIA_S3_BUCKET", "my-bucket")
        monkeypatch.setenv("CURIA_S3_ENDPOINT", "https://r2.example.com")
        url = get_blob_url("audio/test.mp3")
        assert "r2.example.com" in url
        assert "my-bucket" in url
        assert "audio/test.mp3" in url

    def test_get_blob_url_s3_default(self, monkeypatch):
        from core.storage.blob import get_blob_url
        monkeypatch.setenv("CURIA_STORAGE_BACKEND", "s3")
        monkeypatch.delenv("CURIA_S3_ENDPOINT", raising=False)
        monkeypatch.setenv("CURIA_S3_BUCKET", "my-bucket")
        monkeypatch.setenv("CURIA_S3_REGION", "us-west-2")
        url = get_blob_url("audio/test.mp3")
        assert "my-bucket.s3.us-west-2.amazonaws.com" in url

    def test_generate_presigned_url_local_returns_none(self, monkeypatch):
        from core.storage.blob import generate_presigned_url
        monkeypatch.setenv("CURIA_STORAGE_BACKEND", "local")
        assert generate_presigned_url("any-key") is None

    @pytest.mark.asyncio
    async def test_upload_local(self, tmp_path, monkeypatch):
        from core.storage.blob import _upload_local
        monkeypatch.setenv("CURIA_STORAGE_LOCAL_DIR", str(tmp_path))
        url = await _upload_local(b"test data", "subdir/file.bin")
        assert "file://" in url
        written = (tmp_path / "subdir" / "file.bin").read_bytes()
        assert written == b"test data"


# ═══════════════════════════════════════════════════════════════════════════════
# 19. LINES_PER_MINUTE Constant
# ═══════════════════════════════════════════════════════════════════════════════


class TestConstants:

    def test_lines_per_minute_reasonable(self):
        from studio.formats import LINES_PER_MINUTE
        # Should be between 3 and 6 lines per minute at normal TTS rate
        assert 3.0 <= LINES_PER_MINUTE <= 6.0

    def test_article_char_cap(self):
        from core.ingest import ARTICLE_CHAR_CAP
        assert ARTICLE_CHAR_CAP == 50_000


# ═══════════════════════════════════════════════════════════════════════════════
# 20. Crossfire Format (studio/formats.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossfireFormat:

    def test_crossfire_exists_in_registry(self):
        from studio.formats import FORMATS
        assert "crossfire" in FORMATS

    def test_crossfire_properties(self):
        from studio.formats import get_format
        fmt = get_format("crossfire")
        assert fmt.name == "crossfire"
        assert fmt.pacing == "medium_fast"
        assert fmt.resolution_style == "partial"
        assert fmt.energy_curve == "oscillating"

    def test_crossfire_slug_resolves(self):
        from studio.formats import resolve_format_name
        assert resolve_format_name("crossfire") == "crossfire"

    def test_crossfire_has_two_host_rules(self):
        from studio.formats import get_format
        fmt = get_format("crossfire")
        # Must mention disagreement / domination in rules
        must_do_text = " ".join(fmt.rules.must_do)
        must_avoid_text = " ".join(fmt.rules.must_avoid)
        assert "disagreement" in must_do_text
        assert "dominating" in must_avoid_text

    def test_crossfire_target_lines(self):
        from studio.formats import get_format, LINES_PER_MINUTE
        fmt = get_format("crossfire")
        expected = round(fmt.default_length_minutes * LINES_PER_MINUTE)
        assert fmt.target_lines == expected

    def test_crossfire_config_to_dict(self):
        from studio.formats import get_format, format_config_to_dict
        d = format_config_to_dict(get_format("crossfire"))
        assert d["name"] == "crossfire"
        assert "rules" in d


# ═══════════════════════════════════════════════════════════════════════════════
# 21. Crossfire Profile (studio/shows/profiles.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossfireProfile:

    def test_crossfire_profile_exists(self):
        from studio.shows.profiles import SHOW_PROFILES
        assert "crossfire" in SHOW_PROFILES

    def test_crossfire_has_two_speakers(self):
        from studio.shows.profiles import CROSSFIRE_PROFILE
        assert len(CROSSFIRE_PROFILE.speaker_config.speakers) == 2

    def test_crossfire_speakers_are_kenji_and_arjun(self):
        from studio.shows.profiles import CROSSFIRE_PROFILE
        names = [s.name for s in CROSSFIRE_PROFILE.speaker_config.speakers]
        assert names == ["kenji", "arjun"]

    def test_crossfire_speaker_a_is_explainer(self):
        from studio.shows.profiles import CROSSFIRE_PROFILE
        speaker_a = CROSSFIRE_PROFILE.speaker_config.speakers[0]
        assert "builds the case" in speaker_a.backstory

    def test_crossfire_speaker_b_is_skeptic(self):
        from studio.shows.profiles import CROSSFIRE_PROFILE
        speaker_b = CROSSFIRE_PROFILE.speaker_config.speakers[1]
        assert "skeptic" in speaker_b.backstory

    def test_crossfire_speakers_have_role_specific_patterns(self):
        from studio.shows.profiles import CROSSFIRE_PROFILE
        speaker_a = CROSSFIRE_PROFILE.speaker_config.speakers[0]
        speaker_b = CROSSFIRE_PROFILE.speaker_config.speakers[1]
        # A builds arguments, B asks questions
        assert "evidence" in speaker_a.speech_patterns.lower() or "argument" in speaker_a.speech_patterns.lower()
        assert "question" in speaker_b.speech_patterns.lower()

    def test_crossfire_format_name_valid(self):
        from studio.shows.profiles import CROSSFIRE_PROFILE
        from studio.formats import FORMATS
        assert CROSSFIRE_PROFILE.format_name in FORMATS

    def test_crossfire_has_intro_outro(self):
        from studio.shows.profiles import CROSSFIRE_PROFILE
        assert CROSSFIRE_PROFILE.intro_audio_path is not None
        assert CROSSFIRE_PROFILE.outro_audio_path is not None

    def test_single_host_profiles_still_have_one_speaker(self):
        from studio.shows.profiles import SHOW_PROFILES
        for name in ["narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"]:
            profile = SHOW_PROFILES[name]
            assert len(profile.speaker_config.speakers) == 1, f"{name} should have 1 speaker"


# ═══════════════════════════════════════════════════════════════════════════════
# 22. Two-Host DSPy Signatures (core/prompts/transcript_two_host.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTwoHostSignatures:

    def test_host_a_signature_fields(self):
        from core.prompts.transcript_two_host import GenerateHostA
        fields = GenerateHostA.model_fields
        assert "briefing" in fields
        assert "outline" in fields
        assert "speaker_definition" in fields
        assert "quality_guidelines" in fields
        assert "host_a_json" in fields

    def test_host_b_signature_has_host_a_transcript(self):
        from core.prompts.transcript_two_host import GenerateHostB
        fields = GenerateHostB.model_fields
        assert "host_a_transcript" in fields
        assert "speaker_definition" in fields
        assert "host_b_json" in fields

    def test_merge_signature_has_both_transcripts(self):
        from core.prompts.transcript_two_host import MergeDialogue
        fields = MergeDialogue.model_fields
        assert "host_a_transcript" in fields
        assert "host_b_transcript" in fields
        assert "speaker_a_name" in fields
        assert "speaker_b_name" in fields
        assert "merged_json" in fields

    def test_singletons_exist(self):
        from core.prompts.transcript_two_host import generate_host_a, generate_host_b, merge_dialogue
        assert generate_host_a is not None
        assert generate_host_b is not None
        assert merge_dialogue is not None

    def test_prompt_files_loaded(self):
        from core.prompts.loader import load_prompt
        assert load_prompt("transcript_host_a") is not None
        assert load_prompt("transcript_host_b") is not None
        assert load_prompt("transcript_merge") is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 23. Name Validator (core/prompts/name_validator.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNameValidator:

    def test_extract_first_name_normal(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("Alice Smith") == "Alice"

    def test_extract_first_name_single(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("Bob") == "Bob"

    def test_extract_first_name_none(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name(None) is None

    def test_extract_first_name_empty(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("") is None
        assert _extract_first_name("   ") is None

    def test_extract_first_name_too_short(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("A") is None

    def test_extract_first_name_email(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("user@email.com") is None

    def test_extract_first_name_digits(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("user123") is None

    def test_extract_first_name_whitespace_padded(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("  Jane Doe  ") == "Jane"

    def test_extract_first_name_unicode(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("Priya Sharma") == "Priya"

    def test_extract_first_name_hyphenated(self):
        from core.prompts.name_validator import _extract_first_name
        assert _extract_first_name("Jean-Pierre Dupont") == "Jean-Pierre"


# ═══════════════════════════════════════════════════════════════════════════════
# 24. Listener Hints with Name (studio/generator.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestListenerHintsWithName:

    def _hints(self, kb=None, name=None):
        from studio.generator import _format_listener_hints
        return _format_listener_hints(kb, name)

    def test_name_only(self):
        result = self._hints(name="Alice")
        assert "Alice" in result
        assert "LISTENER CONTEXT" in result

    def test_name_with_kb(self):
        from core.kb.schema import UserKB
        kb = UserKB(preferences={"preferred_tone": "warm"})
        result = self._hints(kb=kb, name="Bob")
        assert "Bob" in result
        assert "warm" in result

    def test_no_name_no_kb(self):
        result = self._hints(kb=None, name=None)
        assert result == ""

    def test_name_none_kb_defaults(self):
        from core.kb.schema import UserKB
        # Default KB with no name — still gets length hint from KB defaults
        result = self._hints(kb=UserKB(), name=None)
        assert "LISTENER CONTEXT" in result  # KB default length is 11

    def test_name_appears_first_in_hints(self):
        from core.kb.schema import UserKB
        kb = UserKB(preferences={"preferred_tone": "dry"})
        result = self._hints(kb=kb, name="Eve")
        # Name should appear before tone
        name_pos = result.index("Eve")
        tone_pos = result.index("dry")
        assert name_pos < tone_pos


# ═══════════════════════════════════════════════════════════════════════════════
# 25. _parse_json_response (studio/generator.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseJsonResponse:

    def _parse(self, raw, label="test"):
        from studio.generator import _parse_json_response
        return _parse_json_response(raw, label)

    def test_plain_json_array(self):
        result = self._parse('[{"speaker": "Host", "text": "Hello"}]')
        assert len(result) == 1
        assert result[0]["speaker"] == "Host"

    def test_plain_json_object(self):
        result = self._parse('{"title": "Test"}')
        assert result["title"] == "Test"

    def test_fenced_json(self):
        raw = '```json\n[{"speaker": "Host", "text": "Hi"}]\n```'
        result = self._parse(raw)
        assert len(result) == 1

    def test_fenced_no_language(self):
        raw = '```\n{"key": "value"}\n```'
        result = self._parse(raw)
        assert result["key"] == "value"

    def test_whitespace_padded(self):
        result = self._parse('  \n  [{"a": 1}]  \n  ')
        assert result == [{"a": 1}]

    def test_invalid_json_raises(self):
        with pytest.raises(Exception):
            self._parse("not json at all")


# ═══════════════════════════════════════════════════════════════════════════════
# 26. Two-Host Pipeline Selection Logic
# ═══════════════════════════════════════════════════════════════════════════════


class TestTwoHostSelection:
    """Test that process_episode would pick the right pipeline based on profile."""

    def test_crossfire_is_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["crossfire"]
        is_two_host = len(profile.speaker_config.speakers) >= 2
        assert is_two_host is True

    def test_single_host_formats_are_not_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        for name in ["narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"]:
            profile = SHOW_PROFILES[name]
            is_two_host = len(profile.speaker_config.speakers) >= 2
            assert is_two_host is False, f"{name} should not be two-host"

    def test_speaker_override_forces_single_host(self):
        """When speaker_override is set, two-host should be skipped."""
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["crossfire"]
        speaker_override = "kenji"
        is_two_host = len(profile.speaker_config.speakers) >= 2 and speaker_override is None
        assert is_two_host is False

    def test_no_speaker_override_allows_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["crossfire"]
        speaker_override = None
        is_two_host = len(profile.speaker_config.speakers) >= 2 and speaker_override is None
        assert is_two_host is True


# ═══════════════════════════════════════════════════════════════════════════════
# 27. TTSAdapter Connection Reuse (core/llm_config/adapters/tts.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTTSAdapterConnectionReuse:

    def test_adapter_starts_with_no_client(self):
        from unittest.mock import MagicMock
        from core.llm_config.adapters.tts import TTSAdapter
        adapter = TTSAdapter(
            provider=MagicMock(type="hume", api_key_env="TEST_KEY", base_url=None),
            model=MagicMock(model_id="octave", kind="tts"),
            settings={},
            voice_id="KORA",
        )
        assert adapter._async_client is None

    @pytest.mark.asyncio
    async def test_get_async_client_creates_once(self):
        from unittest.mock import MagicMock
        from core.llm_config.adapters.tts import TTSAdapter
        adapter = TTSAdapter(
            provider=MagicMock(type="hume", api_key_env="TEST_KEY", base_url=None),
            model=MagicMock(model_id="octave", kind="tts"),
            settings={},
            voice_id="KORA",
        )
        client1 = await adapter._get_async_client()
        client2 = await adapter._get_async_client()
        assert client1 is client2  # same instance reused
        await adapter.close_async_client()

    @pytest.mark.asyncio
    async def test_close_async_client(self):
        from unittest.mock import MagicMock
        from core.llm_config.adapters.tts import TTSAdapter
        adapter = TTSAdapter(
            provider=MagicMock(type="hume", api_key_env="TEST_KEY", base_url=None),
            model=MagicMock(model_id="octave", kind="tts"),
            settings={},
            voice_id="KORA",
        )
        await adapter._get_async_client()
        assert adapter._async_client is not None
        await adapter.close_async_client()
        assert adapter._async_client is None
