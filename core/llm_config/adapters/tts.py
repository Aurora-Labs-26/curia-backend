"""
core/llm_config/adapters/tts.py
TTS clients. Provider-pluggable; controlled entirely from config/models.yaml.

Currently supports:
  - elevenlabs         ElevenLabs HTTP API
  - smallest           Smallest.ai Lightning TTS API
  - google_tts         Google Cloud Text-to-Speech (API-key auth path)
  - xai                xAI / Grok TTS (STUB — public TTS endpoint is not yet
                       documented; raises NotImplementedError until verified)
  - edge_tts           Microsoft Edge TTS (free, no API key required)
  - openai_tts         OpenAI Text-to-Speech API
  - cartesia           Cartesia TTS API
  - hume               Hume AI Octave TTS

Stub fallback when no API key for the configured provider is set: writes a
1-second silent WAV per line so the pipeline runs end-to-end.
"""

from __future__ import annotations

import asyncio
import base64
import os
import wave
from typing import TYPE_CHECKING

import httpx
from loguru import logger

if TYPE_CHECKING:
    from ..schema import ModelConfig, ProviderConfig


# ---------------------------------------------------------------------------
# WAV helpers — used by all PCM-returning providers
# ---------------------------------------------------------------------------


def _sample_rate_from_format(fmt: str) -> int:
    """Parse an ElevenLabs-style 'pcm_NNNNN' format string."""
    if fmt.startswith("pcm_"):
        try:
            return int(fmt.split("_", 1)[1])
        except ValueError:
            return 22050
    return 22050


def _write_wav_from_pcm(pcm_bytes: bytes, sample_rate: int, output_path: str) -> None:
    """Wrap raw 16-bit mono PCM into a WAV container."""
    with wave.open(output_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)             # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _write_silent_wav(output_path: str, duration_seconds: float = 1.0) -> None:
    sample_rate = 22050
    num_samples = int(sample_rate * duration_seconds)
    silence = b"\x00\x00" * num_samples
    _write_wav_from_pcm(silence, sample_rate, output_path)


# ---------------------------------------------------------------------------
# Text chunking — Smallest.ai has a ~500-char limit per request
# ---------------------------------------------------------------------------

SMALLEST_MAX_CHARS = 200  # Smallest.ai Lightning hard limit is ~200 chars


def _chunk_text(text: str, max_chars: int = SMALLEST_MAX_CHARS) -> list[str]:
    """Split text into chunks guaranteed to be under max_chars.

    Strategy:
    1. Try sentence boundaries first (cleaner audio breaks).
    2. Any sentence still over max_chars gets split at word boundaries.
    3. Any word still over max_chars gets hard-truncated (last resort).
    """
    import re

    def _by_words(s: str) -> list[str]:
        words = s.split()
        parts: list[str] = []
        cur = ""
        for w in words:
            # Hard-truncate individual words that exceed the limit
            while len(w) > max_chars:
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.append(w[:max_chars])
                w = w[max_chars:]
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= max_chars:
                cur += " " + w
            else:
                parts.append(cur)
                cur = w
        if cur:
            parts.append(cur)
        return parts or [s[:max_chars]]

    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    cur = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            # sentence too long — flush current and split by words
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_by_words(sentence))
        elif not cur:
            cur = sentence
        elif len(cur) + 1 + len(sentence) <= max_chars:
            cur += " " + sentence
        else:
            chunks.append(cur)
            cur = sentence
    if cur:
        chunks.append(cur)
    return chunks or _by_words(text)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class TTSAdapter:
    """
    Encapsulates a configured TTS provider + voice. `synthesize(text, output_path)`
    writes a WAV file at output_path.
    """

    def __init__(
        self,
        provider: "ProviderConfig",
        model: "ModelConfig",
        settings: dict,
        voice_id: str,
    ):
        self.provider = provider
        self.model = model
        self.settings = settings
        self.voice_id = voice_id
        self._warned_stub = False
        self._async_client: httpx.AsyncClient | None = None

    async def _get_async_client(self) -> httpx.AsyncClient:
        """Lazily create and reuse a single async HTTP client for connection pooling."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=60)
        return self._async_client

    async def close_async_client(self) -> None:
        """Close the shared async client. Call after a batch of TTS calls."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    @property
    def output_format(self) -> str:
        """Returns 'mp3' for providers that write MP3, 'wav' for everything else."""
        if self.provider.type in ("edge_tts", "hume"):
            return "mp3"
        if self.provider.type == "openai_tts":
            fmt = self.settings.get("response_format", "wav")
            return "mp3" if fmt == "mp3" else "wav"
        # cartesia always returns WAV (pcm_s16le in wav container)
        return "wav"

    # -- public ------------------------------------------------------------

    def synthesize(self, text: str, output_path: str) -> None:
        import time as _time

        llm_log = logger.bind(log_type="llm")

        llm_log.info(
            f"TTS_START | provider={self.provider.type} "
            f"model={self.model.model_id} voice={self.voice_id} "
            f"text_len={len(text) if text else 0}"
        )
        start = _time.time()

        # edge_tts needs no API key -- bypass the stub check entirely
        if self.provider.type == "edge_tts":
            self._synthesize_edge_tts(text, output_path)
            elapsed = _time.time() - start
            llm_log.info(
                f"TTS_END | provider={self.provider.type} "
                f"model={self.model.model_id} output={output_path} "
                f"duration={elapsed:.2f}s"
            )
            return

        api_key = os.getenv(self.provider.api_key_env)
        if not api_key:
            if not self._warned_stub:
                logger.warning(
                    f"{self.provider.api_key_env} not set -- TTS STUB active "
                    f"(silent WAV) for provider={self.provider.type}."
                )
                self._warned_stub = True
            _write_silent_wav(output_path, duration_seconds=1.0)
            llm_log.info(
                f"TTS_END | provider=stub output={output_path} (silent WAV)"
            )
            return

        if not text or not text.strip():
            _write_silent_wav(output_path, duration_seconds=0.3)
            llm_log.info(
                f"TTS_END | provider={self.provider.type} output={output_path} "
                f"(empty text, silent WAV)"
            )
            return

        try:
            # Dispatch by provider type -- each method writes a WAV at output_path.
            if self.provider.type == "elevenlabs":
                self._synthesize_elevenlabs(text, output_path, api_key)
            elif self.provider.type == "smallest":
                self._synthesize_smallest(text, output_path, api_key)
            elif self.provider.type == "google_tts":
                self._synthesize_google(text, output_path, api_key)
            elif self.provider.type == "xai":
                self._synthesize_xai(text, output_path, api_key)
            elif self.provider.type == "openai_tts":
                self._synthesize_openai_tts(text, output_path, api_key)
            elif self.provider.type == "cartesia":
                self._synthesize_cartesia(text, output_path, api_key)
            elif self.provider.type == "hume":
                self._synthesize_hume(text, output_path, api_key)
            elif self.provider.type == "edge_tts":
                self._synthesize_edge_tts(text, output_path)
            else:
                raise ValueError(f"TTS provider type '{self.provider.type}' not implemented")

            elapsed = _time.time() - start
            llm_log.info(
                f"TTS_END | provider={self.provider.type} "
                f"model={self.model.model_id} output={output_path} "
                f"duration={elapsed:.2f}s"
            )
        except Exception as e:
            elapsed = _time.time() - start
            llm_log.error(
                f"TTS_FAIL | provider={self.provider.type} "
                f"model={self.model.model_id} duration={elapsed:.2f}s error={e}"
            )
            raise

    # -- async public API --------------------------------------------------

    async def synthesize_async(self, text: str, output_path: str) -> None:
        """Async version of synthesize. Uses native async for each provider."""
        import time as _time

        llm_log = logger.bind(log_type="llm")
        llm_log.info(
            f"TTS_ASYNC_START | provider={self.provider.type} "
            f"model={self.model.model_id} voice={self.voice_id} "
            f"text_len={len(text) if text else 0}"
        )
        start = _time.time()

        # edge_tts is natively async — no API key needed
        if self.provider.type == "edge_tts":
            await self._async_edge_tts(text, output_path)
            elapsed = _time.time() - start
            llm_log.info(f"TTS_ASYNC_END | provider=edge_tts duration={elapsed:.2f}s")
            return

        api_key = os.getenv(self.provider.api_key_env)
        if not api_key:
            if not self._warned_stub:
                logger.warning(
                    f"{self.provider.api_key_env} not set -- TTS STUB active "
                    f"(silent WAV) for provider={self.provider.type}."
                )
                self._warned_stub = True
            _write_silent_wav(output_path, duration_seconds=1.0)
            return

        if not text or not text.strip():
            _write_silent_wav(output_path, duration_seconds=0.3)
            return

        try:
            if self.provider.type == "elevenlabs":
                await self._async_elevenlabs(text, output_path, api_key)
            elif self.provider.type == "smallest":
                await self._async_smallest(text, output_path, api_key)
            elif self.provider.type == "google_tts":
                await self._async_google(text, output_path, api_key)
            elif self.provider.type == "openai_tts":
                await self._async_openai_tts(text, output_path, api_key)
            elif self.provider.type == "cartesia":
                await self._async_cartesia(text, output_path, api_key)
            elif self.provider.type == "hume":
                await self._async_hume(text, output_path, api_key)
            elif self.provider.type == "xai":
                await self._async_xai(text, output_path, api_key)
            else:
                raise ValueError(f"Async TTS not implemented for '{self.provider.type}'")

            elapsed = _time.time() - start
            llm_log.info(
                f"TTS_ASYNC_END | provider={self.provider.type} "
                f"duration={elapsed:.2f}s output={output_path}"
            )
        except Exception as e:
            elapsed = _time.time() - start
            llm_log.error(f"TTS_ASYNC_FAIL | provider={self.provider.type} duration={elapsed:.2f}s error={e}")
            raise

    async def synthesize_bytes(self, text: str) -> bytes:
        """Async — returns WAV bytes directly. No temp file at call site."""
        import tempfile

        tmp_path = tempfile.mktemp(suffix=".wav")
        try:
            await self.synthesize_async(text=text, output_path=tmp_path)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # -- async provider implementations ------------------------------------

    async def _async_elevenlabs(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://api.elevenlabs.io/v1").rstrip("/")
        url = f"{base_url}/text-to-speech/{self.voice_id}"
        output_format = self.settings.get("output_format", "pcm_22050")
        voice_settings = {
            "stability": self.settings.get("stability", 0.5),
            "similarity_boost": self.settings.get("similarity_boost", 0.75),
            "style": self.settings.get("style", 0.0),
            "use_speaker_boost": self.settings.get("use_speaker_boost", True),
        }
        client = await self._get_async_client()
        resp = await client.post(
            url,
            params={"output_format": output_format},
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/pcm" if output_format.startswith("pcm_") else "audio/wav",
            },
            json={"text": text, "model_id": self.model.model_id, "voice_settings": voice_settings},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"ElevenLabs error {resp.status_code}: {resp.text[:300]}")
        if output_format.startswith("pcm_"):
            _write_wav_from_pcm(resp.content, _sample_rate_from_format(output_format), output_path)
        else:
            with open(output_path, "wb") as f:
                f.write(resp.content)

    async def _async_openai_tts(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/audio/speech"
        body = {
            "model": self.model.model_id,
            "input": text,
            "voice": self.voice_id,
            "response_format": self.settings.get("response_format", "wav"),
            "speed": self.settings.get("speed", 1.0),
        }
        client = await self._get_async_client()
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI TTS error {resp.status_code}: {resp.text[:300]}")
        with open(output_path, "wb") as f:
            f.write(resp.content)

    async def _async_cartesia(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://api.cartesia.ai").rstrip("/")
        url = f"{base_url}/tts/bytes"
        body = {
            "model_id": self.model.model_id,
            "transcript": text,
            "voice": {"mode": "id", "id": self.voice_id},
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": self.settings.get("sample_rate", 22050),
            },
        }
        client = await self._get_async_client()
        resp = await client.post(
            url,
            headers={
                "X-API-Key": api_key,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Cartesia error {resp.status_code}: {resp.text[:300]}")
        with open(output_path, "wb") as f:
            f.write(resp.content)

    async def _async_smallest(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://waves-api.smallest.ai").rstrip("/")
        endpoint = self.settings.get("endpoint_path", "/api/v1/lightning/get_speech")
        url = f"{base_url}{endpoint}"
        client = await self._get_async_client()
        chunks = _chunk_text(text)
        if len(chunks) == 1:
            body = {
                "voice_id": self.voice_id,
                "text": chunks[0],
                "language": self.settings.get("language", "en"),
                "sample_rate": int(self.settings.get("sample_rate", 22050)),
                "speed": self.settings.get("speed", 1.0),
                "add_wav_header": True,
            }
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Smallest.ai error {resp.status_code}: {resp.text[:300]}")
            with open(output_path, "wb") as f:
                f.write(resp.content)
        else:
            # Multi-chunk: synthesize each, stitch with pydub
            from pydub import AudioSegment
            import tempfile, os
            combined = AudioSegment.empty()
            for chunk in chunks:
                body = {
                    "voice_id": self.voice_id,
                    "text": chunk,
                    "language": self.settings.get("language", "en"),
                    "sample_rate": int(self.settings.get("sample_rate", 22050)),
                    "speed": self.settings.get("speed", 1.0),
                    "add_wav_header": True,
                }
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"Smallest.ai error {resp.status_code}: {resp.text[:300]}")
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(resp.content)
                    tmp_path = tmp.name
                combined += AudioSegment.from_wav(tmp_path)
                os.unlink(tmp_path)
            combined.export(output_path, format="wav")

    async def _async_smallest_with_timings(
        self, text: str, output_path: str, api_key: str
    ) -> list[dict]:
        """
        Calls Smallest.ai Lightning with timestamps=True. Returns a list of word-level
        timing dicts: [{"word": str, "start": float, "end": float}, ...] where
        start/end are in seconds from the beginning of this clip.

        Falls back to empty list if the API doesn't return timestamps (e.g. older endpoint).
        Chunks long text to stay within Smallest.ai's per-request character limit.
        """
        import json as _json

        base_url = (self.provider.base_url or "https://waves-api.smallest.ai").rstrip("/")
        endpoint = self.settings.get("endpoint_path", "/api/v1/lightning/get_speech")
        url = f"{base_url}{endpoint}"
        chunks = _chunk_text(text)

        all_timings: list[dict] = []
        clip_paths: list[str] = []
        import tempfile, os
        time_offset = 0.0

        async with httpx.AsyncClient(timeout=60) as client:
            for chunk in chunks:
                body = {
                    "voice_id": self.voice_id,
                    "text": chunk,
                    "language": self.settings.get("language", "en"),
                    "sample_rate": int(self.settings.get("sample_rate", 22050)),
                    "speed": self.settings.get("speed", 1.0),
                    "add_wav_header": True,
                    "timestamps": True,
                }
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"Smallest.ai error {resp.status_code}: {resp.text[:300]}")

                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    payload = resp.json()
                    audio_b64 = payload.get("audio") or payload.get("audio_data")
                    if audio_b64:
                        audio_bytes = base64.b64decode(audio_b64)
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                            tmp.write(audio_bytes)
                            clip_paths.append(tmp.name)
                        # Offset timings by accumulated duration
                        chunk_timings = payload.get("timestamps") or payload.get("words") or []
                        for t in chunk_timings:
                            all_timings.append({**t, "start": t.get("start", 0) + time_offset,
                                                "end": t.get("end", 0) + time_offset})
                        from pydub import AudioSegment as _AS
                        time_offset += len(_AS.from_wav(clip_paths[-1])) / 1000.0
                    else:
                        # No audio in JSON — synthesize without timings for this chunk
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                            clip_paths.append(tmp.name)
                        await self._async_smallest(chunk, clip_paths[-1], api_key)
                else:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        tmp.write(resp.content)
                        clip_paths.append(tmp.name)

        # Stitch clips together
        if len(clip_paths) == 1:
            import shutil
            shutil.move(clip_paths[0], output_path)
        else:
            from pydub import AudioSegment
            combined = AudioSegment.empty()
            for p in clip_paths:
                combined += AudioSegment.from_wav(p)
                os.unlink(p)
            combined.export(output_path, format="wav")

        return all_timings

    async def _async_google(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://texttospeech.googleapis.com/v1").rstrip("/")
        url = f"{base_url}/text:synthesize"
        language_code = self.settings.get("language_code")
        if not language_code:
            parts = self.voice_id.split("-")
            language_code = "-".join(parts[:2]) if len(parts) >= 2 else "en-US"
        sample_rate = int(self.settings.get("sample_rate_hertz", 22050))
        body = {
            "input": {"text": text},
            "voice": {
                "languageCode": language_code,
                "name": self.voice_id,
                "ssmlGender": self.settings.get("ssml_gender", "NEUTRAL"),
            },
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": sample_rate,
                "speakingRate": self.settings.get("speaking_rate", 1.0),
                "pitch": self.settings.get("pitch", 0.0),
            },
        }
        client = await self._get_async_client()
        resp = await client.post(url, params={"key": api_key}, headers={"Content-Type": "application/json"}, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Google TTS error {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        audio_b64 = payload.get("audioContent")
        if not audio_b64:
            raise RuntimeError("Google TTS response missing audioContent")
        pcm = base64.b64decode(audio_b64)
        if pcm[:4] == b"RIFF":
            with open(output_path, "wb") as f:
                f.write(pcm)
        else:
            _write_wav_from_pcm(pcm, sample_rate, output_path)

    async def _async_edge_tts(self, text: str, output_path: str) -> None:
        """Native async — edge_tts is built on asyncio, no thread pool needed."""
        import shutil
        import edge_tts

        voice = self.voice_id or "en-US-GuyNeural"
        mp3_path = output_path.replace(".wav", ".mp3") if output_path.endswith(".wav") else output_path

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(mp3_path)

        if output_path != mp3_path:
            shutil.move(mp3_path, output_path)

    async def _async_hume(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://api.hume.ai").rstrip("/")
        url = f"{base_url}/v0/tts/file"
        output_fmt = self.settings.get("output_format", "mp3")
        utterance: dict = {"text": text, "voice": {"name": self.voice_id}}
        description = self.settings.get("description")
        if description:
            utterance["description"] = description[:1000]
        body: dict = {
            "utterances": [utterance],
            "format": {"type": output_fmt},
        }
        gen_id = getattr(self, "_hume_generation_id", None)
        if gen_id:
            body["context"] = {"generation_id": gen_id}
        client = await self._get_async_client()
        resp = await client.post(
            url,
            params={"api_key": api_key},
            headers={"Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Hume TTS error {resp.status_code}: {resp.text[:300]}")
        new_gen_id = resp.headers.get("x-hume-generation-id")
        if new_gen_id:
            self._hume_generation_id = new_gen_id
        await asyncio.to_thread(_write_bytes, output_path, resp.content)

    async def _async_xai(self, text: str, output_path: str, api_key: str) -> None:
        endpoint_path = self.settings.get("endpoint_path")
        if not endpoint_path:
            raise NotImplementedError("xAI TTS: no public endpoint documented yet.")
        base_url = (self.provider.base_url or "https://api.x.ai/v1").rstrip("/")
        url = f"{base_url}{endpoint_path}"
        body = {
            "model": self.model.model_id,
            "voice": self.voice_id,
            "text": text,
            **{k: v for k, v in self.settings.items() if k not in ("endpoint_path", "audio_format")},
        }
        audio_format = self.settings.get("audio_format", "wav")
        client = await self._get_async_client()
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"xAI TTS error {resp.status_code}: {resp.text[:300]}")
        if audio_format == "pcm":
            _write_wav_from_pcm(resp.content, int(self.settings.get("sample_rate", 22050)), output_path)
        else:
            with open(output_path, "wb") as f:
                f.write(resp.content)

    # -- sync providers (existing) -----------------------------------------

    def _synthesize_elevenlabs(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://api.elevenlabs.io/v1").rstrip("/")
        url = f"{base_url}/text-to-speech/{self.voice_id}"

        output_format = self.settings.get("output_format", "pcm_22050")
        voice_settings = {
            "stability": self.settings.get("stability", 0.5),
            "similarity_boost": self.settings.get("similarity_boost", 0.75),
            "style": self.settings.get("style", 0.0),
            "use_speaker_boost": self.settings.get("use_speaker_boost", True),
        }

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    params={"output_format": output_format},
                    headers={
                        "xi-api-key": api_key,
                        "Content-Type": "application/json",
                        "Accept": (
                            "audio/wav"
                            if not output_format.startswith("pcm_")
                            else "audio/pcm"
                        ),
                    },
                    json={
                        "text": text,
                        "model_id": self.model.model_id,
                        "voice_settings": voice_settings,
                    },
                )
            if resp.status_code != 200:
                raise RuntimeError(f"ElevenLabs error {resp.status_code}: {resp.text[:300]}")
            if output_format.startswith("pcm_"):
                _write_wav_from_pcm(
                    resp.content,
                    _sample_rate_from_format(output_format),
                    output_path,
                )
            else:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"ElevenLabs request failed: {e}") from e

    def _synthesize_openai_tts(self, text: str, output_path: str, api_key: str) -> None:
        """OpenAI Text-to-Speech API."""
        base_url = (self.provider.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/audio/speech"

        response_format = self.settings.get("response_format", "wav")
        speed = self.settings.get("speed", 1.0)

        body = {
            "model": self.model.model_id,
            "input": text,
            "voice": self.voice_id,
            "response_format": response_format,
            "speed": speed,
        }

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI TTS error {resp.status_code}: {resp.text[:300]}")
            with open(output_path, "wb") as f:
                f.write(resp.content)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"OpenAI TTS request failed: {e}") from e

    def _synthesize_cartesia(self, text: str, output_path: str, api_key: str) -> None:
        """Cartesia TTS API — returns raw WAV bytes."""
        base_url = (self.provider.base_url or "https://api.cartesia.ai").rstrip("/")
        url = f"{base_url}/tts/bytes"

        sample_rate = self.settings.get("sample_rate", 22050)

        body = {
            "model_id": self.model.model_id,
            "transcript": text,
            "voice": {
                "mode": "id",
                "id": self.voice_id,
            },
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": sample_rate,
            },
        }

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    headers={
                        "X-API-Key": api_key,
                        "Cartesia-Version": "2024-06-10",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Cartesia TTS error {resp.status_code}: {resp.text[:300]}")
            with open(output_path, "wb") as f:
                f.write(resp.content)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Cartesia TTS request failed: {e}") from e

    def _synthesize_hume(self, text: str, output_path: str, api_key: str) -> None:
        base_url = (self.provider.base_url or "https://api.hume.ai").rstrip("/")
        url = f"{base_url}/v0/tts/file"
        output_fmt = self.settings.get("output_format", "mp3")
        utterance: dict = {"text": text, "voice": {"name": self.voice_id}}
        description = self.settings.get("description")
        if description:
            utterance["description"] = description[:1000]
        body: dict = {
            "utterances": [utterance],
            "format": {"type": output_fmt},
        }
        gen_id = getattr(self, "_hume_generation_id", None)
        if gen_id:
            body["context"] = {"generation_id": gen_id}
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    params={"api_key": api_key},
                    headers={"Content-Type": "application/json"},
                    json=body,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Hume TTS error {resp.status_code}: {resp.text[:300]}")
            new_gen_id = resp.headers.get("x-hume-generation-id")
            if new_gen_id:
                self._hume_generation_id = new_gen_id
            with open(output_path, "wb") as f:
                f.write(resp.content)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Hume TTS request failed: {e}") from e

    def _synthesize_edge_tts(self, text: str, output_path: str) -> None:
        """Microsoft Edge TTS — free, no API key required. Outputs MP3 directly."""
        import shutil
        import edge_tts
        from concurrent.futures import ThreadPoolExecutor

        voice = self.voice_id or "en-US-GuyNeural"
        mp3_path = output_path.replace(".wav", ".mp3") if output_path.endswith(".wav") else output_path

        def _run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _run():
                    communicate = edge_tts.Communicate(text, voice)
                    await communicate.save(mp3_path)
                loop.run_until_complete(_run())
            finally:
                loop.close()

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(_run_in_thread).result()
            if output_path != mp3_path:
                shutil.move(mp3_path, output_path)
        except Exception as e:
            raise RuntimeError(f"edge_tts synthesis failed: {e}") from e

    def _synthesize_smallest(self, text: str, output_path: str, api_key: str) -> None:
        """
        Smallest.ai Lightning TTS.
        Verify endpoint shape against current Smallest docs; this is a best-effort
        implementation. The API typically returns WAV bytes directly when add_wav_header=true.
        """
        base_url = (self.provider.base_url or "https://waves-api.smallest.ai").rstrip("/")
        endpoint = self.settings.get("endpoint_path", "/api/v1/lightning/get_speech")
        url = f"{base_url}{endpoint}"

        sample_rate = int(self.settings.get("sample_rate", 22050))

        body = {
            "voice_id": self.voice_id,
            "text": text,
            "language": self.settings.get("language", "en"),
            "sample_rate": sample_rate,
            "speed": self.settings.get("speed", 1.0),
            "add_wav_header": True,
        }

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Smallest.ai error {resp.status_code}: {resp.text[:300]}")
            # Response is a complete WAV file when add_wav_header=true; just write it.
            with open(output_path, "wb") as f:
                f.write(resp.content)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Smallest.ai request failed: {e}") from e

    def _synthesize_smallest_with_timings(
        self, text: str, output_path: str, api_key: str
    ) -> list[dict]:
        """
        Sync variant: synthesize via Smallest.ai Lightning with timestamps=True.
        Chunks long text to stay within Smallest.ai's per-request character limit.
        Returns word-level timings list (may be empty if API doesn't support timestamps).
        """
        import tempfile, os as _os
        from pydub import AudioSegment

        base_url = (self.provider.base_url or "https://waves-api.smallest.ai").rstrip("/")
        endpoint = self.settings.get("endpoint_path", "/api/v1/lightning/get_speech")
        url = f"{base_url}{endpoint}"
        chunks = _chunk_text(text)
        logger.debug(f"[tts] input len={len(text)} → {len(chunks)} chunks, sizes={[len(c) for c in chunks]}")

        all_timings: list[dict] = []
        clip_paths: list[str] = []
        time_offset = 0.0

        try:
            with httpx.Client(timeout=60) as client:
                for chunk in chunks:
                    body = {
                        "voice_id": self.voice_id,
                        "text": chunk,
                        "language": self.settings.get("language", "en"),
                        "sample_rate": int(self.settings.get("sample_rate", 22050)),
                        "speed": self.settings.get("speed", 1.0),
                        "add_wav_header": True,
                        "timestamps": True,
                    }
                    resp = client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=body,
                    )
                    if resp.status_code != 200:
                        raise RuntimeError(f"Smallest.ai error {resp.status_code}: {resp.text[:300]}")

                    content_type = resp.headers.get("content-type", "")
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        tmp_path = tmp.name

                    if "application/json" in content_type:
                        payload = resp.json()
                        audio_b64 = payload.get("audio") or payload.get("audio_data")
                        if audio_b64:
                            with open(tmp_path, "wb") as f:
                                f.write(base64.b64decode(audio_b64))
                        else:
                            self._synthesize_smallest(chunk, tmp_path, api_key)
                        chunk_timings = payload.get("timestamps") or payload.get("words") or []
                        for t in chunk_timings:
                            all_timings.append({**t, "start": t.get("start", 0) + time_offset,
                                                "end": t.get("end", 0) + time_offset})
                    else:
                        with open(tmp_path, "wb") as f:
                            f.write(resp.content)

                    clip_paths.append(tmp_path)
                    time_offset += len(AudioSegment.from_wav(tmp_path)) / 1000.0

            if len(clip_paths) == 1:
                import shutil
                shutil.move(clip_paths[0], output_path)
            else:
                combined = AudioSegment.empty()
                for p in clip_paths:
                    combined += AudioSegment.from_wav(p)
                    _os.unlink(p)
                combined.export(output_path, format="wav")

            return all_timings
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Smallest.ai request failed: {e}") from e

    def synthesize_with_timings(self, text: str, output_path: str) -> list[dict]:
        """
        Public method: synthesize and return word-level timing data.
        Only Smallest.ai supports this natively; other providers return [].
        """
        if self.provider.type == "edge_tts":
            self._synthesize_edge_tts(text, output_path)
            return []

        api_key = os.getenv(self.provider.api_key_env, "")
        if not api_key:
            _write_silent_wav(output_path, duration_seconds=1.0)
            return []

        if not text or not text.strip():
            _write_silent_wav(output_path, duration_seconds=0.3)
            return []

        if self.provider.type == "smallest":
            return self._synthesize_smallest_with_timings(text, output_path, api_key)

        # All other providers: synthesize normally, return no timing data
        self.synthesize(text, output_path)
        return []

    async def synthesize_async_with_timings(self, text: str, output_path: str) -> list[dict]:
        """
        Async public method: synthesize and return word-level timing data.
        Only Smallest.ai supports this natively; other providers return [].
        """
        if self.provider.type == "edge_tts":
            await self._async_edge_tts(text, output_path)
            return []

        api_key = os.getenv(self.provider.api_key_env, "")
        if not api_key:
            _write_silent_wav(output_path, duration_seconds=1.0)
            return []

        if not text or not text.strip():
            _write_silent_wav(output_path, duration_seconds=0.3)
            return []

        if self.provider.type == "smallest":
            return await self._async_smallest_with_timings(text, output_path, api_key)

        await self.synthesize_async(text, output_path)
        return []

    def _synthesize_google(self, text: str, output_path: str, api_key: str) -> None:
        """
        Google Cloud Text-to-Speech (API-key auth path).
        For service-account auth, set GOOGLE_APPLICATION_CREDENTIALS instead and
        swap the auth header — out of scope here.

        Voice naming: voice_id maps to Google's `name` field, e.g.:
          - en-US-Neural2-J
          - en-US-Studio-O
          - en-GB-Wavenet-D
        Language code is taken from settings.language_code or inferred from voice_id.
        """
        base_url = (self.provider.base_url or "https://texttospeech.googleapis.com/v1").rstrip("/")
        url = f"{base_url}/text:synthesize"

        # Infer language code from voice_id if not explicitly set
        language_code = self.settings.get("language_code")
        if not language_code:
            parts = self.voice_id.split("-")
            language_code = "-".join(parts[:2]) if len(parts) >= 2 else "en-US"

        sample_rate = int(self.settings.get("sample_rate_hertz", 22050))

        body = {
            "input": {"text": text},
            "voice": {
                "languageCode": language_code,
                "name": self.voice_id,
                "ssmlGender": self.settings.get("ssml_gender", "NEUTRAL"),
            },
            "audioConfig": {
                "audioEncoding": "LINEAR16",   # 16-bit PCM
                "sampleRateHertz": sample_rate,
                "speakingRate": self.settings.get("speaking_rate", 1.0),
                "pitch": self.settings.get("pitch", 0.0),
            },
        }

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    params={"key": api_key},
                    headers={"Content-Type": "application/json"},
                    json=body,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Google TTS error {resp.status_code}: {resp.text[:300]}")
            payload = resp.json()
            audio_b64 = payload.get("audioContent")
            if not audio_b64:
                raise RuntimeError("Google TTS response missing audioContent")
            pcm = base64.b64decode(audio_b64)
            # LINEAR16 already includes a WAV header from Google's API output —
            # but to be safe we wrap raw PCM ourselves. Inspect the bytes: if they
            # start with 'RIFF', it's already a WAV; write as-is.
            if pcm[:4] == b"RIFF":
                with open(output_path, "wb") as f:
                    f.write(pcm)
            else:
                _write_wav_from_pcm(pcm, sample_rate, output_path)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Google TTS request failed: {e}") from e

    def _synthesize_xai(self, text: str, output_path: str, api_key: str) -> None:
        """
        xAI / Grok TTS — STUB.

        As of writing, xAI's public API at api.x.ai documents chat/completions but
        not a Text-to-Speech endpoint. Grok voice mode exists in the consumer app;
        if/when xAI publishes a TTS API, swap the placeholder URL below for the
        real one and adjust the request shape.

        The override pattern: set settings.endpoint_path to the documented path
        once available, and set settings.audio_format to whatever the API returns.
        """
        endpoint_path = self.settings.get("endpoint_path")
        if not endpoint_path:
            raise NotImplementedError(
                "xAI TTS adapter is a stub: no public TTS endpoint is currently documented "
                "by xAI. Set settings.endpoint_path on the model in config/models.yaml "
                "once xAI ships a TTS API, then this method will issue a Bearer-auth POST "
                "to {base_url}{endpoint_path}."
            )

        base_url = (self.provider.base_url or "https://api.x.ai/v1").rstrip("/")
        url = f"{base_url}{endpoint_path}"

        body = {
            "model": self.model.model_id,
            "voice": self.voice_id,
            "text": text,
            **{k: v for k, v in self.settings.items() if k not in ("endpoint_path", "audio_format")},
        }
        audio_format = self.settings.get("audio_format", "wav")

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"xAI TTS error {resp.status_code}: {resp.text[:300]}")
            if audio_format == "pcm":
                _write_wav_from_pcm(
                    resp.content,
                    int(self.settings.get("sample_rate", 22050)),
                    output_path,
                )
            else:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"xAI TTS request failed: {e}") from e


def build_tts(
    provider: "ProviderConfig",
    model: "ModelConfig",
    settings: dict,
    voice_id: str,
) -> TTSAdapter:
    if model.kind != "tts":
        raise ValueError(
            f"build_tts called with model kind={model.kind} (expected 'tts') "
            f"for {model.model_id}"
        )
    return TTSAdapter(
        provider=provider, model=model, settings=settings, voice_id=voice_id
    )
