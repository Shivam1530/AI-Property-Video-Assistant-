# Reality Assistant Drive — Pipeline Overhaul Changes

## Pro Video Engine — completion round (2026-10-30)

Completed the `.kiro/specs/pro-video-engine` implementation plan: the pipeline is now
modular (`beat_sync`, `quality_check`, `svd_engine`, `editor`), fully tested
(20 tests incl. 13 Hypothesis property tests), and validated end-to-end with real
FFmpeg renders (music+narration and music-only, 1080×1920).

### `app/editor.py` — NEW master orchestrator
- `SceneSpec` dataclass + `render_video()` entry point; routes each scene to
  `svd_engine.generate_clip` when Cinema Mode is on and SVD succeeds (clips are
  re-timed to 30 fps and the scene duration), otherwise to Ken Burns.
- **Narration audio fix**: the old flow muxed narration per scene and then dropped
  every audio stream in the xfade concat (render failed with music, narration
  silently lost without). Narration WAVs are now laid onto the final
  transition-aware timeline by `editor._build_narration_track` and mixed with
  music after assembly. Narration+music and narration-only both verified working.

### `app/video.py`
- Removed `_detect_beats` / `_scene_durations` / `render_video` (superseded by
  `beat_sync.py` / `editor.py`); helpers remain in place.
- Extracted testable helpers: `_ZOOM_PATTERNS` + `_zoom_pattern()`, `_TRANSITIONS`
  + `_xfade_transitions()`, `_xfade_offsets()`, `_escape_drawtext()` /
  `_escape_filter_path()`.
- **drawtext escaping fix**: dynamic text (caption *and* property-name watermark)
  is now passed via `textfile=` — on FFmpeg 9 inline-quoted `\'` silently drops
  apostrophes and the old `'\''` dance renders nothing (verified empirically);
  `textfile` content is raw and renders correctly ("O'Brien Tower" verified).
- Ken Burns fallback now catches any `still_motion` failure (its PyPI version is
  0.1.2, not ≥0.4.0 — `requirements.txt` pin relaxed accordingly) with a warning log.
- "security" and "lift" feature captions reworded so every mapped caption contains
  its feature keyword (enforces caption-traceability property P2).

### `app/quality_check.py`
- Added `check_images()` batch wrapper; a corrupt/unsupported upload produces a
  "could not be read" report instead of raising a 500.

### `app/main.py`
- `render_video` now imported from `editor`; `/api/analyse` refactored onto
  `quality_check.check_images` (inline duplicate removed; corrupt uploads no longer
  crash the endpoint); `/api/svd-status` `model_loaded` now correctly checks
  `_svd_pipe is not None` (was always True when CUDA was present); removed unused
  `subprocess` import.

### `web/` — Cinema Mode + progress feedback
- `index.html`: Cinema Mode checkbox (disabled until the GPU check passes, with
  tooltip) and a hidden `#render-progress` indicator placed as a sibling of
  `#preview-content` so `display()` cannot destroy it.
- `app.js`: `checkSvdStatus()` on load enables Cinema Mode when `/api/svd-status`
  reports ≥6 GB VRAM; progress spinner shown during renders; `cinema_mode` rides
  the existing `FormData(form)`.
- `styles.css`: disabled checkbox state, `.render-progress` / `.render-spinner`
  keyframes, `has-issues`/`ok` accent variants for quality items.

### Tests & environment
- New `tests/test_pro_video_engine.py` — properties P1–P13 from the design doc
  (caption length/traceability/determinism, xfade offsets & transitions, beat-sync
  bounds/fallback/gap filter, zoom cycling, quality pass/fail, escaping safety).
  Full suite: **20 passed** (`python -m pytest tests/ -v`).
- Project now has a working `.venv` (the global Python env has a FastAPI/Starlette
  conflict and cannot run the app or tests). `pyttsx3` installed for narration.

## Previous round

## Summary

Complete professional video pipeline overhaul addressing alignment, audio/caption sync,
professional Ken Burns animation, beat-synchronised scene durations, xfade transitions,
luxury end card design, async render fix, and image quality analysis.

## Changes

### `requirements.txt`
- Added: `still-motion>=0.4.0`, `librosa>=0.10.0`, `soundfile>=0.12.0`, `numpy>=1.24.0`, `moviepy>=2.0.0`
- Run `pip install -r requirements.txt` to install new dependencies.

### `app/video.py` — Complete rewrite
- **Beat detection** (`_detect_beats`): uses `librosa.onset.onset_detect` to find music onset timestamps. Falls back to a steady 4.5 s grid if librosa is unavailable.
- **Beat-sync durations** (`_scene_durations`): each scene snaps to the nearest beat boundary (min 2.5 s, max 7.0 s) so cuts land on the music.
- **Ken Burns** (`_render_scene_still_motion`): tries the `still-motion` library for smooth, judder-free zoompan; falls back to a 4× upscale FFmpeg zoompan pipeline if not installed.
- **Caption overlay** (`_overlay_caption_on_clip`): two-layer gradient scrim + large left-aligned caption with drop shadow; property name watermark top-left. Applied after encoding so still-motion output is also captioned.
- **TTS fix** (`_narrate_one`): writes to `%TEMP%\\rad_voice_tmp.wav` (no spaces) and copies to destination, bypassing the pyttsx3 Windows spaces-in-path silent-failure bug.
- **xfade transitions** (`_build_xfade_concat`): replaces the simple concat demuxer with a single FFmpeg `filter_complex` xfade chain. Transitions vary per scene pair (fade, smoothleft, fadeblack, circleopen).
- **Luxury end card** (`_end_card`): dark teal canvas (#042f2e), decorative rules, large price hero, property type chip, rounded CTA button, tagline.
- **`render_video`**: orchestrates the new pipeline; properly passes per-scene durations to both the encoder and the xfade concat step.

### `app/main.py`
- **Async fix**: `render_video` is now dispatched via `loop.run_in_executor(None, partial(...))` so it no longer blocks the FastAPI event loop during multi-minute renders.
- **`POST /api/analyse`**: new endpoint that accepts listing JSON + optional image uploads. Returns per-image quality warnings (resolution, brightness) and overall suggestions alongside the caption plan.
- Added `from PIL import Image` import.

### `web/app.js`
- **Preview button** now also calls `/api/analyse` when images are selected, merging captions + image quality into a single `display()` call.
- **`display()`** renders an `IMAGE QUALITY CHECK` section showing per-file pass/fail with issue details, and a suggestions list.
- Video player uses `playsinline` attribute for better mobile behaviour.

### `web/styles.css`
- Added `.quality-report`, `.quality-item`, `.quality-name`, `.quality-issue`, `.quality-ok` styles for the image quality report panel.
- Added `.suggestion-list` styles.
- Added `.video-wrap` container.
