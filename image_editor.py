import base64
import io
import os
import re
import subprocess
import time

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from image_engine import SSD_IMAGES_DIR, _normalize, _save_image_record, generate_image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
REALESRGAN_EXE = os.path.join(BIN_DIR, "realesrgan-ncnn-vulkan.exe")
SD_EXE = os.path.join(BIN_DIR, "sd.exe")

COLOR_MAP = {
    "neon blue": (0, 229, 255),
    "cyan": (0, 240, 255),
    "blue": (59, 130, 246),
    "neon green": (57, 255, 20),
    "green": (16, 185, 129),
    "neon pink": (255, 20, 147),
    "pink": (236, 72, 153),
    "red": (239, 68, 68),
    "gold": (250, 204, 21),
    "yellow": (250, 204, 21),
    "purple": (168, 85, 247),
    "white": (255, 255, 255),
    "black": (17, 17, 17),
}


def _resolve_input_path(image_ref: str) -> str:
    """Resolve /ssd_images/foo.jpg or a local file path to an existing file on disk."""
    ref = image_ref.strip()
    if ref.startswith("/ssd_images/"):
        ref = os.path.join(SSD_IMAGES_DIR, os.path.basename(ref))
    elif not os.path.isabs(ref):
        cand = os.path.join(SSD_IMAGES_DIR, os.path.basename(ref))
        if os.path.exists(cand):
            ref = cand
        else:
            ref = os.path.join(BASE_DIR, ref)
    if not os.path.exists(ref):
        raise FileNotFoundError(f"Input image not found: {image_ref}")
    return ref


def _save_edited(img: Image.Image, command: str, mode: str, ext: str = "png") -> dict:
    os.makedirs(SSD_IMAGES_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", _normalize(command))[:28].strip("_") or "edit"
    ts = int(time.time() * 1000)
    fname = f"edit_{slug}_{ts}.{ext}"
    fpath = os.path.join(SSD_IMAGES_DIR, fname)
    if ext.lower() in ("jpg", "jpeg") and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(fpath)
    _save_image_record(f"Edit: {command}", _normalize(command), fname, mode)
    return {
        "filename": fname,
        "path": fpath,
        "url": f"/ssd_images/{fname}",
        "mode": mode,
        "command": command,
    }


def _detect_edit_type(command: str) -> str:
    lower = command.lower()
    if any(k in lower for k in ("background", "replace bg", "change bg")):
        return "background"
    if any(k in lower for k in ("upscale", "4k", "enhance", "sharper", "high res", "super resolution")):
        return "upscale"
    if any(k in lower for k in ("anime", "oil painting", "watercolor", "pixel art", "cyberpunk", "cartoon", "style")):
        return "style"
    if any(k in lower for k in ("remove ", "erase ", "delete ", "inpaint", "sunglasses", "add hat", "add glasses")):
        return "inpaint"
    return "quick"


def _edit_background(img: Image.Image, command: str) -> tuple[Image.Image, str]:
    # Extract target background description
    bg_desc = re.sub(
        r"^(?:change|replace|set|make)\s+(?:the\s+)?(?:background|bg)\s+(?:to|with|into|as)\s+",
        "",
        command.strip(),
        flags=re.IGNORECASE,
    ).strip() or "sunset ocean beach"

    # 1. Remove old background using rembg if available
    fg_rgba = img.convert("RGBA")
    used_rembg = False
    try:
        from rembg import remove

        buf = io.BytesIO()
        fg_rgba.save(buf, format="PNG")
        out_bytes = remove(buf.getvalue())
        fg_rgba = Image.open(io.BytesIO(out_bytes)).convert("RGBA")
        used_rembg = True
    except Exception:
        # Offline fallback: soft elliptical center-subject vignette mask
        w, h = fg_rgba.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((int(w * 0.12), int(h * 0.08), int(w * 0.88), int(h * 0.92)), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(8, min(w, h) // 25)))
        fg_rgba.putalpha(mask)

    # 2. Generate new background (Flux AI / SSD Cache / Offline gradient)
    w, h = fg_rgba.size
    bg_img = None
    try:
        res = generate_image(f"scenic {bg_desc}, wide landscape, no people", force_offline=False)
        bg_path = os.path.join(SSD_IMAGES_DIR, res["filename"])
        if bg_path.lower().endswith((".jpg", ".jpeg", ".png")) and os.path.exists(bg_path):
            bg_img = Image.open(bg_path).convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
    except Exception:
        bg_img = None

    if bg_img is None:
        # 100% offline procedural sunset/ocean/sky gradient backdrop
        bg_img = Image.new("RGBA", (w, h))
        draw = ImageDraw.Draw(bg_img)
        for y in range(h):
            t = y / max(1, h - 1)
            if "beach" in bg_desc.lower() or "sunset" in bg_desc.lower() or "ocean" in bg_desc.lower():
                if t < 0.55:
                    r = int(255 - 80 * t)
                    g = int(110 + 70 * t)
                    b = int(90 + 90 * t)
                else:
                    r = int(20 + 40 * (t - 0.55))
                    g = int(120 + 60 * (t - 0.55))
                    b = int(190 - 30 * (t - 0.55))
            else:
                r = int(25 + 60 * t)
                g = int(40 + 90 * (1 - t))
                b = int(110 + 100 * t)
            draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    composed = Image.alpha_composite(bg_img, fg_rgba).convert("RGB")
    mode = "rembg + AI Background" if used_rembg else "Mask + AI Background"
    return composed, mode


def _edit_upscale(img: Image.Image, input_path: str) -> tuple[Image.Image, str]:
    if os.path.exists(REALESRGAN_EXE):
        os.makedirs(SSD_IMAGES_DIR, exist_ok=True)
        tmp_out = os.path.join(SSD_IMAGES_DIR, f"_tmp_esrgan_{int(time.time()*1000)}.png")
        try:
            proc = subprocess.run(
                [REALESRGAN_EXE, "-i", input_path, "-o", tmp_out, "-n", "realesrgan-x4plus", "-s", "4"],
                capture_output=True,
                timeout=60,
            )
            if proc.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                up = Image.open(tmp_out).convert("RGB")
                try:
                    os.remove(tmp_out)
                except OSError:
                    pass
                return up, "Real-ESRGAN 4x (Offline)"
        except Exception:
            pass
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass

    # Fast 100% offline 4x Lanczos + UnsharpMask fallback
    w, h = img.size
    up = img.convert("RGB").resize((w * 4, h * 4), Image.Resampling.LANCZOS)
    up = up.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3))
    up = ImageEnhance.Contrast(up).enhance(1.08)
    return up, "4x Lanczos + UnsharpMask (Offline)"


def _edit_style(img: Image.Image, command: str) -> tuple[Image.Image, str]:
    lower = command.lower()
    rgb = img.convert("RGB")

    if "pixel" in lower:
        w, h = rgb.size
        small = rgb.resize((max(48, w // 10), max(48, h // 10)), Image.Resampling.BILINEAR)
        quant = small.quantize(colors=32).convert("RGB")
        styled = quant.resize((w, h), Image.Resampling.NEAREST)
        return styled, "Pixel Art Style (Offline)"

    if "oil" in lower or "painting" in lower:
        styled = rgb.filter(ImageFilter.ModeFilter(size=7))
        styled = ImageEnhance.Color(styled).enhance(1.3)
        styled = ImageEnhance.Contrast(styled).enhance(1.15)
        return styled, "Oil Painting Style (Offline)"

    if "watercolor" in lower:
        styled = rgb.filter(ImageFilter.MedianFilter(size=5))
        styled = ImageEnhance.Color(styled).enhance(1.2)
        styled = ImageEnhance.Brightness(styled).enhance(1.06)
        return styled, "Watercolor Style (Offline)"

    if "cyberpunk" in lower or "neon" in lower:
        gray = ImageOps.grayscale(rgb)
        duotone = ImageOps.colorize(gray, black="#09001a", mid="#7c3aed", white="#00f0ff")
        styled = ImageEnhance.Contrast(duotone).enhance(1.25)
        return styled, "Cyberpunk Neon Style (Offline)"

    # Default: Anime cel-shaded look (posterize + vibrant color + ink edges)
    base = ImageOps.posterize(rgb.filter(ImageFilter.SMOOTH_MORE), 5)
    base = ImageEnhance.Color(base).enhance(1.45)
    base = ImageEnhance.Contrast(base).enhance(1.15)
    edges = ImageOps.invert(ImageOps.grayscale(rgb).filter(ImageFilter.FIND_EDGES))
    edges = edges.point(lambda p: 255 if p > 210 else 40).convert("RGB")
    styled = ImageChops.multiply(base, edges)
    return styled, "Anime Cel-Shade Style (Offline)"


def _edit_inpaint(img: Image.Image, command: str, mask_b64: str | None = None) -> tuple[Image.Image, str]:
    rgb = img.convert("RGB")
    w, h = rgb.size

    if "sunglasses" in command.lower() or "glasses" in command.lower():
        out = rgb.copy()
        draw = ImageDraw.Draw(out)
        # Draw sleek dark sunglasses centered in upper-middle third
        cy = int(h * 0.36)
        lw = int(w * 0.14)
        lh = int(h * 0.07)
        cx1, cx2 = int(w * 0.38), int(w * 0.62)
        draw.rounded_rectangle([cx1 - lw // 2, cy - lh // 2, cx1 + lw // 2, cy + lh // 2], radius=10, fill=(15, 15, 20), outline=(220, 220, 230), width=3)
        draw.rounded_rectangle([cx2 - lw // 2, cy - lh // 2, cx2 + lw // 2, cy + lh // 2], radius=10, fill=(15, 15, 20), outline=(220, 220, 230), width=3)
        draw.line([(cx1 + lw // 2, cy), (cx2 - lw // 2, cy)], fill=(220, 220, 230), width=4)
        return out, "Inpaint Overlay (Offline)"

    # If user painted a mask, blur/blend surrounding pixels into masked region
    if mask_b64:
        try:
            raw = base64.b64decode(mask_b64.split(",")[-1])
            mask = Image.open(io.BytesIO(raw)).convert("L").resize((w, h))
            blurred = rgb.filter(ImageFilter.GaussianBlur(radius=max(12, min(w, h) // 20)))
            out = Image.composite(blurred, rgb, mask)
            return out, "Masked Inpaint Fill (Offline)"
        except Exception:
            pass

    # Fallback: soft center-region content blur/fill
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse((int(w * 0.35), int(h * 0.35), int(w * 0.65), int(h * 0.65)), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=16))
    blurred = rgb.filter(ImageFilter.GaussianBlur(radius=24))
    return Image.composite(blurred, rgb, mask), "Smart Region Inpaint (Offline)"


def _edit_quick(img: Image.Image, command: str) -> tuple[Image.Image, str]:
    lower = command.lower()
    rgb = img.convert("RGB")

    if "black and white" in lower or "b&w" in lower or "grayscale" in lower:
        return ImageOps.grayscale(rgb).convert("RGB"), "Quick Edit: B&W (Offline)"

    if "sepia" in lower or "vintage" in lower:
        gray = ImageOps.grayscale(rgb)
        sepia = ImageOps.colorize(gray, black="#2b1b0e", white="#f5e6c8")
        return sepia, "Quick Edit: Sepia (Offline)"

    if "blur" in lower:
        return rgb.filter(ImageFilter.GaussianBlur(radius=6)), "Quick Edit: Blur (Offline)"

    if "sharpen" in lower:
        return rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=3)), "Quick Edit: Sharpen (Offline)"

    if "mirror" in lower or "flip" in lower:
        return ImageOps.mirror(rgb), "Quick Edit: Mirror (Offline)"

    if "rotate" in lower:
        m = re.search(r"(\d+)", lower)
        angle = int(m.group(1)) if m else 90
        return rgb.rotate(-angle, expand=True), f"Quick Edit: Rotate {angle}° (Offline)"

    if "crop" in lower and "square" in lower:
        w, h = rgb.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        return rgb.crop((left, top, left + side, top + side)), "Quick Edit: Crop Square (Offline)"

    if "neon" in lower and "text" not in lower:
        glow = rgb.filter(ImageFilter.GaussianBlur(radius=8))
        glow = ImageEnhance.Color(glow).enhance(1.8)
        return ImageChops.screen(rgb, glow), "Quick Edit: Neon Glow (Offline)"

    if "add text" in lower or "text" in lower:
        out = rgb.copy()
        draw = ImageDraw.Draw(out)
        m = re.search(r"['\"]([^'\"]+)['\"]", command)
        label = m.group(1) if m else "ARGON AI"

        text_color = (255, 255, 255)
        for cname, cval in COLOR_MAP.items():
            if cname in lower:
                text_color = cval
                break

        w, h = out.size
        font_size = max(24, min(w, h) // 11)
        font = None
        for font_path in (
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ):
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (w - tw) // 2
        if "top" in lower:
            ty = int(h * 0.06)
        elif "center" in lower:
            ty = (h - th) // 2
        else:
            ty = h - th - int(h * 0.06)

        # Neon / shadow outline
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3)):
            draw.text((tx + dx, ty + dy), label, font=font, fill=(0, 0, 0))
        draw.text((tx, ty), label, font=font, fill=text_color)
        return out, "Quick Edit: Text Overlay (Offline)"

    # Default quick enhancement
    return ImageEnhance.Contrast(rgb).enhance(1.12), "Quick Edit: Auto-Enhance (Offline)"


def apply_image_edit(image_ref: str, command: str, mask_b64: str | None = None) -> dict:
    """Apply natural-language edit to an image and save result to ssd_images/."""
    t_start = time.perf_counter()
    input_path = _resolve_input_path(image_ref)
    img = Image.open(input_path)
    edit_type = _detect_edit_type(command)

    if edit_type == "background":
        edited, mode = _edit_background(img, command)
    elif edit_type == "upscale":
        edited, mode = _edit_upscale(img, input_path)
    elif edit_type == "style":
        edited, mode = _edit_style(img, command)
    elif edit_type == "inpaint":
        edited, mode = _edit_inpaint(img, command, mask_b64=mask_b64)
    else:
        edited, mode = _edit_quick(img, command)

    res = _save_edited(edited, command, mode, ext="png")
    res["latency_ms"] = round((time.perf_counter() - t_start) * 1000.0, 2)
    res["edit_type"] = edit_type
    res["original_url"] = image_ref if image_ref.startswith("/ssd_images/") else f"/ssd_images/{os.path.basename(input_path)}"
    return res
