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
    def test_get_preferences_returns_saved_prefs(self):
        with patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value={
                "display_name": "Arihant", "location_name": "Mumbai, India",
                "scheduled_time": "09:00:00", "timezone": "Asia/Kolkata",
            })
            store.get_user_topics = AsyncMock(return_value={
                "chosen": [{"name": "Tech"}, {"name": "Business"}],
                "custom": [{"name": "chess"}],
            })
            r = _client().get("/brief/preferences")
        assert r.status_code == 200
        assert r.json() == {
            "display_name": "Arihant",
            "beats": ["Tech", "Business"],
            "custom_topics": ["chess"],
            "scheduled_time": "09:00:00",
            "timezone": "Asia/Kolkata",
            "location_name": "Mumbai, India",
        }

    def test_get_preferences_404_when_never_set(self):
        with patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value=None)
            r = _client().get("/brief/preferences")
        assert r.status_code == 404

    def test_preferences_omitted_city_keeps_existing(self):
        """None (field absent) = leave the stored city and country untouched —
        the edit sheet can save beats/schedule without re-sending location."""
        with patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value={
                "location_name": "Pune, Maharashtra, India", "location_country": "IN"})
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={"display_name": "Arihant"})
        assert r.status_code == 200
        assert r.json() == {"location_name": "Pune, Maharashtra, India"}
        assert store.create_user_with_topics.await_args.args[2] == "Pune, Maharashtra, India"
        assert store.create_user_with_topics.await_args.args[7] == "IN"



def _audio_client():
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
            store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
            store.get_daily_brief_for_date = AsyncMock(return_value=None)
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 404

    def test_409_when_audio_not_ready(self):
        with patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": None})
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 409

    def test_s3_backend_returns_presigned_json(self):
        with patch("api.routes.brief.store") as store, \
             patch("core.storage.blob.get_storage_backend", return_value="s3"), \
             patch("core.storage.blob.generate_presigned_url", return_value="https://signed.example/x"):
            store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
            store.get_daily_brief_for_date = AsyncMock(
                return_value={"stitched_mp3_url": "audio/brief/b-1.mp3"})
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 200
        assert r.json() == {"url": "https://signed.example/x"}

    def test_s3_backend_signing_failure_is_500(self):
        with patch("api.routes.brief.store") as store, \
             patch("core.storage.blob.get_storage_backend", return_value="s3"), \
             patch("core.storage.blob.generate_presigned_url", return_value=None):
            store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
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
            store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
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
            store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
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
            store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
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
                store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
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
             patch.object(audio.store, "set_latest_manifest_segments", AsyncMock()), \
             patch("core.llm_config.resolve.tts", return_value=adapter), \
             patch("core.storage.blob.upload_file", upload), \
             patch.object(audio, "_stitch",
                          return_value=(42.0, [(300, 1300), (1900, 2400), (3000, 3600)])):
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
    # WEATHER_PROVIDER pinned to "weatherapi" throughout: these tests assert
    # WeatherAPI.com's specific response shape. OpenWeatherMap (the new
    # default) has its own coverage in TestOpenWeatherProvider below.
    async def test_no_key_short_circuits_without_http(self):
        from brief import weather
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "", "OPENWEATHER_API_KEY": "",
                                        "WEATHER_PROVIDER": "weatherapi"}), \
             patch("brief.weather.httpx.AsyncClient") as client:
            assert await weather.get_weather_and_local_time("Mumbai") == ("", "")
        client.assert_not_called()

    async def test_no_location_short_circuits(self):
        from brief import weather
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k", "WEATHER_PROVIDER": "weatherapi"}):
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
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k", "WEATHER_PROVIDER": "weatherapi"}), \
             patch("brief.weather.httpx.AsyncClient", return_value=self._client(resp)):
            out = await weather.get_weather_and_local_time("Mumbai")
        assert out == ("Sunny, 31.0°C", "2026-07-23 09:00")

    async def test_non_200_returns_empty(self):
        from brief import weather
        resp = MagicMock(status_code=403, text="quota")
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k", "WEATHER_PROVIDER": "weatherapi"}), \
             patch("brief.weather.httpx.AsyncClient", return_value=self._client(resp)):
            assert await weather.get_weather_and_local_time("Mumbai") == ("", "")

    async def test_network_exception_swallowed(self):
        from brief import weather
        with patch.dict("os.environ", {"WEATHERAPI_KEY": "k", "WEATHER_PROVIDER": "weatherapi"}), \
             patch("brief.weather.httpx.AsyncClient",
                   return_value=self._client(exc=RuntimeError("dns"))):
            assert await weather.get_weather_and_local_time("Mumbai") == ("", "")


class TestOpenWeatherProvider:
    def _client(self, resp=None, exc=None):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=resp, side_effect=exc)
        return client

    async def test_default_provider_is_openweather(self):
        from brief import weather
        with patch.dict("os.environ", {"OPENWEATHER_API_KEY": "k"}):
            assert weather.settings.WEATHER_PROVIDER == "openweather"

    async def test_happy_path_prefers_coordinates_when_given(self):
        from brief import weather
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "weather": [{"description": "clear sky"}],
            "main": {"temp": 22.5},
            "timezone": 19800,
        }
        with patch.dict("os.environ", {"OPENWEATHER_API_KEY": "k", "WEATHER_PROVIDER": "openweather"}), \
             patch("brief.weather.httpx.AsyncClient", return_value=self._client(resp)) as client:
            weather_str, local_time = await weather.get_weather_and_local_time(
                "Mumbai, Maharashtra, India", 19.076, 72.877
            )
        assert weather_str == "Clear sky, 22.5°C"
        assert local_time
        called_params = client.return_value.get.await_args.kwargs["params"]
        assert called_params["lat"] == 19.076 and called_params["lon"] == 72.877
        assert "q" not in called_params

    async def test_falls_back_to_free_text_without_coordinates(self):
        from brief import weather
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "weather": [{"description": "clear sky"}], "main": {"temp": 22.5}, "timezone": 0,
        }
        with patch.dict("os.environ", {"OPENWEATHER_API_KEY": "k", "WEATHER_PROVIDER": "openweather"}), \
             patch("brief.weather.httpx.AsyncClient", return_value=self._client(resp)) as client:
            await weather.get_weather_and_local_time("Mumbai, Maharashtra, India")
        called_params = client.return_value.get.await_args.kwargs["params"]
        assert called_params["q"] == "Mumbai, Maharashtra, India"
        assert "lat" not in called_params

    async def test_falls_back_to_weatherapi_when_only_that_key_is_set(self):
        from brief import weather
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "current": {"condition": {"text": "Sunny"}, "temp_c": 31.0},
            "location": {"localtime": "2026-07-23 09:00"}}
        with patch.dict("os.environ", {"OPENWEATHER_API_KEY": "", "WEATHERAPI_KEY": "k",
                                        "WEATHER_PROVIDER": "openweather"}), \
             patch("brief.weather.httpx.AsyncClient", return_value=self._client(resp)) as client:
            out = await weather.get_weather_and_local_time("Mumbai")
        assert out == ("Sunny, 31.0°C", "2026-07-23 09:00")
        called_url = client.return_value.get.await_args.args[0]
        assert "weatherapi.com" in called_url


# ---------------------------------------------------------------------------
# User-local date keying — briefs are keyed by the user's own calendar date
# everywhere else (dispatcher local_date, transcript records); the routes must
# look up / create / enqueue by the same key or users east of UTC get
# off-by-one behavior around their midnight.
# ---------------------------------------------------------------------------

from datetime import datetime, timezone as _tz


class TestUserLocalDate:
    def test_kolkata_rolls_to_next_day_before_utc(self):
        from api.routes.brief import _user_local_date
        # 20:00 UTC Aug 3 = 01:30 IST Aug 4
        now = datetime(2026, 8, 3, 20, 0, tzinfo=_tz.utc)
        assert _user_local_date({"timezone": "Asia/Kolkata"}, now) == "2026-08-04"
        assert _user_local_date({"timezone": "UTC"}, now) == "2026-08-03"

    def test_deprecated_alias_works(self):
        from api.routes.brief import _user_local_date
        now = datetime(2026, 8, 3, 20, 0, tzinfo=_tz.utc)
        assert _user_local_date({"timezone": "Asia/Calcutta"}, now) == "2026-08-04"

    def test_garbage_or_missing_timezone_falls_back_to_utc(self):
        from api.routes.brief import _user_local_date
        now = datetime(2026, 8, 3, 20, 0, tzinfo=_tz.utc)
        assert _user_local_date({"timezone": "Nope/Nope"}, now) == "2026-08-03"
        assert _user_local_date({}, now) == "2026-08-03"


class TestRoutesUseUserLocalDate:
    """Routes are asserted against a SENTINEL date via a patched
    _user_local_date — comparing against a live-computed IST date would pass
    a UTC-reverted route for the ~18h/day the two calendars agree (caught by
    a survived mutant)."""
    SENTINEL = "2099-01-01"

    def _expected(self):
        return self.SENTINEL

    def _sentinel_patch(self):
        return patch("api.routes.brief._user_local_date", return_value=self.SENTINEL)

    def test_today_looks_up_by_user_local_date(self):
        with self._sentinel_patch(), patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value={
                "id": "u1", "scheduled_time": "09:00:00", "timezone": "Asia/Kolkata"})
            store.get_daily_brief_for_date = AsyncMock(return_value=None)
            store.is_user_due_now = AsyncMock(return_value=False)
            store.get_user_topics = AsyncMock(return_value={"chosen": [], "custom": []})
            r = _client().get("/brief/today")
        assert r.status_code == 200
        assert store.get_daily_brief_for_date.await_args.args[1] == self._expected()

    def test_eager_create_and_enqueue_use_user_local_date(self):
        with self._sentinel_patch(), patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.enqueue", AsyncMock()) as enq:
            store.get_user = AsyncMock(return_value={
                "id": "u1", "scheduled_time": "00:01:00", "timezone": "Asia/Kolkata"})
            store.get_daily_brief_for_date = AsyncMock(return_value=None)
            store.is_user_due_now = AsyncMock(return_value=True)
            store.get_or_create_daily_brief = AsyncMock(
                return_value={"id": "b-1", "status": "generating", "created": True})
            store.get_daily_brief_detail = AsyncMock(
                return_value={"id": "b-1", "status": "generating", "articles": []})
            store.get_latest_manifest = AsyncMock(return_value=[])
            _client().get("/brief/today")
        assert store.get_or_create_daily_brief.await_args.args[1] == self._expected()
        assert enq.await_args.kwargs["payload"]["date"] == self._expected()

    def test_generate_enqueues_user_local_date(self):
        with self._sentinel_patch(), patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.enqueue",
                   AsyncMock(return_value="aaaaaaaa-0000-0000-0000-000000000003")) as enq:
            store.get_user = AsyncMock(return_value={
                "id": "u1", "timezone": "Asia/Kolkata"})
            store.get_or_create_daily_brief = AsyncMock(
                return_value={"id": "12345678-1234-1234-1234-123456789012"})
            r = _client().post("/brief/generate")
        assert r.status_code == 202
        assert enq.await_args.kwargs["payload"]["date"] == self._expected()

    def test_today_audio_looks_up_by_user_local_date(self):
        with self._sentinel_patch(), patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value={
                "id": "u1", "timezone": "Asia/Kolkata"})
            store.get_daily_brief_for_date = AsyncMock(return_value=None)
            r = _audio_client().get("/brief/today/audio")
        assert r.status_code == 404
        assert store.get_daily_brief_for_date.await_args.args[1] == self._expected()


class TestGpsLocationPath:
    """The "local news" toggle: app sends a device GPS fix; the backend
    reverse-geocodes to a verified place and stores DEVICE coordinates."""

    def _match(self):
        return {"display": "Mumbai, Maharashtra, India", "name": "Mumbai",
                "country_code": "IN", "timezone": "", "latitude": 19.076,
                "longitude": 72.877}

    def test_gps_fix_resolves_and_stores_device_coords(self):
        with patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.cities.reverse_geocode",
                   AsyncMock(return_value=self._match())) as rev:
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={
                "display_name": "A", "latitude": 19.076, "longitude": 72.877})
        assert r.status_code == 200
        assert r.json() == {"location_name": "Mumbai, Maharashtra, India"}
        rev.assert_awaited_once_with(19.076, 72.877)
        args = store.create_user_with_topics.await_args.args
        assert args[2] == "Mumbai, Maharashtra, India"
        assert args[-3:] == ("IN", 19.076, 72.877)

    def test_explicit_nulls_clear_location(self):
        """Toggle OFF: the app sends explicit nulls — distinct from omitting
        the fields entirely (which keeps the stored location)."""
        with patch("api.routes.brief.store") as store:
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={
                "display_name": "A", "latitude": None, "longitude": None})
        assert r.status_code == 200
        assert r.json() == {"location_name": ""}
        assert store.create_user_with_topics.await_args.args[2] == ""

    def test_location_name_field_is_ignored_not_stored(self):
        """Old clients may still send location_name — it must be ignored, not
        stored verbatim (free text can no longer enter the table)."""
        with patch("api.routes.brief.store") as store:
            store.get_user = AsyncMock(return_value=None)
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={
                "display_name": "A", "location_name": "timbaktu"})
        assert r.status_code == 200
        assert store.create_user_with_topics.await_args.args[2] == ""

    def test_lat_without_lon_422(self):
        r = _client().put("/brief/preferences",
                          json={"display_name": "A", "latitude": 19.0})
        assert r.status_code == 422

    def test_out_of_range_coords_422(self):
        r = _client().put("/brief/preferences", json={
            "display_name": "A", "latitude": 91.0, "longitude": 10.0})
        assert r.status_code == 422

    def test_unresolvable_fix_422_never_stored(self):
        with patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.cities.reverse_geocode",
                   AsyncMock(return_value=None)):
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={
                "display_name": "A", "latitude": 0.0, "longitude": -140.0})
        assert r.status_code == 422
        store.create_user_with_topics.assert_not_awaited()

    def test_reverse_outage_503_never_stored(self):
        from brief.cities import GeocoderUnavailable
        with patch("api.routes.brief.store") as store, \
             patch("api.routes.brief.cities.reverse_geocode",
                   AsyncMock(side_effect=GeocoderUnavailable("down"))):
            store.create_user_with_topics = AsyncMock()
            r = _client().put("/brief/preferences", json={
                "display_name": "A", "latitude": 19.0, "longitude": 72.8})
        assert r.status_code == 503
        store.create_user_with_topics.assert_not_awaited()


# ---------------------------------------------------------------------------
# Offline-first enablers: richer push payload, progress replay guard, ETag.
# ---------------------------------------------------------------------------

from datetime import datetime as _dt


class TestOfflineEnablers:
    async def test_push_payload_carries_brief_id_and_date(self):
        from worker.handlers import brief as h
        runner = AsyncMock(return_value={"status": "ready", "brief_id": "b-9"})
        push = AsyncMock()
        with patch.object(h, "generate_brief_for_user", runner), \
             patch("brief.audio.render_brief_audio", AsyncMock()), \
             patch("core.notifications.send_brief_ready", push):
            await h.handle_generate_brief({"user_id": "u1", "date": "2026-08-12"})
        assert push.await_args.kwargs == {"brief_id": "b-9", "brief_date": "2026-08-12"}

    def test_progress_with_client_ts_threads_through(self):
        with patch("api.routes.brief.store") as store:
            store.set_daily_brief_progress = AsyncMock(return_value="applied")
            r = _client().put(
                "/brief/12345678-1234-1234-1234-123456789012/progress",
                json={"play_progress": 0.4, "client_ts": "2026-08-12T09:00:00Z"})
        assert r.status_code == 204
        assert store.set_daily_brief_progress.await_args.args[4] is not None

    def test_stale_progress_is_204_not_404(self):
        """A superseded queued update is SUCCESS for the client outbox — a 404
        would make it retry forever."""
        with patch("api.routes.brief.store") as store:
            store.set_daily_brief_progress = AsyncMock(return_value="stale")
            r = _client().put(
                "/brief/12345678-1234-1234-1234-123456789012/progress",
                json={"play_progress": 0.1, "client_ts": "2026-08-12T08:00:00Z"})
        assert r.status_code == 204

    def test_unknown_brief_still_404(self):
        with patch("api.routes.brief.store") as store:
            store.set_daily_brief_progress = AsyncMock(return_value="not_found")
            r = _client().put(
                "/brief/12345678-1234-1234-1234-123456789012/progress",
                json={"play_progress": 0.1})
        assert r.status_code == 404

    def _today_mocks(self, store):
        store.get_user = AsyncMock(return_value={"id": "u1", "timezone": "UTC"})
        store.get_daily_brief_for_date = AsyncMock(return_value={"id": "b-1"})
        store.get_daily_brief_detail = AsyncMock(return_value={
            "id": "b-1", "status": "ready", "articles": []})
        store.get_latest_manifest = AsyncMock(return_value=[{"kind": "intro",
                                                            "duration_s": 3.0}])

    def test_today_sets_etag_and_304s_on_match(self):
        with patch("api.routes.brief.store") as store:
            self._today_mocks(store)
            c = _client()
            r1 = c.get("/brief/today")
            etag = r1.headers.get("etag")
            assert r1.status_code == 200 and etag
            r2 = c.get("/brief/today", headers={"If-None-Match": etag})
        assert r2.status_code == 304
        assert r2.content == b""

    def test_changed_content_changes_etag(self):
        with patch("api.routes.brief.store") as store:
            self._today_mocks(store)
            c = _client()
            e1 = c.get("/brief/today").headers["etag"]
            store.get_latest_manifest = AsyncMock(return_value=[{"kind": "intro",
                                                                "duration_s": 9.9}])
            r = c.get("/brief/today", headers={"If-None-Match": e1})
        assert r.status_code == 200
        assert r.headers["etag"] != e1
