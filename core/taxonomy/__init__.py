"""
core/taxonomy/
Topic bucketing for sources — deterministic pins + LLM judge (see "topics v1.md").

Public surface:
    from core.taxonomy import classify_source, resolve_pins, TAXONOMY, is_valid
"""
from .buckets import TAXONOMY, is_valid
from .classify import classify_source, resolve_pins

__all__ = ["TAXONOMY", "is_valid", "classify_source", "resolve_pins"]
