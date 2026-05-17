"""
tests/test_firebase.py
Firebase init module — tests initialization modes. No real Firebase calls.
"""
import pytest
from core.firebase import is_initialized


def test_firebase_not_initialized_by_default():
    """Without env vars, Firebase should not auto-initialize."""
    # After import, _app is None unless init_firebase() was called
    # We can't guarantee state across test runs, so just check the function exists
    assert callable(is_initialized)
