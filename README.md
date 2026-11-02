# Reality Assistant Drive

A local-first property-video generator. Add listing details and 4-8 real property photos to produce a vertical MP4 with captions, gentle cinematic movement, transitions, a closing call to action, and optional local narration or licensed background music.

## What it uses

- Vanilla HTML, CSS, and JavaScript frontend
- FastAPI backend
- FFmpeg for video export
- Optional Ollama model `gemma4:e2b` for script ideas; deterministic templates keep the result limited to supplied facts
- Optional `pyttsx3` narration and an uploaded royalty-free music file

## Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) available on `PATH`
- Optional: [Ollama](https://ollama.com/) with `ollama pull gemma4:e2b`
- Optional narration: `pip install pyttsx3`

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Input rules

Use real property images. The app improves presentation with motion, framing, captions and transitions, but does not invent rooms or amenities. Any conceptual 3D visual should be clearly marked as a concept and should not be used as a listing photograph.

For the best result, use sharp images at least 1080px wide, one property per export, and a licensed MP3/WAV track if music is desired.

## Verification

`python -m pytest tests/ -v` runs 20 tests: the fact-safe caption generator, request validation, and 13 Hypothesis property tests covering the Pro Video Engine (beat sync, xfade timing, zoom patterns, image quality, text escaping). The render endpoint also runs `ffprobe` before returning a download link.

## Limitations

- Rendering is CPU-intensive and can take several minutes for 1080x1920 video.
- Local TTS quality depends on Windows voices; it is disabled unless requested.
- The app does not bundle music, because a track's license must be verified by its owner.
