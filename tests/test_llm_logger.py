"""
tests/test_llm_logger.py
Unit tests for core/llm_logger.py — LLM call logging decorator.
"""

from unittest.mock import patch

import core.llm_logger as llm_logger


class TestLogLlmCall:
    def test_returns_result(self):
        @llm_logger.log_llm_call(task="test", model_id="m1", provider="p1")
        def add(a=0, b=0):
            return a + b

        assert add(a=1, b=2) == 3

    def test_reraises_exception(self):
        @llm_logger.log_llm_call(task="fail", model_id="m1", provider="p1")
        def boom():
            raise ValueError("bang")

        try:
            boom()
            assert False, "should have raised"
        except ValueError as e:
            assert str(e) == "bang"

    def test_truncates_long_input(self):
        logged = {}

        @llm_logger.log_llm_call(task="trunc", model_id="m1", provider="p1")
        def echo(text=""):
            return text

        with patch.object(llm_logger.llm_log, "debug") as mock_debug:
            echo(text="x" * 3000)
            # Input log should truncate at 2000
            input_call = mock_debug.call_args_list[0][0][0]
            assert "..." in input_call

    def test_truncates_long_output(self):
        @llm_logger.log_llm_call(task="trunc_out", model_id="m1", provider="p1")
        def big():
            return "y" * 5000

        with patch.object(llm_logger.llm_log, "debug") as mock_debug:
            big()
            output_call = mock_debug.call_args_list[1][0][0]
            assert "..." in output_call

    def test_logs_timing(self):
        @llm_logger.log_llm_call(task="time", model_id="m1", provider="p1")
        def noop():
            return "ok"

        with patch.object(llm_logger.llm_log, "info") as mock_info:
            noop()
            end_call = mock_info.call_args_list[1][0][0]
            assert "duration=" in end_call
            assert "output_len=" in end_call
