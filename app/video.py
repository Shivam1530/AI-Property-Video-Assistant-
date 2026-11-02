from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
TRANSITION_DURATION = 0.5      # xfade overlap in seconds
FONT = Path("C:/Windows/Fonts/arial.ttf")
FONT_BOLD = Path("C:/Windows/Fonts/arialbd.ttf")

# Fallback: if FONT_BOLD doesn't exist, use FONT
if not FONT_BOLD.exists():
    FONT_BOLD = FONT

# ─── Cinzel Bold font (luxury heading font) ───────────────────────────────────
# Cached in project-local fonts/ directory; downloaded from Google Fonts on
# first use. Falls back to FONT_BOLD (Arial Bold) if download fails.
_FONTS_DIR = Path(__file__).parent.parent / "fonts"
_CINZEL_BOLD_PATH = _FONTS_DIR / "Cinzel-Bold.ttf"
_CINZEL_BOLD_URL = (
    "https://fonts.gstatic.com/s/cinzel/v26/"
    "8vIU7ww63mVu7gtR-kwKxNvkNOjw-jHgTYo.ttf"
)


def _get_cinzel_bold() -> Path:
    """Return a Path to the Cinzel Bold TTF, downloading it once if needed.

    Falls back to ``FONT_BOLD`` (Arial Bold) when the network is unavailable or
    the download fails, so the end card is always renderable.
    """
    if _CINZEL_BOLD_PATH.exists():
        return _CINZEL_BOLD_PATH
    try:
        _FONTS_DIR.mkdir(parents=True, exist_ok=True)
        logging.info("Downloading Cinzel Bold font for end-card rendering…")
        req = urllib.request.Request(
            _CINZEL_BOLD_URL,
            headers={"User-Agent": "RealityAssistantDrive/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            _CINZEL_BOLD_PATH.write_bytes(response.read())
        logging.info("Cinzel Bold saved to %s", _CINZEL_BOLD_PATH)
        return _CINZEL_BOLD_PATH
    except Exception as exc:
        logging.warning("Could not download Cinzel Bold (%s); using Arial Bold.", exc)
        return FONT_BOLD


# ─── Feature-to-caption mapping ──────────────────────────────────────────────
_FEATURE_CAPTIONS: dict[str, str] = {
    "private balcony":  "Step out to your private balcony — mornings will never be the same.",
    "near metro":       "Seamlessly connected. The metro is just minutes away.",
    "metro":            "Seamlessly connected. The metro is just minutes away.",
    "24/7 security":    "Peace of mind, day and night. Round-the-clock security, always.",
    "security":         "Peace of mind, day and night. Round-the-clock security, always.",
    "modular kitchen":  "A kitchen built for those who love to cook.",
    "kitchen":          "A kitchen built for those who love to cook.",
    "gym and pool":     "Your lifestyle. Elevated. Gym and pool, right at home.",
    "gym":              "Stay fit without stepping out. Your personal gym is right here.",
    "pool":             "Dive into luxury. A sparkling pool awaits.",
    "swimming pool":    "Dive into luxury. A sparkling pool awaits.",
    "parking":          "Your vehicle, always safe. Dedicated parking included.",
    "lift":             "Modern living at every level. Lift access throughout.",
    "power backup":     "Never in the dark. Full power backup, always on.",
    "garden":           "Breathe easy. Step into a lush private garden.",
    "terrace":          "Your rooftop retreat — a terrace made for golden evenings.",
    "clubhouse":        "Community and comfort. A world-class clubhouse at your doorstep.",
    "vaastu":           "Built with intention. Vaastu-compliant design for a harmonious home.",
    "semi-furnished":   "Move in ready. Semi-furnished and thoughtfully designed.",
    "furnished":        "Fully furnished. Just bring yourself.",
    "school":           "Top schools nearby — the perfect neighbourhood to raise a family.",
    "hospital":         "Healthcare close at hand. Leading hospitals just minutes away.",
    "park":             "Green spaces all around. A park right at your doorstep.",
}


def build_captions(listing: dict) -> list[str]:
    """Produce persuasive, human-sounding sales captions from listing facts.

    Uses up to 7 features so all uploaded images (4–8) each get a dedicated scene.
    Every caption is derived exclusively from submitted listing facts.
    """
    features = listing["features"][:7]
    captions: list[str] = [
        f"{listing['property_name']} — {listing['property_type']} in {listing['location']}",
        f"Priced at {listing['price']}. A home worth every rupee.",
    ]
    for feature in features:
        key = feature.strip().lower()
        caption = _FEATURE_CAPTIONS.get(key, f"Experience {feature} — crafted for modern living.")
        captions.append(caption)
    captions.append(f"{listing['cta']}. This is your home.")
    return captions


# ─── Internal helpers ─────────────────────────────────────────────────────────

# Four alternating Ken Burns zoom patterns (Requirement 2.3): zoom-in centre,
# zoom-out top-right, zoom-in bottom-left, zoom-out top-centre.
_ZOOM_PATTERNS: list[dict] = [
    {"zoom_start": 1.0, "zoom_end": 1.18, "focus": (0.5, 0.5), "focus_end": (0.55, 0.45)},
    {"zoom_start": 1.18, "zoom_end": 1.0, "focus": (0.7, 0.3), "focus_end": (0.5, 0.5)},
    {"zoom_start": 1.0, "zoom_end": 1.15, "focus": (0.3, 0.6), "focus_end": (0.5, 0.4)},
    {"zoom_start": 1.12, "zoom_end": 1.0, "focus": (0.5, 0.4), "focus_end": (0.4, 0.6)},
]


def _zoom_pattern(scene_index: int) -> dict:
    """Return the Ken Burns pattern for a scene, cycling with period 4."""
    return _ZOOM_PATTERNS[scene_index % len(_ZOOM_PATTERNS)]


# Transition styles — varied per scene pair for visual interest (Requirement 5.2).
_TRANSITIONS: list[str] = ["fade", "smoothleft", "fadeblack", "smoothleft", "fade", "circleopen", "fade"]


def _xfade_transitions(n_scenes: int) -> list[str]:
    """Return the transition name for each of the ``n_scenes - 1`` scene pairs."""
    return [_TRANSITIONS[i % len(_TRANSITIONS)] for i in range(max(n_scenes - 1, 0))]


def _xfade_offsets(durations: list[float]) -> list[float]:
    """Return the xfade offset for each transition point.

    For the transition after scene ``i`` the offset is the timeline position at
    which that transition starts: ``sum(durations[0..i]) − (i + 1) × TRANSITION_DURATION``.
    Returns ``n − 1`` offsets for ``n`` scenes (Requirement 5.5).
    """
    return [round(sum(durations[: i + 1]) - (i + 1) * TRANSITION_DURATION, 3) for i in range(len(durations) - 1)]


def _escape_drawtext(text: str) -> str:
    """Escape a literal for use inside FFmpeg drawtext's single-quoted values.

    Backslashes are doubled first, then apostrophes and colons are escaped so
    the filter parser cannot mistake them for quoting or option separators
    (Requirement 6.6). Dynamic *text* is passed via ``textfile=`` instead
    (raw, no escaping needed); this helper remains for quoted inline values
    such as file paths.
    """
    return text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def _escape_filter_path(path: Path) -> str:
    """Escape a file path for a quoted drawtext option value (drive colon etc.)."""
    return _escape_drawtext(str(path).replace("\\", "/"))


def _run(command: list[str]) -> None:
    # Popen + communicate avoids the Windows pipe-buffer deadlock that
    # capture_output=True causes when FFmpeg writes >64 KB of progress output.
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, stderr_bytes = proc.communicate()
    if proc.returncode:
        stderr_text = stderr_bytes.decode(errors="replace")[-800:]
        raise RuntimeError(f"Video rendering failed: {stderr_text}")


def _end_card(listing: dict, path: Path) -> None:
    """Render a luxury end-card JPEG using Pillow.

    Canvas: 1080×1920 on #042f2e dark teal background.
    Uses Cinzel Bold (downloaded lazily from Google Fonts) for headings, with
    Arial Bold as an automatic fallback when the network is unavailable.

    Vertical rhythm
    ───────────────
    y=330–333  decorative rule (aqua #99f6e4)
    y=460      property name   Cinzel Bold 74  white
    y=560      location        Arial 40        #99f6e4
    y=640–642  mid separator   —               #4db6ac
    y=710      property type   Arial 30        #7ecec4
    y=830      price (hero)    Cinzel Bold 92  white
    y=900      "ASKING PRICE"  Arial 28        #7ecec4
    y=980–982  bottom rule     —               #2d6e68
    y=1060–1150 CTA button     Cinzel Bold 36  white on #0f766e
    y=1230     tagline         Arial 26        #4db6ac
    """
    cinzel_bold = _get_cinzel_bold()       # Cinzel Bold, or Arial Bold on fallback
    arial = FONT if FONT.exists() else FONT_BOLD

    canvas = Image.new("RGB", (W, H), "#042f2e")
    draw = ImageDraw.Draw(canvas)

    # ── Decorative top rule (aqua, centred, 140 px wide) ──────────────────────
    rule_w = 140
    draw.rectangle(
        [(W // 2 - rule_w // 2, 330), (W // 2 + rule_w // 2, 333)],
        fill="#99f6e4",
    )

    # ── Property name — Cinzel Bold 74, white, centred ────────────────────────
    f_name = ImageFont.truetype(str(cinzel_bold), 74)
    draw.text((W // 2, 460), listing["property_name"], anchor="mm", font=f_name, fill="white")

    # ── Location — Arial 40, aqua #99f6e4, centred ───────────────────────────
    f_loc = ImageFont.truetype(str(arial), 40)
    draw.text((W // 2, 560), listing["location"], anchor="mm", font=f_loc, fill="#99f6e4")

    # ── Mid separator (teal, centred, 120 px wide) ────────────────────────────
    draw.rectangle([(W // 2 - 60, 640), (W // 2 + 60, 642)], fill="#4db6ac")

    # ── Property type — Arial 30, #7ecec4, caps, centred ─────────────────────
    f_type = ImageFont.truetype(str(arial), 30)
    draw.text((W // 2, 710), listing["property_type"].upper(), anchor="mm", font=f_type, fill="#7ecec4")

    # ── Price hero — Cinzel Bold 92, white, centred ───────────────────────────
    f_price = ImageFont.truetype(str(cinzel_bold), 92)
    draw.text((W // 2, 830), listing["price"], anchor="mm", font=f_price, fill="white")

    # ── "ASKING PRICE" label — Arial 28, #7ecec4, centred ────────────────────
    f_label = ImageFont.truetype(str(arial), 28)
    draw.text((W // 2, 900), "ASKING PRICE", anchor="mm", font=f_label, fill="#7ecec4")

    # ── Bottom decorative rule (dark teal, centred, 440 px wide) ─────────────
    draw.rectangle(
        [(W // 2 - 220, 980), (W // 2 + 220, 982)],
        fill="#2d6e68",
    )

    # ── CTA pill button — rounded rect fill #0f766e, Cinzel Bold 36 white ─────
    cta_x1, cta_y1, cta_x2, cta_y2 = 180, 1060, 900, 1150
    try:
        draw.rounded_rectangle(
            [(cta_x1, cta_y1), (cta_x2, cta_y2)], radius=16, fill="#0f766e"
        )
    except AttributeError:
        # Older Pillow builds (< 8.2) do not have rounded_rectangle
        draw.rectangle([(cta_x1, cta_y1), (cta_x2, cta_y2)], fill="#0f766e")
    f_cta = ImageFont.truetype(str(cinzel_bold), 36)
    cta_cx = (cta_x1 + cta_x2) // 2
    cta_cy = (cta_y1 + cta_y2) // 2
    draw.text((cta_cx, cta_cy), listing["cta"], anchor="mm", font=f_cta, fill="white")

    # ── Tagline — Arial 26, teal #4db6ac, centred ────────────────────────────
    f_tag = ImageFont.truetype(str(arial), 26)
    draw.text(
        (W // 2, 1230),
        "Reality Drive · AI Property Studio",
        anchor="mm",
        font=f_tag,
        fill="#4db6ac",
    )

    canvas.save(path, quality=95)


def _render_scene_ffmpeg_fallback(image: Path, duration: float, output: Path, pattern: dict) -> None:
    """FFmpeg Ken Burns via zoompan directly at 1080x1920 (no upscale trick).

    Skipping the 2-4x upscale step cuts per-scene render time from minutes to
    under 2 seconds, which is critical for a responsive web UX. The zoompan
    filter produces smooth enough motion at native resolution when encoded with
    a fast preset. Commas inside min()/max() expressions are escaped as \\,
    for FFmpeg 9's filter option parser.
    """
    frames = int(duration * FPS)
    zoom_start = pattern["zoom_start"]
    zoom_end = pattern["zoom_end"]
    zoom_delta = (zoom_end - zoom_start) / max(frames, 1)

    # FFmpeg 9 requires commas inside z= expressions to be escaped as \\,
    if zoom_delta > 0:
        z_expr = "min(zoom+{delta:.6f}\\,{end})".format(delta=zoom_delta, end=zoom_end)
    else:
        z_expr = "max(zoom+{delta:.6f}\\,{end})".format(delta=zoom_delta, end=zoom_end)

    _run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image),
        "-t", str(duration),
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
               f"zoompan=z='{z_expr}':d={frames}:s={W}x{H}:fps={FPS}",
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        str(output),
    ])


def _overlay_caption_on_clip(clip_path: Path, caption: str, property_name: str, scene_index: int, work: Path) -> None:
    """Burn a styled caption overlay onto an existing clip. Overwrites clip_path in-place.

    Both dynamic strings (caption and property-name watermark) are passed via
    ``textfile=`` — raw file content needs no filter escaping, which matters
    because FFmpeg's inline-quoted escaping of apostrophes is unreliable.
    """
    caption_file = work / f"cap-{clip_path.stem}.txt"
    caption_file.write_text(caption, encoding="utf-8")
    name_file = work / f"name-{clip_path.stem}.txt"
    name_file.write_text(property_name, encoding="utf-8")

    font_esc = _escape_filter_path(FONT)
    caption_esc = _escape_filter_path(caption_file)
    name_esc = _escape_filter_path(name_file)

    tmp = clip_path.with_suffix(".overlay_tmp.mp4")

    scrim_top_y = H - 420
    scrim_mid_y = H - 210

    filters = (
        # Scrim gradient (two boxes)
        f"drawbox=x=0:y={scrim_top_y}:w={W}:h=210:color=black@0.30:t=fill,"
        f"drawbox=x=0:y={scrim_mid_y}:w={W}:h=210:color=black@0.65:t=fill,"
        # Main caption — large, left-aligned, white with shadow
        f"drawtext=fontfile='{font_esc}':textfile='{caption_esc}':"
        f"fontsize=54:fontcolor=white:x=72:y={H - 320}:"
        f"line_spacing=18:shadowx=2:shadowy=3:shadowcolor=black@0.70:"
        f"box=0,"
        # Property name watermark — top-left, small, semi-transparent
        f"drawtext=fontfile='{font_esc}':textfile='{name_esc}':"
        f"fontsize=28:fontcolor=white@0.55:x=60:y=72:"
        f"shadowx=1:shadowy=1:shadowcolor=black@0.5"
    )

    _run([
        "ffmpeg", "-y", "-i", str(clip_path),
        "-vf", filters,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(tmp),
    ])
    tmp.replace(clip_path)


def _render_scene_still_motion(
    image: Path, duration: float, output: Path,
    property_name: str, scene_index: int, caption: str, work: Path
) -> None:
    """Render a single scene with Ken Burns motion, then overlay captions.

    Tries still-motion first (best quality) but applies a Windows-safe monkey-patch
    to still_motion's FFmpeg runner so it uses Popen+communicate instead of
    capture_output=True (which deadlocks when FFmpeg fills the 64 KB pipe buffer).
    Falls back to the direct FFmpeg zoompan path on any failure.
    """
    pattern = _zoom_pattern(scene_index)

    # Use our own FFmpeg zoompan implementation directly.
    # still_motion produces marginally sharper Ken Burns but takes 2+ minutes
    # per scene on Windows regardless of preset — completely unusable for a
    # web request. Our zoompan path renders in under 2 seconds per scene.
    _render_scene_ffmpeg_fallback(image, duration, output, pattern)

    if caption:
        _overlay_caption_on_clip(output, caption, property_name, scene_index, work)



def _narrate_one(text: str, output: Path) -> None:
    """Synthesise speech. Uses a short temp path to avoid pyttsx3 spaces-in-path bug on Windows."""
    try:
        import pyttsx3
    except ImportError as error:
        raise RuntimeError("pip install pyttsx3") from error

    # pyttsx3 save_to_file silently fails on Windows when path contains spaces.
    # Use a fixed short temp path, then copy.
    tmp_path = Path(tempfile.gettempdir()) / "rad_voice_tmp.wav"

    engine = pyttsx3.init()
    # Select first available English-sounding voice
    voices = engine.getProperty("voices")
    for v in voices:
        if "english" in (v.name or "").lower() or "en" in (v.id or "").lower():
            engine.setProperty("voice", v.id)
            break
    engine.setProperty("rate", 148)
    engine.setProperty("volume", 1.0)
    engine.save_to_file(text, str(tmp_path))
    engine.runAndWait()

    if not tmp_path.exists() or tmp_path.stat().st_size < 1000:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            "TTS narration produced no audio. "
            "Uncheck narration or install a Windows speech voice via Settings → Time & Language → Speech."
        )
    shutil.copy2(str(tmp_path), str(output))
    tmp_path.unlink(missing_ok=True)


def _pad_audio_to(audio: Path, duration: float, output: Path) -> None:
    """Trim or silence-pad audio to exactly `duration` seconds."""
    _run([
        "ffmpeg", "-y",
        "-i", str(audio),
        "-af", f"apad=whole_dur={duration}",
        "-t", str(duration),
        "-c:a", "pcm_s16le",
        str(output),
    ])


def _mux_audio_into_scene(video: Path, audio: Path, output: Path) -> None:
    """Combine a mute video clip with an audio track, keeping the video duration."""
    _run([
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        str(output),
    ])


def _build_xfade_concat(scene_paths: list[Path], durations: list[float], output_path: Path) -> None:
    """Concatenate scenes with xfade cross-dissolve transitions using a single filter_complex."""
    n = len(scene_paths)
    if n == 1:
        # Nothing to fade — just copy
        shutil.copy2(str(scene_paths[0]), str(output_path))
        return

    transitions = _xfade_transitions(n)
    offsets = _xfade_offsets(durations)

    inputs_args = []
    for p in scene_paths:
        inputs_args += ["-i", str(p)]

    # Build filter_complex
    fc_parts = []
    prev_label = "[0:v]"

    for i in range(1, n):
        transition = transitions[i - 1]
        is_last = (i == n - 1)
        curr_label = "[vout]" if is_last else f"[v{i}]"
        fc_parts.append(
            f"{prev_label}[{i}:v]xfade=transition={transition}:"
            f"duration={TRANSITION_DURATION}:offset={offsets[i - 1]:.3f}{curr_label}"
        )
        prev_label = curr_label

    filter_complex = ";".join(fc_parts)

    _run([
        "ffmpeg", "-y",
        *inputs_args,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ])


def _verify(path: Path) -> None:
    proc = subprocess.Popen(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_bytes, _ = proc.communicate()
    try:
        duration = float(json.loads(stdout_bytes.decode(errors="replace"))["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        duration = 0
    if proc.returncode or duration < 8:
        raise RuntimeError("Export verification failed. Please try different source images.")
