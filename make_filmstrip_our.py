#!/usr/bin/env python3
"""为我们 final_v6.mp4 抽帧合成 4x4 网格 filmstrip（带时间标注）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FRAME_DIR = ROOT / "ref_analysis" / "frames_our"
OUT_DIR = ROOT / "ref_analysis"

COLS = 4
ROWS = 4
N_FRAMES = COLS * ROWS  # 16

# 1920x1080 → 16:9 → cell 320x180
CELL_W = 320
CELL_H = 180
PAD = 8
BORDER = 4

SECS_PER_FRAME = 1  # 1 fps


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _timecode(frame_idx_1based: int) -> str:
    secs = (frame_idx_1based - 1) * SECS_PER_FRAME
    mm, ss = divmod(secs, 60)
    return f"{mm:02d}:{ss:02d}"


def compose_filmstrip(frames: list[Image.Image], labels: bool = True) -> Image.Image:
    assert len(frames) == N_FRAMES, f"need {N_FRAMES} frames, got {len(frames)}"

    canvas_w = COLS * CELL_W + (COLS + 1) * PAD + 2 * BORDER
    canvas_h = ROWS * CELL_H + (ROWS + 1) * PAD + 2 * BORDER

    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    inner = Image.new("RGB", (canvas_w - 2 * BORDER, canvas_h - 2 * BORDER), (24, 24, 24))
    draw = ImageDraw.Draw(inner)

    if labels:
        idx_font = _load_font(28)
        tc_font = _load_font(22)

    for i, fr in enumerate(frames):
        if fr.mode != "RGB":
            fr = fr.convert("RGB")
        cell = fr.copy()
        cell.thumbnail((CELL_W, CELL_H), Image.LANCZOS)
        cw, ch = cell.size
        r, c = divmod(i, COLS)
        ox = PAD + c * (CELL_W + PAD)
        oy = PAD + r * (CELL_H + PAD)
        px = ox + (CELL_W - cw) // 2
        py = oy + (CELL_H - ch) // 2
        inner.paste(cell, (px, py))

        if labels:
            frame_no = i + 1
            tc = _timecode(frame_no)
            idx_label = f"#{frame_no:02d}"
            try:
                tw = int(round(draw.textlength(idx_label, font=idx_font)))
                l, t, r_b, b = idx_font.getbbox(idx_label)
                th = b - t
                ascent = -t
            except AttributeError:
                tw, th, ascent = 30, 24, 0
            pad_l = 6
            bx, by = ox + 4, oy + 4
            draw.rectangle([bx, by, bx + tw + pad_l * 2, by + th + pad_l * 2],
                           fill=(255, 255, 255), outline=(0, 0, 0))
            draw.text((bx + pad_l, by + pad_l - ascent), idx_label,
                      fill=(0, 0, 0), font=idx_font)
            try:
                tw2 = int(round(draw.textlength(tc, font=tc_font)))
                l2, t2, r_b2, b2 = tc_font.getbbox(tc)
                th2 = b2 - t2
                ascent2 = -t2
            except AttributeError:
                tw2, th2, ascent2 = 60, 22, 0
            pad2 = 5
            tx = ox + CELL_W - tw2 - pad2 * 2 - 4
            ty = oy + CELL_H - th2 - pad2 * 2 - 4
            draw.rectangle([tx, ty, tx + tw2 + pad2 * 2, ty + th2 + pad2 * 2],
                           fill=(0, 0, 0))
            draw.text((tx + pad2, ty + pad2 - ascent2), tc,
                      fill=(255, 255, 255), font=tc_font)

    canvas.paste(inner, (BORDER, BORDER))
    canvas.thumbnail((1536, 1536), Image.LANCZOS)
    return canvas


def _compress_to_kb(img: Image.Image, target_kb: int, max_passes: int = 10) -> bytes:
    if img.mode != "RGB":
        img = img.convert("RGB")
    quality = 88
    best: bytes | None = None
    for _ in range(max_passes):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if best is None or len(data) < len(best):
            best = data
        if len(data) <= target_kb * 1024:
            return data
        if quality > 50:
            quality -= 8
        else:
            img = img.copy()
            img.thumbnail((int(img.width * 0.85), int(img.height * 0.85)), Image.LANCZOS)
    assert best is not None
    return best


def main() -> int:
    if not FRAME_DIR.is_dir():
        print(f"ERROR: {FRAME_DIR} not found")
        return 1
    files = sorted(FRAME_DIR.glob("our_*.jpg"))
    if len(files) < N_FRAMES:
        print(f"ERROR: need {N_FRAMES} frames, found {len(files)}")
        return 1
    files = files[:N_FRAMES]

    frames = [Image.open(p) for p in files]
    strip = compose_filmstrip(frames, labels=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    out_path = OUT_DIR / "filmstrip_our_4x4.jpg"
    raw = _compress_to_kb(strip, target_kb=400)
    out_path.write_bytes(raw)

    print(f"[filmstrip_our] wrote {out_path}  size={strip.size}  bytes={len(raw)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())