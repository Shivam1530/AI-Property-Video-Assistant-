"""
app/quality_check.py — Per-image quality analysis module.

Evaluates uploaded property images for resolution, brightness, and blur
before rendering, allowing agents to surface warnings to the user.
"""

from __future__ import annotations

import io
from typing import TypedDict

import numpy as np
import scipy.ndimage
from PIL import Image


class ImageReport(TypedDict):
    """Structured quality report for a single image.

    Fields:
        filename   — original upload filename.
        resolution — formatted as "W×H" (e.g. "1920×1080").
        issues     — human-readable warning strings; empty list means all checks passed.
    """

    filename: str
    resolution: str
    issues: list[str]


def check_image(data: bytes, filename: str) -> ImageReport:
    """Analyse a single image for quality issues.

    Checks performed:
    - Resolution below 800×800 px
    - Mean luminance below 35 (very dark / underexposed)
    - Mean luminance above 230 (overexposed)
    - Laplacian variance below 80 (blurry)

    Args:
        data:     Raw image bytes (any Pillow-supported format).
        filename: Original upload filename shown in the report.

    Returns:
        ImageReport with ``resolution`` set and ``issues`` listing any
        warnings found (empty list when all checks pass).
    """
    img = Image.open(io.BytesIO(data))
    width, height = img.size
    resolution = f"{width}\u00d7{height}"  # "W×H"

    issues: list[str] = []

    # ── Resolution check ────────────────────────────────────────────────────
    if width < 800 or height < 800:
        issues.append(
            f"Resolution {resolution} is below the recommended 800×800 px minimum."
        )

    # ── Luminance checks ─────────────────────────────────────────────────────
    grey_arr = np.array(img.convert("L"), dtype=np.float32)
    mean_lum = float(grey_arr.mean())

    if mean_lum < 35:
        issues.append(
            f"Image appears very dark (mean luminance {mean_lum:.1f} < 35)."
        )
    elif mean_lum > 230:
        issues.append(
            f"Image appears overexposed (mean luminance {mean_lum:.1f} > 230)."
        )

    # ── Blur check (Laplacian variance) ──────────────────────────────────────
    lap_kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    laplacian = scipy.ndimage.convolve(grey_arr, lap_kernel)
    blur_score = float(laplacian.var())

    if blur_score < 80:
        issues.append(
            f"Image may be blurry (Laplacian variance {blur_score:.1f} < 80)."
        )

    return ImageReport(filename=filename, resolution=resolution, issues=issues)


def check_images(files: list[tuple[bytes, str]]) -> list[ImageReport]:
    """Analyse a batch of uploads.

    A file that Pillow cannot open (corrupt or unsupported) produces a report
    with a single "could not be read" issue instead of raising, so one bad
    upload never blocks the whole analysis.
    """
    reports: list[ImageReport] = []
    for data, filename in files:
        try:
            reports.append(check_image(data, filename))
        except Exception:
            reports.append(
                ImageReport(
                    filename=filename,
                    resolution="unknown",
                    issues=["Image could not be read — file may be corrupt or unsupported."],
                )
            )
    return reports
