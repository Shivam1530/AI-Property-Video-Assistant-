"""
app/editor.py — master render orchestrator.

Selects the rendering pipeline per scene (SVD cinema clips when Cinema Mode is
enabled and inference succeeds, Ken Burns otherwise), plans the beat-synced
scene timeline, and chains every processing step into a single entry point:

    render_video(listing, images, output, music, narration, cinema_mode)

Requirements: 10.1–10.5, 1.6, 4.5, 4.6, 5.1, 5.5, 13.1, 13.2, 13.5
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import beat_sync, svd_engine
from .video import (
    FPS,
    TRANSITION_DURATION,
    _build_xfade_concat,
    _end_card,
    _narrate_one,
    _overlay_caption_on_clip,
    _pad_audio_to,
    _render_scene_ffmpeg_fallback,
    _render_scene_still_motion,
    _run,
    _verify,
    build_captions,
)

logger = logging.getLogger(__name__)


@dataclass
class SceneSpec:
    """One planned scene: source image, target duration, caption, position."""

    image: Path
    duration: float
    caption: str
    index: int


def _normalise_svd_clip(source: Path, output: Path, duration: float) -> None:
    """Bring an SVD clip (25 frames @ ~7 fps) up to the project frame rate and
    the scene duration by looping/trimming, so xfade inputs stay uniform."""
    _run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(source),
        "-t", str(duration), "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        str(output),
    ])


def _build_narration_track(padded: list[Path], durations: list[float], work: Path) -> Path:
    """Lay the per-scene narration WAVs onto the final (xfade-overlapped) timeline.

    On the assembled video, scene ``i`` starts at
    ``sum(durations[:i]) − i × TRANSITION_DURATION`` and runs until the next
    transition, so its narration slot is ``duration − TRANSITION_DURATION``
    (the full duration for the last scene). Concatenating those slots yields a
    track exactly as long as the assembled video.
    """
    n = len(padded)
    output = work / "narration-track.wav"
    if n == 1:
        shutil.copy2(str(padded[0]), str(output))
        return output

    inputs: list[str] = []
    for p in padded:
        inputs += ["-i", str(p)]

    parts: list[str] = []
    labels: list[str] = []
    for i in range(n):
        slot = durations[i] if i == n - 1 else durations[i] - TRANSITION_DURATION
        parts.append(f"[{i}:a]atrim=0:{slot:.3f},apad=whole_dur={slot:.3f}[a{i}]")
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"concat=n={n}:v=0:a=1[aout]")

    _run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[aout]", "-c:a", "pcm_s16le",
        str(output),
    ])
    return output


def render_video(
    listing: dict,
    images: list[Path],
    output: Path,
    music: Path | None,
    narration: bool,
    cinema_mode: bool = False,
) -> None:
    """Render the finished property video to *output* (Requirement 10.x)."""
    work = output.parent / f"work-{output.stem}"
    work.mkdir(exist_ok=True)

    captions = build_captions(listing)
    content_captions = captions[:-1]
    usable = images[:min(len(images), len(content_captions))]
    n_content = len(usable)
    n_scenes = n_content + 1  # +1 for end card

    all_durations = beat_sync.scene_durations(n_scenes, music)

    # Build the scene plan: one spec per content image + the end card.
    end_jpg = work / "end-card.jpg"
    _end_card(listing, end_jpg)
    specs = [
        SceneSpec(image=img, duration=all_durations[i], caption=content_captions[i], index=i)
        for i, img in enumerate(usable)
    ]
    specs.append(
        SceneSpec(image=end_jpg, duration=all_durations[n_content], caption=captions[-1], index=n_content)
    )

    scenes: list[Path] = []
    narration_wavs: list[Path] = []
    try:
        for spec in specs:
            is_end = spec.index == n_content
            clip = work / ("scene-end.mp4" if is_end else f"scene-{spec.index:02}.mp4")

            if is_end:
                # End card: gentle zoom, text is already baked into the JPEG.
                _render_scene_ffmpeg_fallback(spec.image, spec.duration, clip, {
                    "zoom_start": 1.0, "zoom_end": 1.06,
                    "focus": (0.5, 0.5), "focus_end": (0.5, 0.5),
                })
            else:
                rendered = False
                if cinema_mode:
                    svd_tmp = work / f"svd-{spec.index:02}.mp4"
                    rendered = svd_engine.generate_clip(spec.image, svd_tmp)
                    if rendered:
                        _normalise_svd_clip(svd_tmp, clip, spec.duration)
                        svd_tmp.unlink(missing_ok=True)
                    else:
                        logger.warning("SVD clip failed for scene %d; falling back to Ken Burns", spec.index)
                if not rendered:
                    _render_scene_still_motion(
                        image=spec.image,
                        duration=spec.duration,
                        output=clip,
                        property_name=listing["property_name"],
                        scene_index=spec.index,
                        caption=spec.caption,
                        work=work,
                    )
            scenes.append(clip)

            if narration:
                raw = work / f"voice-{spec.index:02}.wav"
                padded = work / f"voice-{spec.index:02}-pad.wav"
                _narrate_one(spec.caption, raw)
                _pad_audio_to(raw, spec.duration, padded)
                narration_wavs.append(padded)

        # Assemble video-only timeline with xfade transitions.
        assembled = work / "assembled.mp4"
        _build_xfade_concat(scenes, all_durations, assembled)

        narr_track = _build_narration_track(narration_wavs, all_durations, work) if narration else None

        # Final audio mux. amix divides by the number of inputs, so the
        # narration is pre-boosted ×2 and music ×0.20 to land at 1.0 / 0.10.
        if narr_track is not None and music is not None:
            _run(["ffmpeg", "-y",
                  "-i", str(assembled), "-i", str(narr_track),
                  "-stream_loop", "-1", "-i", str(music),
                  "-filter_complex",
                  "[1:a]volume=2.0[n];[2:a]volume=0.20[m];[n][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
                  "-map", "0:v", "-map", "[a]",
                  "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", str(output)])
        elif narr_track is not None:
            _run(["ffmpeg", "-y",
                  "-i", str(assembled), "-i", str(narr_track),
                  "-map", "0:v", "-map", "1:a",
                  "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", "-shortest", str(output)])
        elif music is not None:
            _run(["ffmpeg", "-y", "-i", str(assembled),
                  "-stream_loop", "-1", "-i", str(music),
                  "-filter:a", "volume=0.18",
                  "-map", "0:v", "-map", "1:a",
                  "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", "-shortest", str(output)])
        else:
            shutil.copy2(str(assembled), str(output))

        _verify(output)
    finally:
        for item in sorted(work.glob("*"), reverse=True):
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        work.rmdir()
