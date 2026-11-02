"""
app/beat_sync.py — Beat-synchronised scene timing.

Provides two public functions:

    detect_beats(music_path)   → filtered onset timestamps (seconds)
    scene_durations(n, music)  → exactly n per-scene durations (seconds)

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────
MIN_ONSET_GAP: float = 2.5       # seconds — Requirement 3.2
DEFAULT_SCENE_DURATION: float = 4.5  # seconds — fallback / ideal cut point
MIN_SCENE_DURATION: float = 2.5  # seconds — Requirement 3.3
MAX_SCENE_DURATION: float = 7.0  # seconds — Requirement 3.3
LIBROSA_DELTA: float = 0.07      # onset sensitivity — Requirement 3.1

# Fallback grid step used when librosa is unavailable (120 BPM ≈ 0.5 s/beat;
# grouping 9 beats gives 4.5 s — we simply use DEFAULT_SCENE_DURATION directly).
_FALLBACK_GRID_STEP: float = DEFAULT_SCENE_DURATION
_FALLBACK_GRID_MAX: int = 50


# ── public API ─────────────────────────────────────────────────────────────

def detect_beats(music_path: Path) -> list[float]:
    """Return onset timestamps (seconds) from *music_path*, filtered so that
    no two adjacent entries are less than MIN_ONSET_GAP (2.5 s) apart.

    Falls back to a 120 BPM grid (steps of 4.5 s, up to 50 entries) if
    librosa is not installed or raises any exception.

    The returned list always begins with 0.0 (initialised before filtering) so
    that there is always at least one anchor point.

    Requirement 3.1 — uses librosa.onset.onset_detect with delta=0.07
    Requirement 3.2 — adjacent onsets < 2.5 s apart are discarded
    Requirement 3.4 — fallback when librosa unavailable / raises
    """
    try:
        import librosa  # optional dependency

        y, sr = librosa.load(str(music_path), sr=None, mono=True)
        raw_frames = librosa.onset.onset_detect(
            y=y,
            sr=sr,
            delta=LIBROSA_DELTA,
            units="frames",
        )
        raw_times: list[float] = librosa.frames_to_time(raw_frames, sr=sr).tolist()
    except Exception as exc:  # includes ImportError, FileNotFoundError, …
        logger.warning("beat_sync: librosa failed (%s). Using fallback grid.", exc)
        return _fallback_grid()

    return _filter_min_gap([0.0] + raw_times)


# ── internal helpers ───────────────────────────────────────────────────────

def _filter_min_gap(times: list[float]) -> list[float]:
    """Keep only onsets that are at least MIN_ONSET_GAP seconds after the
    previous kept onset.  Always retains the first element.
    """
    if not times:
        return [0.0]

    filtered: list[float] = [times[0]]
    for t in times[1:]:
        if t - filtered[-1] >= MIN_ONSET_GAP:
            filtered.append(t)

    return filtered


def _fallback_grid() -> list[float]:
    """Return a fixed 120 BPM-inspired grid: [0.0, 4.5, 9.0, …] up to
    _FALLBACK_GRID_MAX entries (Requirement 3.4).
    """
    return [round(i * _FALLBACK_GRID_STEP, 3) for i in range(_FALLBACK_GRID_MAX)]


def scene_durations(n_scenes: int, music_path: Path | None) -> list[float]:
    """Return exactly *n_scenes* scene durations (floats, rounded to 3 dp).

    When *music_path* is provided, beat onsets drive the cut points.  Each
    scene gets the onset closest to ``cursor + DEFAULT_SCENE_DURATION``,
    constrained to [MIN_SCENE_DURATION, MAX_SCENE_DURATION].

    When *music_path* is None (or detect_beats raises), every scene gets the
    fixed DEFAULT_SCENE_DURATION fallback.

    Requirement 3.3 — durations constrained to [2.5, 7.0]
    Requirement 3.4 — falls back to 4.5 s grid without music
    Requirement 3.5 — returns exactly n_scenes entries, rounded to 3 dp
    """
    if n_scenes <= 0:
        return []

    if music_path is None:
        return [DEFAULT_SCENE_DURATION] * n_scenes

    try:
        beat_times = detect_beats(music_path)
    except Exception as exc:
        logger.warning("beat_sync: detect_beats failed (%s). Using fixed grid.", exc)
        return [DEFAULT_SCENE_DURATION] * n_scenes

    return _scene_durations_from_beats(n_scenes, beat_times)


def _scene_durations_from_beats(n_scenes: int, beat_times: list[float]) -> list[float]:
    """Core duration-assignment algorithm, separated for testability.

    For each scene, finds the beat onset closest to ``cursor + 4.5 s`` from
    the candidates in [cursor + 2.5, cursor + 7.0].  Falls back to
    DEFAULT_SCENE_DURATION when no candidate exists in range.
    """
    durations: list[float] = []
    cursor: float = 0.0

    for _ in range(n_scenes):
        lo = cursor + MIN_SCENE_DURATION
        hi = cursor + MAX_SCENE_DURATION
        ideal = cursor + DEFAULT_SCENE_DURATION

        candidates = [t for t in beat_times if lo <= t <= hi]

        if candidates:
            best = min(candidates, key=lambda t: abs(t - ideal))
            raw_dur = best - cursor
        else:
            raw_dur = DEFAULT_SCENE_DURATION

        # Clamp defensively (should already be in range, but be safe).
        raw_dur = max(MIN_SCENE_DURATION, min(MAX_SCENE_DURATION, raw_dur))
        dur = round(raw_dur, 3)
        durations.append(dur)
        cursor += dur

    return durations
