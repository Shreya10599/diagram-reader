from __future__ import annotations

import base64
import io
import re

from PIL import Image


def zoom_image(original_b64: str, region: str = "") -> str:
    """Crops and upscales a region of the original chart image so the
    verification agent can get a closer look at ambiguous points. Returns
    the crop as a PNG data URL.

    `region` is Claude's free-text description of where to zoom. It may be
    an explicit pixel box ("x:200-400, y:100-300") or a keyword
    ("left"/"right"/"top"/"bottom"/"center"); anything else falls back to
    upscaling the whole chart. Zooming is deterministic — the crop uses the
    region text only as a hint, so a bad region can't silently return
    nothing."""
    img = Image.open(io.BytesIO(base64.b64decode(original_b64))).convert("RGB")
    width, height = img.size

    bbox = _parse_region(region, width, height)
    crop = img.crop(bbox) if bbox else img

    scale = 2
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)

    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _parse_region(region: str, width: int, height: int) -> tuple | None:
    """Turns a region description into a (left, top, right, bottom) crop box,
    or None to mean 'whole image'. Never returns a box outside the image."""
    text = region.strip().lower()

    box_match = re.search(r"x[:\s]*(\d+)[-–](\d+).*?y[:\s]*(\d+)[-–](\d+)", text)
    if box_match:
        left, right, top, bottom = map(int, box_match.groups())
        left = min(max(left, 0), width - 1)
        top = min(max(top, 0), height - 1)
        right = min(max(right, left + 1), width)
        bottom = min(max(bottom, top + 1), height)
        return (left, top, right, bottom)

    halves = {
        "left": (0, 0, width // 2, height),
        "right": (width // 2, 0, width, height),
        "top": (0, 0, width, height // 2),
        "bottom": (0, height // 2, width, height),
        "center": (width // 4, height // 4, 3 * width // 4, 3 * height // 4),
    }
    for keyword, bbox in halves.items():
        if keyword in text:
            return bbox

    return None
