"""
HLS streaming playback API flow.

These tests cover the STREAMING_PLAN.md contract without real LLM/TTS/DB work:
  /episodes/{id}/audio dispatches to the live playlist while synthesizing,
  /episodes/{id}/stream.m3u8 exposes tokenized chunk URLs,
  /episodes/{id}/stream/chunks/{name} serves local chunks,
  ready episodes still dispatch to the final audio path.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydub import AudioSegment

from api.auth import CurrentUser
from api.routes import episodes


@pytest.fixture()
def client(monkeypatch, tmp_path):
    episode_id = uuid.uuid4()
    token = "test-token"
    rows: dict[str, dict] = {
        "streaming": {
            "audio_path": None,
            "audio_url": None,
            "status": "synthesizing",
            "title": "Streaming Episode",
            "stream_state": "live",
            "stream_chunks": [
                {"key": f"audio/{episode_id}/live/seg_0000.aac", "duration_ms": 1234},
                {"key": f"audio/{episode_id}/live/seg_0001.aac", "duration_ms": 2500},
            ],
        }
    }

    async def fake_resolve_token(auth_header):
        assert auth_header == f"Bearer {token}"
        return CurrentUser(id="user-1", role="user")

    async def fake_fetchrow(query, params):
        assert params["id"] == str(episode_id)
        assert params["user_id"] == "user-1"
        return rows["streaming"]

    monkeypatch.setattr("api.auth._resolve_token", fake_resolve_token)
    monkeypatch.setattr(episodes, "db_fetchrow", fake_fetchrow)
    monkeypatch.setenv("CURIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("CURIA_STORAGE_LOCAL_DIR", str(tmp_path / "blobs"))

    chunk_dir = tmp_path / "blobs" / "audio" / str(episode_id) / "live"
    chunk_dir.mkdir(parents=True)
    (chunk_dir / "seg_0000.aac").write_bytes(b"first-aac")
    (chunk_dir / "seg_0001.aac").write_bytes(b"second-aac")

    app = FastAPI()
    app.include_router(episodes.router)
    test_client = TestClient(app)
    return test_client, episode_id, token, rows, tmp_path


def test_audio_dispatches_to_live_playlist(client):
    test_client, episode_id, token, *_ = client

    response = test_client.get(f"/episodes/{episode_id}/audio?token={token}")

    assert response.status_code == 200
    body = response.json()
    assert body["streaming"] is True
    assert body["url"].endswith(f"/episodes/{episode_id}/stream.m3u8?token={token}")


def test_audio_returns_starting_before_first_chunk(client):
    test_client, episode_id, token, rows, _ = client
    rows["streaming"] = {
        "audio_path": None,
        "audio_url": None,
        "status": "script_ready",
        "title": "Script Ready Episode",
        "stream_state": None,
        "stream_chunks": None,
    }

    response = test_client.get(f"/episodes/{episode_id}/audio?token={token}")

    assert response.status_code == 202
    assert response.json() == {"status": "starting", "retry_in": 2}


def test_playlist_exposes_tokenized_local_chunks_and_endlist(client):
    test_client, episode_id, token, rows, _ = client
    rows["streaming"]["stream_state"] = "ended"

    response = test_client.get(f"/episodes/{episode_id}/stream.m3u8?token={token}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert response.headers["cache-control"] == "public, max-age=60"
    playlist = response.text
    assert "#EXTM3U" in playlist
    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in playlist
    assert "#EXT-X-TARGETDURATION:3" in playlist
    assert "#EXTINF:1.234," in playlist
    assert "#EXTINF:2.500," in playlist
    assert f"/episodes/{episode_id}/stream/chunks/seg_0000.aac?token={token}" in playlist
    assert f"/episodes/{episode_id}/stream/chunks/seg_0001.aac?token={token}" in playlist
    assert playlist.rstrip().endswith("#EXT-X-ENDLIST")


def test_playlist_is_no_store_while_live(client):
    test_client, episode_id, token, *_ = client

    response = test_client.get(f"/episodes/{episode_id}/stream.m3u8?token={token}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "#EXT-X-ENDLIST" not in response.text


def test_local_chunk_endpoint_serves_bytes(client):
    test_client, episode_id, token, *_ = client

    response = test_client.get(f"/episodes/{episode_id}/stream/chunks/seg_0001.aac?token={token}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/aac")
    assert response.content == b"second-aac"


def test_ready_audio_dispatch_uses_final_local_mp3(client):
    test_client, episode_id, token, rows, tmp_path = client
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"mp3-data")
    rows["streaming"] = {
        "audio_path": str(mp3),
        "audio_url": None,
        "status": "ready",
        "title": "Ready Episode",
        "stream_state": "ended",
        "stream_chunks": [],
    }

    response = test_client.get(f"/episodes/{episode_id}/audio?token={token}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == b"mp3-data"


def test_hls_publisher_splits_long_tts_segments(monkeypatch):
    from core.audio.hls import HlsPublisher

    loop = object()
    publisher = HlsPublisher("episode-1", loop, gap_ms=400, target_chunk_ms=8000)
    durations: list[int] = []

    def fake_publish_clip(clip, label):
        durations.append(len(clip))

    monkeypatch.setattr(publisher, "_publish_clip", fake_publish_clip)

    publisher.publish_segment(0, AudioSegment.silent(duration=20500))

    assert durations == [8000, 8000, 4900]
