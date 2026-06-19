"""
tests/test_logging_setup.py
Tests for core/logging.py — logging initialization.
"""

from unittest.mock import patch

import core.logging as logging_mod


class TestSetupLogging:
    def setup_method(self):
        """Reset the module's _initialized flag before each test."""
        logging_mod._initialized = False

    def teardown_method(self):
        logging_mod._initialized = False

    def test_idempotent(self, tmp_path):
        with patch.object(logging_mod, "LOGS_DIR", tmp_path):
            logging_mod.setup_logging("api")
            # Second call should be a no-op
            logging_mod.setup_logging("api")
        assert logging_mod._initialized is True

    def test_creates_logs_dir(self, tmp_path):
        logs_dir = tmp_path / "new_logs"
        with patch.object(logging_mod, "LOGS_DIR", logs_dir):
            logging_mod.setup_logging("worker")
        assert logs_dir.exists()

    def test_sets_initialized_flag(self, tmp_path):
        with patch.object(logging_mod, "LOGS_DIR", tmp_path):
            assert logging_mod._initialized is False
            logging_mod.setup_logging("api")
            assert logging_mod._initialized is True

    def test_custom_log_level(self, tmp_path):
        with patch.object(logging_mod, "LOGS_DIR", tmp_path), \
             patch.dict("os.environ", {"CURIA_LOG_LEVEL": "DEBUG"}):
            logging_mod.setup_logging("api")
        assert logging_mod._initialized is True
