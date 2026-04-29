"""
scripts/mix_episode.py
Mix an episode MP3 with intro/outro/background music.
- Intro: full volume for 12s, then fade under speech
- Background: music loops at low volume under full episode
- Outro: music fades back up, plays 12s at full volume, then fades out
Usage: python scripts/mix_episode.py <episode_mp3> <music_mp3>
"""

import sys
from pathlib import Path
from pydub import AudioSegment
from pydub.effects import normalize

INTRO_DURATION_MS = 12000       # 12s full volume intro
OUTRO_DURATION_MS = 12000       # 12s full volume outro
FADE_DURATION_MS = 3000         # 3s crossfade
BG_VOLUME_DB = -18              # how much to duck music under speech


def loop_audio(audio: AudioSegment, target_ms: int) -> AudioSegment:
    """Loop audio segment to fill target duration."""
    loops = (target_ms // len(audio)) + 2
    return (audio * loops)[:target_ms]


def mix_episode(episode_path: str, music_path: str) -> str:
    print(f"Loading episode: {episode_path}")
    episode = AudioSegment.from_mp3(episode_path)

    print(f"Loading music: {music_path}")
    music_raw = AudioSegment.from_mp3(music_path)

    ep_len = len(episode)
    print(f"Episode duration: {ep_len / 1000:.1f}s")

    # --- Build music bed ---
    # Full track for intro
    intro_music = music_raw[:INTRO_DURATION_MS].fade_in(500)

    # Fade down to bg level after intro
    intro_to_bg_fade = music_raw[INTRO_DURATION_MS:INTRO_DURATION_MS + FADE_DURATION_MS]
    intro_to_bg_fade = intro_to_bg_fade.fade(
        to_gain=BG_VOLUME_DB,
        start=0,
        end=FADE_DURATION_MS
    )

    # Background loop under episode body
    bg_start = INTRO_DURATION_MS + FADE_DURATION_MS
    bg_duration = ep_len - FADE_DURATION_MS  # leave room for outro fade-up
    bg_music = loop_audio(music_raw, bg_duration + OUTRO_DURATION_MS + FADE_DURATION_MS * 2)
    bg_music = bg_music + BG_VOLUME_DB  # duck

    # Fade music back up before outro
    fade_up_music = bg_music[bg_duration:bg_duration + FADE_DURATION_MS].fade(
        to_gain=-BG_VOLUME_DB,  # back to 0 relative
        start=0,
        end=FADE_DURATION_MS
    )

    # Outro: full volume, then fade out
    outro_start_in_music = (bg_duration + FADE_DURATION_MS) % len(music_raw)
    outro_music = loop_audio(music_raw, OUTRO_DURATION_MS).fade_out(2000)

    # --- Assemble music track ---
    # Full timeline: intro(12s) + fade_down(3s) + bg(ep_len - 3s) + fade_up(3s) + outro(12s)
    total_music_len = INTRO_DURATION_MS + ep_len + OUTRO_DURATION_MS + FADE_DURATION_MS

    music_timeline = AudioSegment.silent(duration=total_music_len)

    # Place intro
    music_timeline = music_timeline.overlay(intro_music, position=0)

    # Place fade down
    music_timeline = music_timeline.overlay(intro_to_bg_fade, position=INTRO_DURATION_MS)

    # Place background
    music_timeline = music_timeline.overlay(bg_music[:ep_len], position=INTRO_DURATION_MS + FADE_DURATION_MS)

    # Place fade up
    fade_up_pos = INTRO_DURATION_MS + FADE_DURATION_MS + ep_len - FADE_DURATION_MS
    music_timeline = music_timeline.overlay(fade_up_music, position=fade_up_pos)

    # Place outro
    outro_pos = INTRO_DURATION_MS + ep_len
    music_timeline = music_timeline.overlay(outro_music, position=outro_pos)

    # --- Place episode speech ---
    speech_timeline = AudioSegment.silent(duration=total_music_len)
    speech_timeline = speech_timeline.overlay(episode, position=INTRO_DURATION_MS)

    # --- Mix ---
    print("Mixing...")
    final = music_timeline.overlay(speech_timeline)
    final = normalize(final)

    # --- Export ---
    episode_stem = Path(episode_path).stem
    output_path = str(Path(episode_path).parent / f"{episode_stem}_mixed.mp3")
    print(f"Exporting: {output_path}")
    final.export(output_path, format="mp3", bitrate="192k")

    print(f"\nDone. Play: open \"{output_path}\"")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/mix_episode.py <episode_mp3> <music_mp3>")
        sys.exit(1)
    mix_episode(sys.argv[1], sys.argv[2])
