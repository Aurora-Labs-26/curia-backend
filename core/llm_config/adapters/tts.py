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
        if self.provider.type == "edge_tts":
            return "mp3"
        return "wav"

    # -- public ------------------------------------------------------------

    def synthesize(self, text: str, output_path: str) -> None:
        # edge_tts needs no API key — bypass the stub check entirely
        if self.provider.type == "edge_tts":
            return self._synthesize_edge_tts(text, output_path)

        api_key = os.getenv(self.provider.api_key_env)
        if not api_key:
            if not self._warned_stub:
                logger.warning(
                    f"{self.provider.api_key_env} not set — TTS STUB active "
                    f"(silent WAV) for provider={self.provider.type}."
                )
                self._warned_stub = True
            _write_silent_wav(output_path, duration_seconds=1.0)
            return

        if not text or not text.strip():
            _write_silent_wav(output_path, duration_seconds=0.3)
            return

        # Dispatch by provider type — each method writes a WAV at output_path.
        if self.provider.type == "elevenlabs":
            return self._synthesize_elevenlabs(text, output_path, api_key)
        if self.provider.type == "smallest":
            return self._synthesize_smallest(text, output_path, api_key)
        if self.provider.type == "google_tts":
            return self._synthesize_google(text, output_path, api_key)
        if self.provider.type == "xai":
            return self._synthesize_xai(text, output_path, api_key)
        if self.provider.type == "edge_tts":
            return self._synthesize_edge_tts(text, output_path)

        raise ValueError(f"TTS provider type '{self.provider.type}' not implemented")

    # -- providers ---------------------------------------------------------

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

    def _synthesize_edge_tts(self, text: str, output_path: str) -> None:
        """Microsoft Edge TTS — free, no API key required. Outputs MP3 directly."""
        import concurrent.futures
        import edge_tts
        import shutil

        voice = self.voice_id or "en-US-GuyNeural"
        mp3_path = output_path.replace(".wav", ".mp3") if output_path.endswith(".wav") else output_path

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(mp3_path)

        def _run_in_thread():
            # Run in a fresh thread with its own event loop to avoid
            # "cannot be called from a running event loop" in async workers.
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_in_thread)
                future.result(timeout=60)
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
