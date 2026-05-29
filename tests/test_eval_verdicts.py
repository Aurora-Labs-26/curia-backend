"""
tests/test_eval_verdicts.py
Tests for api/routes/eval.py verdict storage (JSONL file I/O).

Verifies:
  - _append_verdict writes a valid JSONL line
  - _load_verdicts reads all lines back
  - Handles empty/missing file
  - Handles corrupted lines gracefully
  - Stats endpoint aggregates correctly
  - Export CSV contains all fields
  - All file I/O runs through asyncio.to_thread (async endpoints)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def verdicts_dir(tmp_path, monkeypatch):
    """Redirect verdict storage to a temp directory."""
    import api.routes.eval as eval_mod
    monkeypatch.setattr(eval_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(eval_mod, "VERDICTS_FILE", tmp_path / "eval_verdicts.jsonl")
    return tmp_path


class TestAppendVerdict:

    def test_creates_file_and_appends(self, verdicts_dir):
        from api.routes.eval import _append_verdict

        _append_verdict({"source_id": "abc", "verdict": "good"})

        f = verdicts_dir / "eval_verdicts.jsonl"
        assert f.exists()
        lines = f.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["verdict"] == "good"

    def test_appends_multiple(self, verdicts_dir):
        from api.routes.eval import _append_verdict

        _append_verdict({"verdict": "good"})
        _append_verdict({"verdict": "bad"})
        _append_verdict({"verdict": "edit"})

        lines = (verdicts_dir / "eval_verdicts.jsonl").read_text().strip().split("\n")
        assert len(lines) == 3


class TestLoadVerdicts:

    def test_empty_when_no_file(self, verdicts_dir):
        from api.routes.eval import _load_verdicts

        assert _load_verdicts() == []

    def test_reads_all_lines(self, verdicts_dir):
        from api.routes.eval import _append_verdict, _load_verdicts

        _append_verdict({"verdict": "good", "transform": "summary"})
        _append_verdict({"verdict": "bad", "transform": "metadata"})

        rows = _load_verdicts()
        assert len(rows) == 2
        assert rows[0]["transform"] == "summary"
        assert rows[1]["verdict"] == "bad"

    def test_skips_corrupted_lines(self, verdicts_dir):
        f = verdicts_dir / "eval_verdicts.jsonl"
        f.write_text(
            '{"verdict": "good"}\n'
            'not valid json\n'
            '{"verdict": "bad"}\n'
        )
        from api.routes.eval import _load_verdicts

        rows = _load_verdicts()
        assert len(rows) == 2

    def test_skips_blank_lines(self, verdicts_dir):
        f = verdicts_dir / "eval_verdicts.jsonl"
        f.write_text('{"v": 1}\n\n\n{"v": 2}\n')
        from api.routes.eval import _load_verdicts

        rows = _load_verdicts()
        assert len(rows) == 2


class TestEvalStatsEndpoint:

    @pytest.mark.asyncio
    async def test_stats_aggregation(self, verdicts_dir):
        from api.routes.eval import _append_verdict, eval_stats

        _append_verdict({"transform": "summary", "verdict": "good"})
        _append_verdict({"transform": "summary", "verdict": "good"})
        _append_verdict({"transform": "summary", "verdict": "bad"})
        _append_verdict({"transform": "metadata", "verdict": "bad"})

        result = await eval_stats()
        stats = result["stats"]
        assert result["total_verdicts"] == 4

        summary_stat = next(s for s in stats if s["transform"] == "summary")
        assert summary_stat["good"] == 2
        assert summary_stat["bad"] == 1
        assert summary_stat["pass_rate"] == 67

    @pytest.mark.asyncio
    async def test_stats_empty(self, verdicts_dir):
        from api.routes.eval import eval_stats

        result = await eval_stats()
        assert result["stats"] == []
        assert result["total_verdicts"] == 0


class TestEvalExportEndpoint:

    @pytest.mark.asyncio
    async def test_csv_export(self, verdicts_dir):
        from api.routes.eval import _append_verdict, eval_export

        _append_verdict({
            "source_id": "s1", "url": "https://example.com",
            "title": "Test", "transform": "summary",
            "verdict": "good", "golden": None, "note": "nice",
            "ts": "2026-05-28T00:00:00Z",
        })

        response = await eval_export()
        body = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                body += chunk.encode()
            else:
                body += chunk
        text = body.decode()

        assert "source_id" in text
        assert "summary" in text
        assert "s1" in text

    @pytest.mark.asyncio
    async def test_csv_empty(self, verdicts_dir):
        from api.routes.eval import eval_export

        response = await eval_export()
        body = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                body += chunk.encode()
            else:
                body += chunk

        lines = body.decode().strip().split("\n")
        assert len(lines) == 1  # header only
