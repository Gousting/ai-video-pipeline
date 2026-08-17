#!/usr/bin/env python3
"""Filmstrip composition helper for VLM reviews.

把从一段视频抽出的若干帧合成到一张图里发给 VLM：
- N == 4：2x2 网格
- 其他 N：1xN 横向网格
- 每帧左上角画序号标签（白底黑字、字号按格子尺寸自适应）
- 合成图 thumbnail 后最长边 ≤ 1536px
- JPEG 压缩到 ~200KB 内（base64 体积≈ ≤267KB，远低于逐帧发）

公开 API:
  filmstrip(frames, labels=True)                 -> PIL.Image.Image
  filmstrip_b64(frames, labels=True, kb=200)    -> str  (data:image/jpeg;base64,...)
  save_filmstrip(frames, path, labels=True)     -> Path

CLI 自测:
  python scripts/filmstrip.py
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Iterable, Union

from PIL import Image, ImageDraw, ImageFont

# 合成图最长边硬上限
MAX_LONG_EDGE = 1536
# JPEG 体积目标
DEFAULT_TARGET_KB = 200


def _load_font(size: int) -> ImageFont.ImageFont:
    """优先用 arial（Windows 自带），回落到 PIL 默认位图字体。"""
    candidates = ("arial.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf")
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _cell_target_size(n: int, cols: int) -> tuple[int, int]:
    """根据网格列数计算每个 cell 的最大目标像素（矩形）。

    目标是让合成图最长边 ≤ MAX_LONG_EDGE（含 padding/边框），
    再用 .thumbnail() 兜底一次。
    """
    # 横向：cols=N
    # 单行：cols=N, rows=1 -> 整张就是一行
    # 2x2: cols=2, rows=2
    target_long = MAX_LONG_EDGE - 16  # 预留 padding + 边框
    if cols == 1:
        cell_w = min(640, target_long // max(1, n))
    else:
        cell_w = min(720, target_long // cols)
    cell_h = int(cell_w * 9 / 16)  # 16:9 兜底，比例不明时按 9:16 估计
    return cell_w, cell_h


def _normalize(frames: Iterable[Image.Image]) -> list[Image.Image]:
    out = []
    for f in frames:
        if f.mode != "RGB":
            f = f.convert("RGB")
        out.append(f)
    if not out:
        raise ValueError("frames must be a non-empty iterable")
    return out


def filmstrip(
    frames: Union[list[Image.Image], Iterable[Image.Image]],
    labels: bool = True,
    *,
    pad: int = 8,
    border: int = 2,
    canvas_bg: tuple[int, int, int] = (24, 24, 24),
) -> Image.Image:
    """把 N 帧合成到一张图。

    网格策略:
      - N == 4 -> 2 rows x 2 cols
      - 其余 N  -> 1 row x N cols（横向）
    布局:
      - 每帧缩放至 cell 目标尺寸内（保持长宽比，黑底居中）
      - cell 间 pad 像素黑缝
      - 整张图最外侧 border 像素白边
      - labels=True 时每帧左上角画白底黑字序号

    返回 RGB 的 PIL.Image。复合图最长边不超过 1536px。
    """
    norm = _normalize(frames)
    n = len(norm)
    rows, cols = (2, 2) if n == 4 else (1, n)

    cell_w, cell_h = _cell_target_size(n, cols)

    canvas_w = cols * cell_w + (cols + 1) * pad + 2 * border
    canvas_h = rows * cell_h + (rows + 1) * pad + 2 * border

    # 外圈白边
    canvas = Image.new("RGB", (canvas_w + 2 * border, canvas_h + 2 * border), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    # 内画布（黑底放格子）
    inner = Image.new("RGB", (canvas_w, canvas_h), canvas_bg)
    inner_draw = ImageDraw.Draw(inner)

    font = None
    label_box_h = 0
    label_box_w = 0
    if labels:
        font_size = max(20, cell_w // 10)
        font = _load_font(font_size)

    for idx, fr in enumerate(norm):
        # resize 至 cell 尺寸（保持长宽比，用 thumbnail 内含 LANCZOS）
        cell = fr.copy()
        cell.thumbnail((cell_w, cell_h), Image.LANCZOS)
        cw, ch = cell.size
        r, c = divmod(idx, cols)
        ox = border + pad + c * (cell_w + pad)
        oy = border + pad + r * (cell_h + pad)
        # 居中摆放
        px = ox + (cell_w - cw) // 2
        py = oy + (cell_h - ch) // 2
        inner.paste(cell, (px, py))

        if labels:
            label = str(idx + 1)
            # 用 draw.textlength（10.0+）拿宽度，bbox 拿高度
            try:
                tw = int(round(inner_draw.textlength(label, font=font)))
                l, t, r_b, b = font.getbbox(label)
                th = b - t
                ascent = -t
            except AttributeError:
                tw, th = 24, 24
                ascent = 0
            pad_l = max(6, cell_w // 40)
            box_w = tw + pad_l * 2
            box_h = th + pad_l * 2
            box_x = ox + 4
            box_y = oy + 4
            # 白底
            inner_draw.rectangle(
                [box_x, box_y, box_x + box_w, box_y + box_h],
                fill=(255, 255, 255),
                outline=(0, 0, 0),
                width=1,
            )
            # 黑字
            inner_draw.text(
                (box_x + pad_l, box_y + pad_l - ascent),
                label,
                fill=(0, 0, 0),
                font=font,
            )

    canvas.paste(inner, (border, border))

    # 兜底：长边 ≤ 1536
    canvas.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.LANCZOS)
    return canvas


def _compress_to_kb(
    img: Image.Image,
    target_kb: int = DEFAULT_TARGET_KB,
    *,
    max_passes: int = 8,
) -> bytes:
    """把 PIL 图 JPEG 压到 ≤ target_kb。返回 JPEG 字节。"""
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
        # 双向收敛：降低 quality，同时降分辨率
        if quality > 50:
            quality -= 10
        else:
            img = img.copy()
            img.thumbnail((int(img.width * 0.85), int(img.height * 0.85)), Image.LANCZOS)
    assert best is not None
    return best


def filmstrip_b64(
    frames: Union[list[Image.Image], Iterable[Image.Image]],
    labels: bool = True,
    *,
    target_kb: int = DEFAULT_TARGET_KB,
    prefix: bool = True,
) -> str:
    """合成 filmstrip -> JPEG 压缩到 ~target_kb -> base64。

    prefix=True 时返回带 ``data:image/jpeg;base64,`` 前缀的字符串，
    可直接放进 OpenAI 兼容 messages 的 ``image_url.url`` 字段。
    """
    img = filmstrip(frames, labels=labels)
    raw = _compress_to_kb(img, target_kb=target_kb)
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}" if prefix else b64


def save_filmstrip(
    frames: Union[list[Image.Image], Iterable[Image.Image]],
    path: Union[str, Path],
    labels: bool = True,
) -> Path:
    """合成图存盘（PNG 无损或 JPEG）。返回写入路径。"""
    img = filmstrip(frames, labels=labels)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    suffix = p.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        raw = _compress_to_kb(img)
        p.write_bytes(raw)
    else:
        img.save(p, "PNG")
    return p


def _make_synthetic_frames(n: int = 4, base: int = 320) -> list[Image.Image]:
    """自测用：生成 n 张带颜色梯度和文字的合成帧，便于肉眼核对序号。"""
    import math

    frames: list[Image.Image] = []
    for i in range(n):
        w = h = base
        img = Image.new("RGB", (w, h), (i * 60 % 256, (i * 90 + 30) % 256, (i * 120 + 60) % 256))
        draw = ImageDraw.Draw(img)
        # 渐变条
        for y in range(h):
            r = (i * 30 + y) % 256
            g = (i * 50 + 2 * y) % 256
            b = (i * 70 + 3 * y) % 256
            draw.line([(0, y), (w, y)], fill=(r, g, b))
        # 几何标记
        cx, cy = w // 2, h // 2
        r0 = min(w, h) // 3
        for k in range(8):
            ang = 2 * math.pi * k / 8 + i * 0.5
            x2 = cx + int(r0 * math.cos(ang))
            y2 = cy + int(r0 * math.sin(ang))
            draw.line([(cx, cy), (x2, y2)], fill=(255, 255, 255), width=2)
        # 占位文字：肉眼可分辨的帧标识
        try:
            f = _load_font(40)
            tag = f"Frame #{i + 1}"
            draw.text((10, h - 50), tag, fill=(255, 255, 255), font=f)
        except Exception:  # noqa: BLE001
            pass
        frames.append(img)
    return frames


def self_test(out_dir: Path | None = None) -> dict:
    """最小自测：4 张合成图 -> filmstrip -> 保存 PNG/JPEG -> 报尺寸/字节。

    返回 dict 便于上层/PROBE 解析（"ok" / counts / bytes）。
    """
    here = Path(__file__).resolve().parent.parent
    out_dir = out_dir or (here / "output" / "tmp")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 4 张 -> 2x2 网格
    frames4 = _make_synthetic_frames(4)
    fs4 = filmstrip(frames4, labels=True)
    png_path = save_filmstrip(frames4, out_dir / "filmstrip_2x2.png", labels=True)
    b64 = filmstrip_b64(frames4, labels=True, target_kb=200, prefix=False)
    jpg_path = out_dir / "filmstrip_2x2.jpg"
    jpg_path.write_bytes(_compress_to_kb(fs4, target_kb=200))

    # 2) 3 张 -> 1x3 横向
    frames3 = _make_synthetic_frames(3)
    fs3 = filmstrip(frames3, labels=True)
    png3_path = save_filmstrip(frames3, out_dir / "filmstrip_1x3.png", labels=True)

    # 3) 6 张 -> 1x6 横向
    frames6 = _make_synthetic_frames(6)
    fs6 = filmstrip(frames6, labels=True)
    png6_path = save_filmstrip(frames6, out_dir / "filmstrip_1x6.png", labels=True)

    return {
        "ok": True,
        "out_dir": str(out_dir),
        "n4_canvas": fs4.size,
        "n4_png_bytes": png_path.stat().st_size,
        "n4_jpg_bytes": jpg_path.stat().st_size,
        "n4_b64_bytes": len(b64),
        "n3_canvas": fs3.size,
        "n3_png_bytes": png3_path.stat().st_size,
        "n6_canvas": fs6.size,
        "n6_png_bytes": png6_path.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    _ = argv
    # 既支持 python scripts/filmstrip.py 直接自测
    # 也支持 python scripts/filmstrip.py <out_dir> 指定输出目录
    if len(sys.argv) >= 2:
        out_dir = Path(sys.argv[1])
    else:
        out_dir = None
    result = self_test(out_dir)
    print(_json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
