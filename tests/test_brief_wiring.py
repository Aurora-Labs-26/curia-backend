"""
tests/test_brief_wiring.py — TDD for the daily-brief integration wiring:
worker handlers (worker/handlers/brief.py), API routes (api/routes/brief.py),
and the audio pass (brief/audio.py). Store/TTS/blob/runners all mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


class TestHandlerRegistry:
    def test_brief_handlers_registered(self):
        from worker.handlers import HANDLERS, handle_preopt_brief, handle_generate_brief
        assert HANDLERS["preopt_brief"] is handle_preopt_brief
        assert HANDLERS["generate_brief"] is handle_generate_brief


class TestHandleGenerateBrief:
    async def test_missing_user_id_raises(self):
        from worker.handlers.brief import handle_generate_brief
        with pytest.raises(ValueError, match="user_id"):
            await handle_generate_brief({})

    async def test_runs_runner_then_audio(self):
        from worker.handlers import brief as h
        runner = AsyncMock(return_value={"status": "completed", "brief_id": "b-1"})
        audio = AsyncMock(return_value="audio/brief/b-1.mp3")
        with patch.object(h, "generate_brief_for_user", runner), \
             patch("brief.audio.render_brief_audio", audio):
            await h.handle_generate_brief({"user_id": "u1", "date": "2026-07-23"})
        runner.assert_awaited_once_with("u1", "2026-07-23")
        audio.assert_awaited_once_with("b-1")

    async def test_audio_failure_never_fails_the_job(self):
        from worker.handlers import brief as h
        runner = AsyncMock(return_value={"status": "completed", "brief_id": "b-1"})
        audio = AsyncMock(side_effect=RuntimeError("tts down"))
        with patch.object(h, "generate_brief_for_user", runner), \
             patch("brief.audio.render_brief_audio", audio):
            await h.handle_generate_brief({"user_id": "u1"})  # must not raise

    async def test_no_brief_id_skips_audio(self):
        from worker.handlers import brief as h
        runner = AsyncMock(return_value={"status": "failed"})
        audio = AsyncMock()
        with patch.object(h, "generate_brief_for_user", runner), \
             patch("brief.audio.render_brief_audio", audio):
            await h.handle_generate_brief({"user_id": "u1"})
        audio.assert_not_awaited()


class TestHandlePreopt:
    async def test_topic_id_routes_to_single_topic(self):
        from worker.handlers import brief as h
        one = AsyncMock(); alltopics = AsyncMock()
        with patch.object(h, "run_preopt_for_topic_id", one), \
             patch.object(h, "run_preopt", alltopics):
            await h.handle_preopt_brief({"topic_id": "t-9"})
        one.assert_awaited_once_with("t-9")
        alltopics.assert_not_awaited()

    async def test_empty_payload_runs_all_beats(self):
        from worker.handlers import brief as h
        one = AsyncMock(); alltopics = AsyncMock()
        with patch.object(h, "run_preopt_for_topic_id", one), \
             patch.object(h, "run_preopt", alltopics):
            await h.handle_preopt_brief({})
        alltopics.assert_awaited_once()
        one.assert_not_awaited()


# ---------------------------------------------------------------------------
# API routes — app with auth overridden
# ---------------------------------------------------------------------------


def _client():
    from api.auth import current_user_id
    from api.routes.brief import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user_id] = lambda: "u1"
    return TestClient(app)


class TestBriefRoutes:
    def test_preferences_upserts_user_with_topics(self):
        """An explicit location_name (the prefs UI's "wrong city?" correction)
        is used as-is — no IP lookup."""
        with patch("api.routes.brief.store") as store:
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={
                "display_name": "Arihant", "location_name": "Mumbai, India",
                "beats": ["Tech & Science"], "custom_topics": ["chess"],
                "scheduled_time": "09:00", "timezone": "Asia/Kolkata",
            })
        assert r.status_code == 204
        store.create_user_with_topics.assert_awaited_once_with(
            "u1", "Arihant", "Mumbai, India", "09:00",
            ["Tech & Science"], ["chess"], "Asia/Kolkata")

    def test_preferences_derives_city_from_ip_when_not_given(self):
        """No location_name -> resolved from the request IP, so the client never
        has to ask for a location permission or make the user type a city."""
        with patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.geo.city_from_request",
                   AsyncMock(return_value="Bengaluru")):
            store.get_user = AsyncMock(return_value=None)
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={
                "display_name": "Arihant", "beats": ["Tech & Science"],
            })
        assert r.status_code == 204
        assert store.create_user_with_topics.await_args.args[2] == "Bengaluru"

    def test_preferences_failed_lookup_keeps_existing_city(self):
        """A failed lookup must not wipe a city already resolved (or corrected
        by the user) on an earlier save."""
        with patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.geo.city_from_request", AsyncMock(return_value="")):
            store.get_user = AsyncMock(return_value={"location_name": "Pune"})
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={"display_name": "Arihant"})
        assert r.status_code == 204
        assert store.create_user_with_topics.await_args.args[2] == "Pune"

    def test_preferences_rejects_too_many_beats(self):
        r = _client().put("/brief/preferences", json={
            "display_name": "A", "location_name": "X", "beats": [f"b{i}" for i in range(8)]})
        assert r.status_code == 422

    def test_generate_409_without_prefs(self):
        with patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value=None)
            r = _client().post("/brief/generate")
        assert r.status_code == 409

    def test_generate_enqueues_interactive_and_returns_brief_id(self):
        bid = "12345678-1234-1234-1234-123456789012"
        with patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.enqueue", AsyncMock(return_value="aaaaaaaa-0000-0000-0000-000000000001")) as enq:
            store.get_user = AsyncMock(return_value={"id": "u1"})
            store.get_or_create_daily_brief = AsyncMock(return_value={"id": bid})
            r = _client().post("/brief/generate")
        assert r.status_code == 202
        assert r.json()["id"] == bid
        assert enq.await_args.kwargs["lane"] == "interactive"
        assert enq.await_args.kwargs["type"] == "generate_brief"

    def test_today_404_when_prefs_never_set(self):
        """No daily_briefs row AND no harness.users row — genuinely never
        opted in. The client uses this to decide whether to show the pinned
        brief section at all."""
        with patch("api.routes.brief.store") as store:
            store.get_daily_brief_for_date = AsyncMock(return_value=None)
            store.get_user = AsyncMock(return_value=None)
            r = _client().get("/brief/today")
        assert r.status_code == 404

    def test_today_pending_when_prefs_set_but_not_due(self):
        """No daily_briefs row YET but harness.users exists — prefs are set,
        the user's scheduled time just hasn't arrived. Distinct from the 404
        case above: the client shows a pending card (with the chosen beats
        as chips) instead of hiding the section entirely."""
        with patch("api.routes.brief.store") as store:
            store.get_daily_brief_for_date = AsyncMock(return_value=None)
            store.get_user = AsyncMock(return_value={"id": "u1", "scheduled_time": "09:00:00"})
            store.get_user_topics = AsyncMock(return_value={
                "chosen": [{"name": "Tech"}, {"name": "Business"}],
                "custom": [{"name": "chess"}],
            })
            r = _client().get("/brief/today")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        assert body["beats"] == ["Tech", "Business", "chess"]
        assert body["scheduled_time"] == "09:00:00"

    def test_today_returns_detail(self):
        with patch("api.routes.brief.store") as store:
            store.get_daily_brief_for_date = AsyncMock(return_value={"id": "b-1"})
            store.get_daily_brief_detail = AsyncMock(
                return_value={"id": "b-1", "status": "ready", "articles": []})
            store.get_latest_manifest = AsyncMock(return_value=[])
            r = _client().get("/brief/today")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"

    def test_today_merges_manifest_for_chapters(self):
        """intro/outro text lives only in the persisted manifest, but the client
        needs the whole ordered run to build chapter offsets — so /today merges
        it in rather than making the client stitch two calls together."""
        manifest = [
            {"kind": "intro", "text": "Good morning.", "duration_s": 12.0},
            {"kind": "lead", "text": "Big story.", "duration_s": 60.0},
            {"kind": "outro", "text": "That's the brief.", "duration_s": 8.0},
        ]
        with patch("api.routes.brief.store") as store:
            store.get_daily_brief_for_date = AsyncMock(return_value={"id": "b-1"})
            store.get_daily_brief_detail = AsyncMock(
                return_value={"id": "b-1", "status": "ready", "articles": []})
            store.get_latest_manifest = AsyncMock(return_value=manifest)
            r = _client().get("/brief/today")
        body = r.json()
        assert [s["kind"] for s in body["segments"]] == ["intro", "lead", "outro"]
        assert body["total_duration_s"] == 80.0

    def test_preopt_enqueues_background(self):
        with patch("api.routes.brief.enqueue", AsyncMock(return_value="aaaaaaaa-0000-0000-0000-000000000002")) as enq:
            r = _client().post("/brief/preopt")
        assert r.status_code == 202
        assert enq.await_args.kwargs["lane"] == "background"

    def test_registered_on_main_app(self):
        # app.routes holds _IncludedRouter wrappers (no .path) for every
        # include_router'd module on this FastAPI version, so read the schema.
        from api.main import app
        paths = set(app.openapi()["paths"])
        assert "/brief/generate" in paths and "/brief/today" in paths
        assert "/brief/today/audio" in paths and "/brief/{brief_id}/progress" in paths


def _audio_client():
    """GET /today/audio depends on audio_user_id (accepts ?token=), not
    current_user_id — a separate override from _client() above."""
    from api.auth import audio_user_id
    from api.routes.brief import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[audio_user_id] = lambda: "u1"
    return TestClient(app)


class TestBriefAudioRoute:
    """Regression coverage for a real bug found manually: daily_briefs.
    stitched_mp3_url always holds the bare key brief/audio.py passed to
    upload_file (mirrors studio/generator.py's audio_url = r2_key pattern for
    episodes) — never a file:// URI. Which it means depends on
    get_storage_backend(), not the string's shape. The first version of this
    route sniffed for a "file://" prefix that never actually occurs, which
    always fell through to the s3 presign path and 500'd on local dev."""

    def test_404_when_no_brief_today(self):
        with patch("api.routes.brief.store") as store:
            store.get_daily_brief_for_date = AsyncMock(return_value=None)
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 404

    def test_409_when_audio_not_ready(self):
        with patch("api.routes.brief.store") as store:
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": None})
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 409

    def test_s3_backend_returns_presigned_json(self):
        with patch("api.routes.brief.store") as store, \
             patch("core.storage.blob.get_storage_backend", return_value="s3"), \
             patch("core.storage.blob.generate_presigned_url", return_value="https://signed.example/x"):
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": "audio/brief/b-1.mp3"})
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 200
        assert r.json() == {"url": "https://signed.example/x"}

    def test_s3_backend_signing_failure_is_500(self):
        with patch("api.routes.brief.store") as store, \
             patch("core.storage.blob.get_storage_backend", return_value="s3"), \
             patch("core.storage.blob.generate_presigned_url", return_value=None):
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": "audio/brief/b-1.mp3"})
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 500

    def test_local_backend_streams_the_file_bytes(self, tmp_path):
        blob_dir = tmp_path / "blobs"
        (blob_dir / "audio" / "brief").mkdir(parents=True)
        audio_file = blob_dir / "audio" / "brief" / "b-1.mp3"
        audio_file.write_bytes(b"x" * 100)

        with patch("api.routes.brief.store") as store, \
             patch("core.storage.blob.get_storage_backend", return_value="local"), \
             patch.dict("os.environ", {"CURIA_STORAGE_LOCAL_DIR": str(blob_dir)}):
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": "audio/brief/b-1.mp3"})
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 200
        assert r.content == b"x" * 100
        assert r.headers["content-type"] == "audio/mpeg"

    def test_local_backend_honors_range_header(self, tmp_path):
        blob_dir = tmp_path / "blobs"
        (blob_dir / "audio" / "brief").mkdir(parents=True)
        (blob_dir / "audio" / "brief" / "b-1.mp3").write_bytes(b"0123456789")

        with patch("api.routes.brief.store") as store, \
             patch("core.storage.blob.get_storage_backend", return_value="local"), \
             patch.dict("os.environ", {"CURIA_STORAGE_LOCAL_DIR": str(blob_dir)}):
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": "audio/brief/b-1.mp3"})
            r = _audio_client().get("/brief/today/audio", headers={"Range": "bytes=2-4"})
        assert r.status_code == 206
        assert r.content == b"234"
        assert r.headers["content-range"] == "bytes 2-4/10"

    def test_local_backend_missing_file_is_410(self, tmp_path):
        blob_dir = tmp_path / "blobs"
        blob_dir.mkdir()
        with patch("api.routes.brief.store") as store, \
             patch("core.storage.blob.get_storage_backend", return_value="local"), \
             patch.dict("os.environ", {"CURIA_STORAGE_LOCAL_DIR": str(blob_dir)}):
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": "audio/brief/missing.mp3"})
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 410

    def test_query_param_token_authenticates_without_header(self):
        """RNTP/expo-av can't set custom headers on the URL they're handed —
        this is the whole reason audio_user_id (shared with the episode audio
        route) accepts ?token= as a fallback."""
        from api.auth import audio_user_id
        from api.routes.brief import router

        app = FastAPI()
        app.include_router(router)
        # No override here — exercise the real dependency with a fake
        # Firebase failure + legacy api_token lookup, same as auth.py's
        # documented fallback chain.
        with patch("api.auth._resolve_token", AsyncMock(return_value=type("U", (), {"id": "u1"})())) as resolve:
            client = TestClient(app)
            with patch("api.routes.brief.store") as store, \
                 patch("core.storage.blob.get_storage_backend", return_value="s3"), \
                 patch("core.storage.blob.generate_presigned_url", return_value="https://signed.example/x"):
                store.get_daily_brief_for_date = AsyncMock(
                    return_value={"stitched_mp3_url": "audio/brief/b-1.mp3"})
                r = client.get("/brief/today/audio?token=ck_abc")
        assert r.status_code == 200
        resolve.assert_awaited_once_with("Bearer ck_abc")


# ---------------------------------------------------------------------------
# Audio pass
# ---------------------------------------------------------------------------


def _manifest():
    return [{"segment_type": "intro", "text": "Good morning."},
            {"segment_type": "lead", "text": "Big story today."},
            {"segment_type": "outro", "text": "That's the brief."}]


class TestRenderBriefAudio:
    async def test_not_ready_skips_everything(self):
        from brief import audio
        with patch.object(audio.store, "get_daily_brief_detail",
                          AsyncMock(return_value={"brief": {"status": "generating"}, "articles": []})), \
             patch("core.llm_config.resolve") as resolve:
            assert await audio.render_brief_audio("b-1") is None
        resolve.tts.assert_not_called()

    async def test_empty_manifest_returns_none(self):
        from brief import audio
        with patch.object(audio.store, "get_daily_brief_detail", AsyncMock(
                return_value={"brief": {"status": "ready", "user_id": "u1",
                                        "date": "2026-07-23"}, "articles": []})), \
             patch.object(audio.store, "get_latest_manifest", AsyncMock(return_value=[])):
            assert await audio.render_brief_audio("b-1") is None

    async def test_happy_path_synthesizes_stitches_uploads_and_stores_key(self):
        from brief import audio
        adapter = MagicMock(output_format="mp3")
        adapter.synthesize_async = AsyncMock()
        set_url = AsyncMock()
        upload = AsyncMock()
        with patch.object(audio.store, "get_daily_brief_detail", AsyncMock(
                return_value={"brief": {"status": "ready", "user_id": "u1",
                                        "date": "2026-07-23"}, "articles": []})), \
             patch.object(audio.store, "get_latest_manifest",
                          AsyncMock(return_value=_manifest())), \
             patch.object(audio.store, "set_daily_brief_audio", set_url), \
             patch("core.llm_config.resolve.tts", return_value=adapter), \
             patch("core.storage.blob.upload_file", upload), \
             patch.object(audio, "_stitch", return_value=42.0):
            key = await audio.render_brief_audio("b-1")
        assert key == "audio/brief/b-1.mp3"
        assert adapter.synthesize_async.await_count == 3
        upload.assert_awaited_once()
        assert upload.await_args.args[1] == "audio/brief/b-1.mp3"
        set_url.assert_awaited_once_with("b-1", "audio/brief/b-1.mp3")

    async def test_failure_returns_none_and_never_touches_db(self):
        from brief import audio
        adapter = MagicMock(output_format="mp3")
        adapter.synthesize_async = AsyncMock(side_effect=RuntimeError("tts 500"))
        set_url = AsyncMock()
        with patch.object(audio.store, "get_daily_brief_detail", AsyncMock(
                return_value={"brief": {"status": "ready", "user_id": "u1",
                                        "date": "2026-07-23"}, "articles": []})), \
             patch.object(audio.store, "get_latest_manifest",
                          AsyncMock(return_value=_manifest())), \
             patch.object(audio.store, "set_daily_brief_audio", set_url), \
             patch("core.llm_config.resolve.tts", return_value=adapter):
            assert await audio.render_brief_audio("b-1") is None
        set_url.assert_not_awaited()


class TestManifestStoreHelpers:
    async def test_set_daily_brief_audio_updates_only_stitched(self):
        from brief import store
        pool = MagicMock(); pool.execute = AsyncMock()
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            await store.set_daily_brief_audio("b-1", "audio/brief/b-1.mp3")
        sql = pool.execute.await_args.args[0]
        assert "stitched_mp3_url" in sql and "intro" not in sql and "status" not in sql

    async def test_get_latest_manifest_parses_json_string(self):
        from brief import store
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"segments": '[{"text": "hi"}]'})
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            m = await store.get_latest_manifest("u1", "2026-07-23")
        assert m == [{"text": "hi"}]


# ---------------------------------------------------------------------------
# Weather — every degradation path returns ("", "") and never raises
# ---------------------------------------------------------------------------


class TestWeather:
    async def test_no_key_short_circuits_without_http(self):
        from brief import weather
        with patch.dict("os.environ", {"WEATHERAPI_KEY": ""}), \
             patch("brief.weather.httpx.AsyncClient") as client:
            assert await weather.get_weather_and_local_time("Mumbai") == ("", "")
        client.assert_not_called()

    async def test_no_location_short_circuits(self):
        from brief import weather
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k"}):
            assert await weather.get_weather_and_local_time("") == ("", "")

    def _client(self, resp=None, exc=None):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=resp, side_effect=exc)
        return client

    async def test_happy_path_formats_condition_and_temp(self):
        from brief import weather
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "current": {"condition": {"text": "Sunny"}, "temp_c": 31.0},
            "location": {"localtime": "2026-07-23 09:00"}}
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k"}), \
             patch("brief.weather.httpx.AsyncClient", return_value=self._client(resp)):
            out = await weather.get_weather_and_local_time("Mumbai")
        assert out == ("Sunny, 31.0°C", "2026-07-23 09:00")

    async def test_non_200_returns_empty(self):
        from brief import weather
        resp = MagicMock(status_code=403, text="quota")
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k"}), \
             patch("brief.weather.httpx.AsyncClient", return_value=self._client(resp)):
            assert await weather.get_weather_and_local_time("Mumbai") == ("", "")

    async def test_network_exception_swallowed(self):
        from brief import weather
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k"}), \
             patch("brief.weather.httpx.AsyncClient",
                   return_value=self._client(exc=RuntimeError("dns"))):
            assert await weather.get_weather_and_local_time("Mumbai") == ("", "")
