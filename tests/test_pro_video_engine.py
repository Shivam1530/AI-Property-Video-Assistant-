"""Property-based and unit tests for the Pro Video Engine (Properties P1–P13).

Each test targets one correctness property from the design document:
    P1  caption list length invariant          (Requirement 12.2)
    P2  caption content traceability           (Requirement 12.3)
    P3  caption determinism                    (Requirement 12.1)
    P4  fallback caption contains feature text (Requirement 12.5)
    P5  xfade offset arithmetic continuity     (Requirement 5.5)
    P6  xfade transition cycle correctness     (Requirement 5.2)
    P7  beat sync duration bounds              (Requirements 3.3, 3.5)
    P8  beat sync fallback grid                (Requirements 3.4, 3.5)
    P9  beat onset minimum gap filter          (Requirement 3.2)
    P10 zoom pattern cycling                   (Requirement 2.3)
    P11 quality check pass condition           (Requirements 8.1, 8.3)
    P12 quality check issue detection          (Requirements 8.1, 8.2)
    P13 caption escaping safety                (Requirement 6.6)

Run with: python -m pytest tests/ -v
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from hypothesis import given, settings, strategies as st
from PIL import Image

from app.beat_sync import (
    MIN_ONSET_GAP,
    _fallback_grid,
    _filter_min_gap,
    _scene_durations_from_beats,
    detect_beats,
    scene_durations,
)
from app.quality_check import check_image
from app.video import (
    _FEATURE_CAPTIONS,
    _TRANSITIONS,
    _ZOOM_PATTERNS,
    _escape_drawtext,
    _xfade_offsets,
    _xfade_transitions,
    _zoom_pattern,
    TRANSITION_DURATION,
    build_captions,
)

listing_strategy = st.fixed_dictionaries({
    "property_name": st.text(min_size=2, max_size=30),
    "location": st.text(min_size=2, max_size=30),
    "price": st.text(min_size=1, max_size=20),
    "property_type": st.text(min_size=2, max_size=20),
    "features": st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=7),
    "cta": st.text(min_size=2, max_size=40),
})


def _png_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array.astype(np.uint8), mode="L").save(buffer, format="PNG")
    return buffer.getvalue()


def _noise_image(width: int, height: int, seed: int, low: int = 0, high: int = 256) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(low, high, size=(height, width), dtype=np.int64)


# ─── P1: caption list length invariant ───────────────────────────────────────

@given(listing_strategy)
def test_p1_caption_list_length_invariant(listing):
    assert len(build_captions(listing)) == len(listing["features"]) + 3


# ─── P2: caption content traceability ────────────────────────────────────────

def _caption_traces(caption: str, listing: dict) -> bool:
    cap = caption.lower()
    scalars = [
        listing["property_name"], listing["location"], listing["price"],
        listing["property_type"], listing["cta"],
    ]
    if any(term.lower() in cap for term in scalars):
        return True
    for feature in listing["features"]:
        if feature.lower() in cap:
            return True
        if any(token.lower() in cap for token in feature.split() if len(token) >= 2):
            return True
    return False


@given(listing_strategy)
def test_p2_caption_content_traceability(listing):
    for caption in build_captions(listing):
        assert _caption_traces(caption, listing), f"untraceable caption: {caption!r}"


# ─── P3: caption determinism ─────────────────────────────────────────────────

@given(listing_strategy)
def test_p3_caption_determinism(listing):
    assert build_captions(listing) == build_captions(listing)


# ─── P4: fallback caption contains feature text ──────────────────────────────

@given(st.text(min_size=1, max_size=40).filter(lambda s: s.strip().lower() not in _FEATURE_CAPTIONS))
def test_p4_fallback_caption_contains_feature_text(feature):
    listing = {
        "property_name": "Test Heights", "location": "Test City", "price": "₹1 Cr",
        "property_type": "2-BHK", "features": [feature], "cta": "Call today",
    }
    captions = build_captions(listing)
    feature_caption = captions[2]
    assert feature_caption == f"Experience {feature} — crafted for modern living."
    assert feature in feature_caption


# ─── P5: xfade offset arithmetic continuity ──────────────────────────────────

@given(st.lists(st.floats(min_value=2.5, max_value=7.0, allow_nan=False), min_size=2, max_size=9))
def test_p5_xfade_offset_arithmetic_continuity(durations):
    offsets = _xfade_offsets(durations)
    assert len(offsets) == len(durations) - 1
    for i, offset in enumerate(offsets):
        expected = sum(durations[: i + 1]) - (i + 1) * TRANSITION_DURATION
        assert offset == round(expected, 3)
    for previous, current in zip(offsets, offsets[1:]):
        assert current > previous


# ─── P6: xfade transition cycle correctness ──────────────────────────────────

@given(st.lists(st.floats(min_value=2.5, max_value=7.0, allow_nan=False), min_size=2, max_size=9))
def test_p6_xfade_transition_cycle_correctness(durations):
    transitions = _xfade_transitions(len(durations))
    assert len(transitions) == len(durations) - 1
    for i, transition in enumerate(transitions):
        assert transition == _TRANSITIONS[i % len(_TRANSITIONS)]


# ─── P7: beat sync duration bounds ───────────────────────────────────────────

@given(
    st.integers(min_value=1, max_value=9),
    st.lists(st.floats(min_value=0.0, max_value=300.0, allow_nan=False), min_size=1, max_size=100),
)
def test_p7_beat_sync_duration_bounds(n_scenes, beat_times):
    durations = _scene_durations_from_beats(n_scenes, sorted(beat_times))
    assert len(durations) == n_scenes
    for duration in durations:
        assert 2.5 <= duration <= 7.0


# ─── P8: beat sync fallback grid ─────────────────────────────────────────────

@given(st.integers(min_value=1, max_value=9))
def test_p8_beat_sync_fallback_grid(n_scenes):
    assert scene_durations(n_scenes, None) == [4.5] * n_scenes


# ─── P9: beat onset minimum gap filter ───────────────────────────────────────

class _StubOnset:
    def __init__(self, parent):
        self._parent = parent

    def onset_detect(self, y=None, sr=None, delta=None, units=None, **kwargs):
        return np.arange(len(self._parent._times))


class _StubLibrosa:
    """Mimics the surface of librosa used by beat_sync.detect_beats."""

    def __init__(self, times):
        self._times = np.asarray(sorted(times), dtype=float)
        self.onset = _StubOnset(self)

    def load(self, path, sr=None, mono=True):
        return np.zeros(16, dtype=np.float32), 22050

    def frames_to_time(self, frames, sr=None):
        return self._times


@given(st.lists(st.floats(min_value=0.0, max_value=300.0, allow_nan=False), min_size=1, max_size=100))
def test_p9_beat_onset_minimum_gap_filter(raw_times):
    stub = _StubLibrosa(raw_times)
    with patch.dict(sys.modules, {"librosa": stub}):
        result = detect_beats(Path("fake-music.wav"))
    for previous, current in zip(result, result[1:]):
        assert current - previous >= MIN_ONSET_GAP


@given(st.lists(st.floats(min_value=0.0, max_value=300.0, allow_nan=False), min_size=1, max_size=100))
def test_p9_filter_min_gap_direct(raw_times):
    result = _filter_min_gap(sorted(raw_times))
    for previous, current in zip(result, result[1:]):
        assert current - previous >= MIN_ONSET_GAP


def test_p9_fallback_grid_shape():
    grid = _fallback_grid()
    assert grid[0] == 0.0
    assert all(b - a >= MIN_ONSET_GAP for a, b in zip(grid, grid[1:]))


# ─── P10: zoom pattern cycling ───────────────────────────────────────────────

@given(st.integers(min_value=0, max_value=1000))
def test_p10_zoom_pattern_cycling(scene_index):
    assert _zoom_pattern(scene_index) is _ZOOM_PATTERNS[scene_index % len(_ZOOM_PATTERNS)]
    assert _zoom_pattern(scene_index) is _zoom_pattern(scene_index + 4)


# ─── P11: quality check pass condition ───────────────────────────────────────

@settings(max_examples=25)
@given(
    st.integers(min_value=800, max_value=1100),
    st.integers(min_value=800, max_value=1100),
    st.integers(min_value=0, max_value=10_000),
)
def test_p11_quality_check_pass_condition(width, height, seed):
    array = _noise_image(width, height, seed)
    report = check_image(_png_bytes(array), "good.png")
    assert report["issues"] == []
    assert report["resolution"] == f"{width}×{height}"


# ─── P12: quality check issue detection ──────────────────────────────────────

@settings(max_examples=25)
@given(
    st.integers(min_value=100, max_value=799),
    st.integers(min_value=100, max_value=799),
    st.integers(min_value=0, max_value=10_000),
)
def test_p12_low_resolution_flagged(width, height, seed):
    array = _noise_image(width, height, seed)
    report = check_image(_png_bytes(array), "small.png")
    assert len(report["issues"]) == 1
    assert "resolution" in report["issues"][0].lower()


@settings(max_examples=15)
@given(st.integers(min_value=0, max_value=20))
def test_p12_dark_image_flagged(fill):
    array = np.full((900, 900), fill)
    report = check_image(_png_bytes(array), "dark.png")
    assert any("dark" in issue.lower() for issue in report["issues"])


@settings(max_examples=15)
@given(st.integers(min_value=240, max_value=255))
def test_p12_overexposed_image_flagged(fill):
    array = np.full((900, 900), fill)
    report = check_image(_png_bytes(array), "bright.png")
    assert any("overexposed" in issue.lower() for issue in report["issues"])


def test_p12_blurry_image_flagged():
    # Smooth horizontal ramp: correct size and exposure, near-zero Laplacian variance.
    ramp = np.linspace(60, 200, 900, dtype=np.float32)
    array = np.tile(ramp, (900, 1))
    report = check_image(_png_bytes(array), "blurry.png")
    assert len(report["issues"]) == 1
    assert "blurry" in report["issues"][0].lower()


# ─── P13: caption escaping safety ────────────────────────────────────────────

@given(st.text(alphabet=st.characters(categories=("L", "N", "P", "Z")), min_size=0, max_size=200))
def test_p13_caption_escaping_safety(text):
    escaped = _escape_drawtext(text)
    assert re.search(r"(?<!\\)'", escaped) is None
    assert re.search(r"(?<!\\):", escaped) is None
