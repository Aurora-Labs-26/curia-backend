"""
tests/test_queue_sqs.py
SQS queue backend — lane routing, receive/ack/fail semantics, worker dispatch.
boto3 is mocked throughout; no AWS or DB required.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import core.queue as q


INTERACTIVE_URL = "https://sqs.test/curia-interactive"
BACKGROUND_URL = "https://sqs.test/curia-background"

SQS_ENV = {
    "CURIA_QUEUE_BACKEND": "sqs",
    "CURIA_SQS_INTERACTIVE_URL": INTERACTIVE_URL,
    "CURIA_SQS_BACKGROUND_URL": BACKGROUND_URL,
}


@pytest.fixture(autouse=True)
def _reset_sqs_client():
    q._sqs = None
    yield
    q._sqs = None


class TestBackendToggle:
    def test_default_is_postgres(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CURIA_QUEUE_BACKEND", None)
            assert q.get_queue_backend() == "postgres"

    def test_sqs_when_set(self):
        with patch.dict(os.environ, SQS_ENV):
            assert q.get_queue_backend() == "sqs"

    def test_queue_url_resolution(self):
        with patch.dict(os.environ, SQS_ENV):
            assert q._queue_url("interactive") == INTERACTIVE_URL
            assert q._queue_url("background") == BACKGROUND_URL

    def test_queue_url_missing_env_raises(self):
        with patch.dict(os.environ, {"CURIA_QUEUE_BACKEND": "sqs"}, clear=False):
            os.environ.pop("CURIA_SQS_INTERACTIVE_URL", None)
            with pytest.raises(RuntimeError):
                q._queue_url("interactive")


class TestLaneDefaults:
    def test_default_lane_map(self):
        assert q._DEFAULT_LANE["ingest"] == "interactive"
        assert q._DEFAULT_LANE["generate_episode"] == "background"
        assert q._DEFAULT_LANE["generate_ideas"] == "background"
        assert q._DEFAULT_LANE["optimize"] == "background"

    async def test_enqueue_rejects_unknown_lane(self):
        with pytest.raises(ValueError):
            await q.enqueue("ingest", {}, lane="express")


def _mock_db_insert(job_id):
    """Mock get_db() returning a conn whose fetchrow returns a job id."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": job_id})
    conn.execute = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, conn


class TestEnqueueSqs:
    async def test_enqueue_sends_to_lane_queue(self):
        job_id = uuid4()
        ctx, _ = _mock_db_insert(job_id)
        sqs = MagicMock()
        with patch.dict(os.environ, SQS_ENV), \
             patch.object(q, "get_db", return_value=ctx), \
             patch.object(q, "_sqs_client", return_value=sqs):
            out = await q.enqueue("ingest", {"source_id": "s1"}, user_id="u1", lane="interactive")
        assert out == job_id
        sqs.send_message.assert_called_once()
        kwargs = sqs.send_message.call_args.kwargs
        assert kwargs["QueueUrl"] == INTERACTIVE_URL
        body = json.loads(kwargs["MessageBody"])
        assert body["job_id"] == str(job_id)
        assert body["type"] == "ingest"
        assert body["payload"] == {"source_id": "s1"}

    async def test_enqueue_default_lane_background_for_episode(self):
        job_id = uuid4()
        ctx, _ = _mock_db_insert(job_id)
        sqs = MagicMock()
        with patch.dict(os.environ, SQS_ENV), \
             patch.object(q, "get_db", return_value=ctx), \
             patch.object(q, "_sqs_client", return_value=sqs):
            await q.enqueue("generate_episode", {"episode_id": "e1"})
        assert sqs.send_message.call_args.kwargs["QueueUrl"] == BACKGROUND_URL

    async def test_enqueue_send_failure_marks_row_failed_and_raises(self):
        job_id = uuid4()
        ctx, _ = _mock_db_insert(job_id)
        sqs = MagicMock()
        sqs.send_message.side_effect = RuntimeError("sqs down")
        with patch.dict(os.environ, SQS_ENV), \
             patch.object(q, "get_db", return_value=ctx), \
             patch.object(q, "_sqs_client", return_value=sqs), \
             patch.object(q, "fail_permanently", new=AsyncMock()) as mock_fp:
            with pytest.raises(RuntimeError):
                await q.enqueue("ingest", {}, lane="interactive")
        mock_fp.assert_awaited_once()

    async def test_enqueue_postgres_backend_sends_nothing(self):
        job_id = uuid4()
        ctx, _ = _mock_db_insert(job_id)
        sqs = MagicMock()
        with patch.dict(os.environ, {"CURIA_QUEUE_BACKEND": "postgres"}), \
             patch.object(q, "get_db", return_value=ctx), \
             patch.object(q, "_sqs_client", return_value=sqs):
            await q.enqueue("ingest", {}, lane="interactive")
        sqs.send_message.assert_not_called()


def _sqs_message(job_id, type_="ingest", receive_count=1, payload=None):
    return {
        "Messages": [
            {
                "ReceiptHandle": "rh-1",
                "Body": json.dumps(
                    {
                        "job_id": str(job_id),
                        "type": type_,
                        "payload": payload or {},
                        "user_id": "u1",
                        "correlation_id": None,
                    }
                ),
                "Attributes": {"ApproximateReceiveCount": str(receive_count)},
            }
        ]
    }


class TestSqsReceive:
    async def test_receive_returns_job_and_stamps_running(self):
        job_id = uuid4()
        ctx, conn = _mock_db_insert(job_id)
        sqs = MagicMock()
        sqs.receive_message.return_value = _sqs_message(job_id, receive_count=2)
        with patch.dict(os.environ, SQS_ENV), \
             patch.object(q, "get_db", return_value=ctx), \
             patch.object(q, "_sqs_client", return_value=sqs):
            job, receipt = await q.sqs_receive("interactive", worker_id="w1")
        assert job.id == job_id
        assert job.attempts == 2
        assert receipt == "rh-1"
        conn.execute.assert_awaited_once()  # the 'running' stamp

    async def test_receive_empty_returns_none(self):
        sqs = MagicMock()
        sqs.receive_message.return_value = {}
        with patch.dict(os.environ, SQS_ENV), patch.object(q, "_sqs_client", return_value=sqs):
            assert await q.sqs_receive("background") is None

    async def test_malformed_message_deleted_and_skipped(self):
        sqs = MagicMock()
        sqs.receive_message.return_value = {
            "Messages": [{"ReceiptHandle": "rh-bad", "Body": "not json{", "Attributes": {}}]
        }
        with patch.dict(os.environ, SQS_ENV), patch.object(q, "_sqs_client", return_value=sqs):
            assert await q.sqs_receive("interactive") is None
        sqs.delete_message.assert_called_once()


class TestSqsAckFail:
    async def test_ack_deletes_and_stamps_done(self):
        sqs = MagicMock()
        with patch.dict(os.environ, SQS_ENV), \
             patch.object(q, "_sqs_client", return_value=sqs), \
             patch.object(q, "ack", new=AsyncMock()) as mock_ack:
            await q.sqs_ack(uuid4(), "rh-1", "interactive")
        sqs.delete_message.assert_called_once_with(QueueUrl=INTERACTIVE_URL, ReceiptHandle="rh-1")
        mock_ack.assert_awaited_once()

    async def test_fail_does_not_delete_message(self):
        job_id = uuid4()
        ctx, conn = _mock_db_insert(job_id)
        sqs = MagicMock()
        with patch.dict(os.environ, SQS_ENV), \
             patch.object(q, "get_db", return_value=ctx), \
             patch.object(q, "_sqs_client", return_value=sqs):
            await q.sqs_fail(job_id, "boom", attempts=1, final=False)
        sqs.delete_message.assert_not_called()
        status_arg = conn.execute.call_args.args[1]
        assert status_arg == "queued"  # retrying — visible to /jobs polling

    async def test_fail_final_stamps_failed(self):
        job_id = uuid4()
        ctx, conn = _mock_db_insert(job_id)
        with patch.dict(os.environ, SQS_ENV), patch.object(q, "get_db", return_value=ctx):
            await q.sqs_fail(job_id, "boom", attempts=3, final=True)
        assert conn.execute.call_args.args[1] == "failed"

    async def test_fail_permanently_deletes_message(self):
        sqs = MagicMock()
        with patch.dict(os.environ, SQS_ENV), \
             patch.object(q, "_sqs_client", return_value=sqs), \
             patch.object(q, "fail_permanently", new=AsyncMock()) as mock_fp:
            await q.sqs_fail_permanently(uuid4(), "rh-1", "background", "404")
        sqs.delete_message.assert_called_once_with(QueueUrl=BACKGROUND_URL, ReceiptHandle="rh-1")
        mock_fp.assert_awaited_once()


class TestWorkerSqsDispatch:
    async def test_success_path_acks(self):
        import worker.main as wm
        from core.queue import Job

        job = Job(id=uuid4(), type="ingest", payload={}, user_id="u1",
                  attempts=1, max_attempts=3, correlation_id=None)
        with patch.object(wm, "sqs_receive", new=AsyncMock(return_value=(job, "rh-1"))), \
             patch.object(wm, "sqs_ack", new=AsyncMock()) as mock_ack, \
             patch.dict(wm.HANDLERS, {"ingest": AsyncMock()}):
            processed = await wm._process_one_sqs("w1")
        assert processed is True
        mock_ack.assert_awaited_once()

    async def test_retryable_failure_stamps_not_final_then_final(self):
        import worker.main as wm
        from core.queue import Job

        async def boom(payload):
            raise RuntimeError("transient")

        for attempts, expect_final in ((1, False), (3, True)):
            job = Job(id=uuid4(), type="ingest", payload={}, user_id="u1",
                      attempts=attempts, max_attempts=3, correlation_id=None)
            with patch.object(wm, "sqs_receive", new=AsyncMock(return_value=(job, "rh"))), \
                 patch.object(wm, "sqs_fail", new=AsyncMock()) as mock_fail, \
                 patch.dict(wm.HANDLERS, {"ingest": boom}):
                await wm._process_one_sqs("w1")
            assert mock_fail.call_args.kwargs["final"] is expect_final

    async def test_permanent_error_deletes(self):
        import worker.main as wm
        from core.errors import PermanentError
        from core.queue import Job

        async def perma(payload):
            raise PermanentError("404")

        job = Job(id=uuid4(), type="ingest", payload={}, user_id="u1",
                  attempts=1, max_attempts=3, correlation_id=None)
        with patch.object(wm, "sqs_receive", new=AsyncMock(return_value=(job, "rh"))), \
             patch.object(wm, "sqs_fail_permanently", new=AsyncMock()) as mock_fp, \
             patch.dict(wm.HANDLERS, {"ingest": perma}):
            await wm._process_one_sqs("w1")
        mock_fp.assert_awaited_once()

    async def test_empty_queue_returns_false(self):
        import worker.main as wm

        with patch.object(wm, "sqs_receive", new=AsyncMock(return_value=None)):
            assert await wm._process_one_sqs("w1") is False
