"""
tests/test_transformations.py
Tests for core/prompts/transformations.py — DSPy signature definitions and module structure.

Verifies:
  - All 7 transformation signatures exist with correct input/output fields
  - Transformations module has forward() and run_one() methods
  - TRANSFORMATION_NAMES contains all 7 names
  - TIER_1 and TIER_2 partition correctly
  - ARTICLE_CHAR_CAP is set
  - Signature docstrings contain key instructions
"""

import pytest


class TestTransformationConstants:

    def test_transformation_names(self):
        from core.prompts.transformations import TRANSFORMATION_NAMES
        assert len(TRANSFORMATION_NAMES) == 7
        assert "summary" in TRANSFORMATION_NAMES
        assert "metadata" in TRANSFORMATION_NAMES
        assert "key_insights" in TRANSFORMATION_NAMES
        assert "human_stakes" in TRANSFORMATION_NAMES
        assert "core_tensions" in TRANSFORMATION_NAMES
        assert "counterpoints" in TRANSFORMATION_NAMES
        assert "examples" in TRANSFORMATION_NAMES

    def test_tier_partition(self):
        from core.prompts.transformations import TIER_1, TIER_2, TRANSFORMATION_NAMES
        all_tiers = set(TIER_1) | set(TIER_2)
        assert all_tiers == set(TRANSFORMATION_NAMES)
        assert len(TIER_1) == 3
        assert len(TIER_2) == 4

    def test_tier_1_always_present(self):
        from core.prompts.transformations import TIER_1
        assert "summary" in TIER_1
        assert "metadata" in TIER_1
        assert "key_insights" in TIER_1

    def test_article_char_cap(self):
        from core.ingest import ARTICLE_CHAR_CAP
        assert ARTICLE_CHAR_CAP == 50_000


class TestSignatureFields:

    def _get_sig_fields(self, cls):
        import dspy
        input_fields = {k for k, v in cls.model_fields.items()
                        if hasattr(v, 'json_schema_extra') and v.json_schema_extra and v.json_schema_extra.get('__dspy_field_type') == 'input'}
        output_fields = {k for k, v in cls.model_fields.items()
                         if hasattr(v, 'json_schema_extra') and v.json_schema_extra and v.json_schema_extra.get('__dspy_field_type') == 'output'}
        return input_fields, output_fields

    def test_extract_summary_fields(self):
        from core.prompts.transformations import ExtractSummary
        assert hasattr(ExtractSummary, 'model_fields')
        fields = set(ExtractSummary.model_fields.keys())
        assert "article" in fields
        assert "summary" in fields

    def test_extract_metadata_fields(self):
        from core.prompts.transformations import ExtractMetadata
        fields = set(ExtractMetadata.model_fields.keys())
        assert "article" in fields
        assert "metadata_json" in fields

    def test_extract_key_insights_fields(self):
        from core.prompts.transformations import ExtractKeyInsights
        fields = set(ExtractKeyInsights.model_fields.keys())
        assert "article" in fields
        assert "insights" in fields

    def test_extract_human_stakes_fields(self):
        from core.prompts.transformations import ExtractHumanStakes
        fields = set(ExtractHumanStakes.model_fields.keys())
        assert "article" in fields
        assert "stakes" in fields

    def test_extract_core_tensions_fields(self):
        from core.prompts.transformations import ExtractCoreTensions
        fields = set(ExtractCoreTensions.model_fields.keys())
        assert "article" in fields
        assert "tension" in fields

    def test_extract_counterpoints_fields(self):
        from core.prompts.transformations import ExtractCounterpoints
        fields = set(ExtractCounterpoints.model_fields.keys())
        assert "article" in fields
        assert "counterpoint" in fields

    def test_extract_examples_fields(self):
        from core.prompts.transformations import ExtractExamples
        fields = set(ExtractExamples.model_fields.keys())
        assert "article" in fields
        assert "example" in fields


class TestTransformationsModule:

    def test_module_exists(self):
        from core.prompts.transformations import Transformations
        t = Transformations()
        assert hasattr(t, "forward")
        assert hasattr(t, "run_one")

    def test_singleton_exists(self):
        from core.prompts.transformations import transformations
        assert transformations is not None

    def test_run_one_rejects_unknown_name(self):
        from core.prompts.transformations import Transformations
        t = Transformations()
        with pytest.raises((KeyError, AttributeError, ValueError)):
            t.run_one("fake article text", "nonexistent_transformation")


class TestSignatureDocstrings:

    def test_summary_mentions_central_claim(self):
        from core.prompts.transformations import ExtractSummary
        doc = ExtractSummary.__doc__ or ""
        assert "central" in doc.lower() or "argument" in doc.lower()

    def test_metadata_mentions_json(self):
        from core.prompts.transformations import ExtractMetadata
        doc = ExtractMetadata.__doc__ or ""
        assert "json" in doc.lower()

    def test_human_stakes_mentions_null(self):
        from core.prompts.transformations import ExtractHumanStakes
        doc = ExtractHumanStakes.__doc__ or ""
        assert "null" in doc.lower()
