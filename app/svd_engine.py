"""SVD (Stable Video Diffusion) engine — lazy singleton, VRAM-guarded."""
from __future__ import annotations

import tempfile
from pathlib import Path

_svd_pipe = None  # singleton


def _get_pipe():
    global _svd_pipe
    if _svd_pipe is not None:
        return _svd_pipe
    import torch
    from diffusers import StableVideoDiffusionPipeline

    pipe = StableVideoDiffusionPipeline.from_pretrained(
        "stabilityai/stable-video-diffusion-img2vid-xt",
        torch_dtype=torch.float16,
        variant="fp16",
    )
    if torch.cuda.is_available():
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
    else:
        vram_gb = 0.0

    if vram_gb < 8.0:
        pipe.enable_model_cpu_offload()
        pipe.unet.enable_forward_chunking()
    else:
        pipe.to("cuda")
    _svd_pipe = pipe
    return pipe


def generate_clip(image_path: Path, output_path: Path, motion_bucket: int = 127, fps: int = 7) -> bool:
    """Generate a 25-frame SVD-XT clip from image_path. Returns True on success."""
    try:
        import torch
        from diffusers.utils import export_to_video
        from PIL import Image as PILImage

        pipe = _get_pipe()
        img = PILImage.open(image_path).convert("RGB").resize((1024, 576))
        generator = torch.manual_seed(42)
        frames = pipe(
            img,
            decode_chunk_size=4,
            generator=generator,
            motion_bucket_id=motion_bucket,
            noise_aug_strength=0.02,
            num_frames=25,
        ).frames[0]

        # export_to_video writes at SVD's native 576x1024 landscape — we need 1080x1920 portrait
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as _f:
            tmp = Path(_f.name)
        export_to_video(frames, str(tmp), fps=fps)

        # Re-encode to 1080x1920 portrait with FFmpeg
        import subprocess
        proc = subprocess.Popen([
            "ffmpeg", "-y", "-i", str(tmp),
            "-vf", "transpose=1,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
            str(output_path),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.communicate()
        tmp.unlink(missing_ok=True)
        return proc.returncode == 0
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("SVD inference failed: %s", exc)
        return False
