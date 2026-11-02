from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from functools import partial
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .editor import render_video
from .quality_check import check_images
from .video import build_captions

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web"
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
RENDERS = DATA / "renders"
for folder in (UPLOADS, RENDERS):
    folder.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Reality Assistant Drive")
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class Listing(BaseModel):
    property_name: str = Field(min_length=2, max_length=90)
    location: str = Field(min_length=2, max_length=100)
    price: str = Field(min_length=1, max_length=60)
    property_type: str = Field(min_length=2, max_length=60)
    features: list[str] = Field(min_length=1, max_length=8)
    cta: str = Field(min_length=2, max_length=120)


def clean_name(name: str) -> str:
    return "".join(char for char in name if char.isalnum() or char in "._-") or "upload"


async def ollama_script(listing: Listing) -> str | None:
    """Ask local Ollama only for a concise introduction; fallback always remains available."""
    facts = listing.model_dump()
    prompt = (
        "Write one 20-word real-estate video introduction using only this JSON. "
        "Never add amenities, measurements, claims, or opinions. Return plain text only.\n"
        + json.dumps(facts)
    )
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": "gemma4:e2b", "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            text = response.json().get("response", "").strip()
            return text[:220] if _is_fact_safe(text, listing) else None
    except (httpx.HTTPError, ValueError):
        return None


def _is_fact_safe(text: str, listing: Listing) -> bool:
    """Reject model output containing words outside submitted facts and neutral glue words."""
    fact_text = " ".join([listing.property_name, listing.location, listing.price,
                           listing.property_type, *listing.features, listing.cta]).lower()
    allowed = set(re.findall(r"[a-z0-9]+", fact_text))
    allowed.update({"in", "at", "with", "and", "the", "a", "an", "your", "property", "home"})
    words = re.findall(r"[a-z0-9]+", text.lower())
    return bool(words) and all(word in allowed for word in words)


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/preview-script")
async def preview_script(listing: Listing) -> dict:
    captions = build_captions(listing.model_dump())
    ai_intro = await ollama_script(listing)
    return {"captions": captions, "ai_intro": ai_intro, "ai_available": ai_intro is not None}


@app.post("/api/analyse")
async def analyse(
    property_name: Annotated[str, Form()],
    location: Annotated[str, Form()],
    price: Annotated[str, Form()],
    property_type: Annotated[str, Form()],
    features: Annotated[str, Form()],
    cta: Annotated[str, Form()],
    images: Annotated[list[UploadFile], File()] = [],
) -> dict:
    feature_list = [item.strip() for item in features.split(",") if item.strip()]
    listing_data = {
        "property_name": property_name.strip(),
        "location": location.strip(),
        "price": price.strip(),
        "property_type": property_type.strip(),
        "features": feature_list,
        "cta": cta.strip(),
    }
    captions = build_captions(listing_data)
    uploads = [(await img_file.read(), img_file.filename or "image") for img_file in images]
    image_reports = check_images(uploads)
    suggestions = []
    if any(r["issues"] for r in image_reports):
        suggestions.append("Some images may affect video quality \u2014 see per-image feedback above.")
    if len(feature_list) < 3:
        suggestions.append("Add more features (3\u20135) for a richer video narrative.")
    return {
        "captions": captions,
        "image_reports": image_reports,
        "suggestions": suggestions,
    }


@app.post("/api/render")
async def render(
    property_name: Annotated[str, Form()],
    location: Annotated[str, Form()],
    price: Annotated[str, Form()],
    property_type: Annotated[str, Form()],
    features: Annotated[str, Form()],
    cta: Annotated[str, Form()],
    narration: Annotated[bool, Form()] = False,
    cinema_mode: Annotated[bool, Form()] = False,
    images: Annotated[list[UploadFile], File()] = [],
    music: Annotated[UploadFile | None, File()] = None,
) -> dict:
    try:
        feature_list = [item.strip() for item in features.split(",") if item.strip()]
        listing = Listing(
            property_name=property_name.strip(), location=location.strip(), price=price.strip(),
            property_type=property_type.strip(), features=feature_list, cta=cta.strip(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not 4 <= len(images) <= 8:
        raise HTTPException(status_code=422, detail="Upload between 4 and 8 property images.")
    job_id = uuid.uuid4().hex
    job_dir = UPLOADS / job_id
    job_dir.mkdir()
    image_paths: list[Path] = []
    try:
        for image in images:
            if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise HTTPException(status_code=422, detail="Images must be JPG, PNG, or WebP.")
            destination = job_dir / clean_name(image.filename or "property.jpg")
            destination.write_bytes(await image.read())
            image_paths.append(destination)
        music_path = None
        if music and music.filename:
            if music.content_type not in {"audio/mpeg", "audio/wav", "audio/x-wav"}:
                raise HTTPException(status_code=422, detail="Music must be MP3 or WAV.")
            music_path = job_dir / clean_name(music.filename)
            music_path.write_bytes(await music.read())
        output = RENDERS / f"{job_id}.mp4"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(render_video, listing.model_dump(), image_paths, output, music_path, narration, cinema_mode)
        )
        return {"download_url": f"/api/download/{job_id}", "captions": build_captions(listing.model_dump())}
    except HTTPException:
        raise
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/api/svd-status")
def svd_status() -> dict:
    """Return SVD availability, VRAM, and model-loaded state. Never triggers model loading."""
    try:
        import torch
        if not torch.cuda.is_available():
            return {"available": False, "vram_gb": 0.0, "model_loaded": False}
        vram = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
        from . import svd_engine
        return {
            "available": vram >= 6.0,
            "vram_gb": vram,
            "model_loaded": svd_engine._svd_pipe is not None,
        }
    except Exception:
        return {"available": False, "vram_gb": 0.0, "model_loaded": False}


@app.get("/api/download/{job_id}")
def download(job_id: str) -> FileResponse:
    path = RENDERS / f"{job_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="This export is no longer available.")
    return FileResponse(path, media_type="video/mp4", filename="property-video.mp4")
