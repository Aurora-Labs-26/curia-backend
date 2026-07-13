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

    @property
    def output_format(self) -> str:
        """Returns 'mp3' for providers that write MP3, 'wav' for everything else."""
        if self.provider.type in ("edge_tts", "hume", "deepgram"):
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
            elif self.provider.type == "deepgram":
                self._synthesize_deepgram(text, output_path, api_key)
            elif self.provider.type == "sarvam":
                self._synthesize_sarvam(text, output_path, api_key)
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
            elif self.provider.type == "deepgram":
                await self._async_deepgram(text, output_path, api_key)
            elif self.provider.type == "sarvam":
                await self._async_sarvam(text, output_path, api_key)
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
            def _read_and_cleanup():
                with open(tmp_path, "rb") as f:
                    return f.read()
            return await asyncio.to_thread(_read_and_cleanup)
        finally:
            def _cleanup():
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            await asyncio.to_thread(_cleanup)

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
        async with httpx.AsyncClient(timeout=120) as client:
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
            await asyncio.to_thread(_write_wav_from_pcm, resp.content, _sample_rate_from_format(output_format), output_path)
        else:
            await asyncio.to_thread(_write_bytes, output_path, resp.content)

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
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI TTS error {resp.status_code}: {resp.text[:300]}")
        await asyncio.to_thread(_write_bytes, output_path, resp.content)

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
        async with httpx.AsyncClient(timeout=120) as client:
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
        await asyncio.to_thread(_write_bytes, output_path, resp.content)

    async def _async_smallest(self, text: str, output_path: str, api_key: str) -> None:
        """Smallest.ai Lightning TTS via SDK — SDK handles chunking internally."""
        from smallestai import AsyncSmallestAI

        sample_rate = int(self.settings.get("sample_rate", 22050))
        speed = float(self.settings.get("speed", 1.0))
        language = self.settings.get("language", "en")

        async with AsyncSmallestAI(api_key=api_key) as client:
            chunks: list[bytes] = []
            async for chunk in await client.waves.synthesize_lightning_v31(
                text=text,
                voice_id=self.voice_id,
                sample_rate=sample_rate,
                speed=speed,
                language=language,
                output_format="wav",
            ):
                chunks.append(chunk)
        audio_bytes = b"".join(chunks)
        await asyncio.to_thread(_write_bytes, output_path, audio_bytes)

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
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, params={"key": api_key}, headers={"Content-Type": "application/json"}, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Google TTS error {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        audio_b64 = payload.get("audioContent")
        if not audio_b64:
            raise RuntimeError("Google TTS response missing audioContent")
        pcm = base64.b64decode(audio_b64)
        if pcm[:4] == b"RIFF":
            await asyncio.to_thread(_write_bytes, output_path, pcm)
        else:
            await asyncio.to_thread(_write_wav_from_pcm, pcm, sample_rate, output_path)

    async def _async_edge_tts(self, text: str, output_path: str) -> None:
        """Native async — edge_tts is built on asyncio, no thread pool needed."""
        import shutil
        import edge_tts

        voice = self.voice_id or "en-US-GuyNeural"
        mp3_path = output_path.replace(".wav", ".mp3") if output_path.endswith(".wav") else output_path

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(mp3_path)

        if output_path != mp3_path:
            await asyncio.to_thread(shutil.move, mp3_path, output_path)

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
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"xAI TTS error {resp.status_code}: {resp.text[:300]}")
        if audio_format == "pcm":
            await asyncio.to_thread(_write_wav_from_pcm, resp.content, int(self.settings.get("sample_rate", 22050)), output_path)
        else:
            await asyncio.to_thread(_write_bytes, output_path, resp.content)

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
            with httpx.Client(timeout=120) as client:
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
            with httpx.Client(timeout=120) as client:
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
            with httpx.Client(timeout=120) as client:
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
        """Smallest.ai Lightning TTS via SDK — SDK handles chunking internally."""
        from smallestai import SmallestAI

        sample_rate = int(self.settings.get("sample_rate", 22050))
        speed = float(self.settings.get("speed", 1.0))
        language = self.settings.get("language", "en")

        try:
            client = SmallestAI(api_key=api_key)
            audio_chunks: list[bytes] = list(client.waves.synthesize_lightning_v31(
                text=text,
                voice_id=self.voice_id,
                sample_rate=sample_rate,
                speed=speed,
                language=language,
                output_format="wav",
            ))
            _write_bytes(output_path, b"".join(audio_chunks))
        except Exception as e:
            raise RuntimeError(f"Smallest.ai SDK request failed: {e}") from e

    def synthesize_with_timings(self, text: str, output_path: str) -> list[dict]:
        """
        Synthesize and return word-level timing data.
        Hume returns real word timestamps. Smallest.ai uses the SDK (no timestamp support
        in their REST API) and returns [] — caller falls back to proportional timing.
        All other providers also return [].
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

        if self.provider.type == "hume":
            return self._synthesize_hume_with_timings(text, output_path, api_key)

        if self.provider.type == "smallest":
            # Smallest.ai REST API does not return word timestamps; use the SDK path
            # and return [] so the caller uses proportional timing fallback.
            self._synthesize_smallest(text, output_path, api_key)
            return []

        # All other providers: synthesize normally, return no timing data
        self.synthesize(text, output_path)
        return []

    async def synthesize_async_with_timings(self, text: str, output_path: str) -> list[dict]:
        """
        Async synthesize and return word-level timing data.
        Hume returns real word timestamps. Smallest.ai uses the SDK (no REST timestamp
        support) and returns []. All other providers also return [].
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

        if self.provider.type == "hume":
            return await self._async_hume_with_timings(text, output_path, api_key)

        if self.provider.type == "smallest":
            # Smallest.ai REST API does not return word timestamps; use the SDK path
            # and return [] so the caller uses proportional timing fallback.
            await self._async_smallest(text, output_path, api_key)
            return []

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
            with httpx.Client(timeout=120) as client:
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
            with httpx.Client(timeout=120) as client:
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

    def _synthesize_hume(self, text: str, output_path: str, api_key: str) -> None:
        """Hume AI Octave TTS via SDK — returns MP3, chains generation_id for voice consistency."""
        from hume import HumeClient
        from hume.tts import PostedUtterance, PostedUtteranceVoiceWithName, FormatMp3, FormatWav
        from hume.tts import PostedContextWithGenerationId

        voice_provider = self.settings.get("voice_provider", "HUME_AI")
        output_fmt = self.settings.get("output_format", "mp3")
        fmt = FormatMp3() if output_fmt == "mp3" else FormatWav()

        utterance = PostedUtterance(
            text=text,
            voice=PostedUtteranceVoiceWithName(name=self.voice_id, provider=voice_provider),
        )

        gen_id = getattr(self, "_hume_generation_id", None)
        context = PostedContextWithGenerationId(generation_id=gen_id) if gen_id else None

        try:
            client = HumeClient(api_key=api_key)
            kwargs: dict = dict(utterances=[utterance], format=fmt, num_generations=1, version="2")
            if context is not None:
                kwargs["context"] = context
            result = client.tts.synthesize_json(**kwargs)

            generation = result.generations[0]
            audio_bytes = base64.b64decode(generation.audio)
            _write_bytes(output_path, audio_bytes)
            self._hume_generation_id = generation.generation_id
        except Exception as e:
            raise RuntimeError(f"Hume TTS SDK request failed: {e}") from e

    def _synthesize_hume_with_timings(self, text: str, output_path: str, api_key: str) -> list[dict]:
        """Hume AI Octave TTS with word-level timestamps. Chains generation_id."""
        from hume import HumeClient
        from hume.tts import PostedUtterance, PostedUtteranceVoiceWithName, FormatMp3, FormatWav
        from hume.tts import PostedContextWithGenerationId

        voice_provider = self.settings.get("voice_provider", "HUME_AI")
        output_fmt = self.settings.get("output_format", "mp3")
        fmt = FormatMp3() if output_fmt == "mp3" else FormatWav()

        utterance = PostedUtterance(
            text=text,
            voice=PostedUtteranceVoiceWithName(name=self.voice_id, provider=voice_provider),
        )

        gen_id = getattr(self, "_hume_generation_id", None)
        context = PostedContextWithGenerationId(generation_id=gen_id) if gen_id else None

        try:
            client = HumeClient(api_key=api_key)
            kwargs: dict = dict(
                utterances=[utterance],
                format=fmt,
                num_generations=1,
                version="2",
                include_timestamp_types=["word"],
            )
            if context is not None:
                kwargs["context"] = context
            result = client.tts.synthesize_json(**kwargs)

            generation = result.generations[0]
            audio_bytes = base64.b64decode(generation.audio)
            _write_bytes(output_path, audio_bytes)
            self._hume_generation_id = generation.generation_id

            # Extract word-level timings from snippets
            word_timings: list[dict] = []
            for snippet_group in (generation.snippets or []):
                snippets = snippet_group if isinstance(snippet_group, list) else [snippet_group]
                for snippet in snippets:
                    for ts in (snippet.timestamps or []):
                        word_timings.append({
                            "word": ts.text,
                            "start": ts.time.begin / 1000.0,
                            "end": ts.time.end / 1000.0,
                        })
            return word_timings
        except Exception as e:
            raise RuntimeError(f"Hume TTS SDK request failed: {e}") from e

    # ── Deepgram Aura TTS (REST) ───────────────────────────────────────────
    # https://developers.deepgram.com/docs/tts-rest
    # POST {base_url}/v1/speak?model=<voice>&encoding=mp3  · Authorization: Token <key>
    # The Deepgram "voice" IS the model query param (e.g. aura-2-thalia-en), so we
    # send the speaker's voice_id there, falling back to the model alias's model_id.

    # Deepgram's REST /v1/speak caps input at 2000 chars/request, so we chunk
    # at sentence boundaries (≤1800 for safety) and concatenate the MP3 segments.
    _DEEPGRAM_MAX_CHARS = 1800

    @staticmethod
    def _chunk_for_deepgram(text: str, max_chars: int = _DEEPGRAM_MAX_CHARS) -> list[str]:
        import re

        text = (text or "").strip()
        if len(text) <= max_chars:
            return [text] if text else []
        chunks: list[str] = []
        cur = ""
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            # a single sentence longer than the limit → hard-split on words
            if len(sentence) > max_chars:
                if cur:
                    chunks.append(cur.strip()); cur = ""
                word = ""
                for w in sentence.split(" "):
                    if len(word) + len(w) + 1 > max_chars:
                        chunks.append(word.strip()); word = ""
                    word += w + " "
                if word.strip():
                    cur = word
                continue
            if len(cur) + len(sentence) + 1 > max_chars:
                chunks.append(cur.strip()); cur = sentence + " "
            else:
                cur += sentence + " "
        if cur.strip():
            chunks.append(cur.strip())
        return [c for c in chunks if c]

    def _deepgram_params(self) -> dict:
        params: dict = {"model": self.voice_id or self.model.model_id, "encoding": "mp3"}
        if self.settings.get("bit_rate"):
            params["bit_rate"] = int(self.settings["bit_rate"])
        return params

    def _deepgram_request(self, text: str, api_key: str) -> bytes:
        """One Deepgram /v1/speak call for a single ≤2000-char chunk."""
        base_url = (self.provider.base_url or "https://api.deepgram.com").rstrip("/")
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{base_url}/v1/speak",
                params=self._deepgram_params(),
                headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
                json={"text": text},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Deepgram error {resp.status_code}: {resp.text[:300]}")
        return resp.content

    def _synthesize_deepgram(self, text: str, output_path: str, api_key: str) -> None:
        """Deepgram Aura TTS via REST — chunks ≤2000 chars, concatenates MP3."""
        try:
            audio = b"".join(self._deepgram_request(c, api_key) for c in self._chunk_for_deepgram(text))
            _write_bytes(output_path, audio)
        except Exception as e:
            raise RuntimeError(f"Deepgram TTS request failed: {e}") from e

    async def _async_deepgram(self, text: str, output_path: str, api_key: str) -> None:
        """Deepgram Aura TTS via REST (native async httpx) — chunked, MP3 concat."""
        base_url = (self.provider.base_url or "https://api.deepgram.com").rstrip("/")
        url = f"{base_url}/v1/speak"
        headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        out = bytearray()
        async with httpx.AsyncClient(timeout=120) as client:
            for chunk in self._chunk_for_deepgram(text):
                resp = await client.post(url, params=self._deepgram_params(), headers=headers, json={"text": chunk})
                if resp.status_code != 200:
                    raise RuntimeError(f"Deepgram error {resp.status_code}: {resp.text[:300]}")
                out += resp.content
        await asyncio.to_thread(_write_bytes, output_path, bytes(out))

    # -- Sarvam AI (Bulbul) --------------------------------------------------
    # Sarvam caps text per request, so we chunk at sentence boundaries (reusing
    # the deepgram chunker with a Sarvam-sized limit) and concatenate the WAV
    # segments at the PCM frame level. Response: JSON {"audios": ["<b64 wav>"]}.

    _SARVAM_MAX_CHARS = 450

    def _sarvam_body(self, text: str) -> dict:
        return {
            "text": text,
            "model": self.model.model_id,
            "speaker": self.voice_id,
            "target_language_code": self.settings.get("target_language_code", "en-IN"),
            "speech_sample_rate": int(self.settings.get("sample_rate", 22050)),
            "enable_preprocessing": True,
        }

    def _sarvam_max_chars(self) -> int:
        return int(self.settings.get("max_chars", self._SARVAM_MAX_CHARS))

    @staticmethod
    def _sarvam_decode_audios(payload: dict) -> list[bytes]:
        import base64

        audios = payload.get("audios") or []
        if not audios:
            raise RuntimeError("Sarvam response contained no audio")
        return [base64.b64decode(a) for a in audios]

    @staticmethod
    def _combine_wavs(wav_blobs: list[bytes], output_path: str) -> None:
        """Concatenate WAV blobs at the frame level into one file."""
        import io

        if len(wav_blobs) == 1:
            _write_bytes(output_path, wav_blobs[0])
            return
        params = None
        frames = bytearray()
        for blob in wav_blobs:
            with wave.open(io.BytesIO(blob), "rb") as w:
                if params is None:
                    params = w.getparams()
                frames += w.readframes(w.getnframes())
        with wave.open(output_path, "wb") as out:
            out.setnchannels(params.nchannels)
            out.setsampwidth(params.sampwidth)
            out.setframerate(params.framerate)
            out.writeframes(bytes(frames))

    def _sarvam_request(self, text: str, api_key: str) -> list[bytes]:
        """One Sarvam /text-to-speech call for a single chunk → WAV blobs."""
        base_url = (self.provider.base_url or "https://api.sarvam.ai").rstrip("/")
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{base_url}/text-to-speech",
                headers={
                    "api-subscription-key": api_key,
                    "Content-Type": "application/json",
                },
                json=self._sarvam_body(text),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Sarvam error {resp.status_code}: {resp.text[:300]}")
        return self._sarvam_decode_audios(resp.json())

    def _synthesize_sarvam(self, text: str, output_path: str, api_key: str) -> None:
        """Sarvam Bulbul TTS via REST — chunked, WAV frame-level concat."""
        try:
            blobs: list[bytes] = []
            for chunk in self._chunk_for_deepgram(text, self._sarvam_max_chars()):
                blobs.extend(self._sarvam_request(chunk, api_key))
            self._combine_wavs(blobs, output_path)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Sarvam TTS request failed: {e}") from e

    async def _async_sarvam(self, text: str, output_path: str, api_key: str) -> None:
        """Sarvam Bulbul TTS via REST (native async httpx) — chunked, WAV concat."""
        base_url = (self.provider.base_url or "https://api.sarvam.ai").rstrip("/")
        headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
        blobs: list[bytes] = []
        async with httpx.AsyncClient(timeout=120) as client:
            for chunk in self._chunk_for_deepgram(text, self._sarvam_max_chars()):
                resp = await client.post(
                    f"{base_url}/text-to-speech", headers=headers, json=self._sarvam_body(chunk),
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"Sarvam error {resp.status_code}: {resp.text[:300]}")
                blobs.extend(self._sarvam_decode_audios(resp.json()))
        await asyncio.to_thread(self._combine_wavs, blobs, output_path)

    async def _async_hume(self, text: str, output_path: str, api_key: str) -> None:
        """Hume AI Octave TTS via SDK (async via thread pool) — chains generation_id."""
        await asyncio.to_thread(self._synthesize_hume, text, output_path, api_key)

    async def _async_hume_with_timings(self, text: str, output_path: str, api_key: str) -> list[dict]:
        """Hume AI Octave TTS with word timestamps (async via thread pool)."""
        return await asyncio.to_thread(self._synthesize_hume_with_timings, text, output_path, api_key)


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
