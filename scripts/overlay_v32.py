#!/usr/bin/env python3
"""v3.2 Overlay：intro/outro 标题卡（PIL 烧录到视频）。

设计：
- intro 卡（0-2s）：黑底 + "COLOR RIOT" + 副标题 "选学姐还是学妹？" + 霓虹粉边框
- outro 卡（58-60s）：黑底 + "THE CHOICE" + 副标题 "—— 你的答案 ——" + 霓虹青边框
- 不用 character label 角标（任务无此要求，简化）
- 不用 subtitle strip（任务要求零对白，无字幕）

CLI:
  python overlay_v32.py --video <concat.mp4> --out <video_with_overlay.mp4>
"""
import argparse
import subprocess
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(r"D:\ai-video-pipeline")
TMP = ROOT / "output" / "pipeline_v3" / "tmp"
OVERLAYS = ROOT / "output" / "pipeline_v3" / "overlays"

WIDTH = 720
HEIGHT = 1280


def find_cjk_font(size: int) -> ImageFont.FreeTypeFont:
    """找一个支持中文的字体。优先微软雅黑，回退 DejaVuSans。"""
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for fp in candidates:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_card(out_path: Path, *, title: str, subtitle: str,
                title_color: tuple, border_color: tuple,
                extra_text: str = "") -> Path:
    """渲染一张标题卡 PNG（全画面 720x1280）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    # 霓虹边框（4px 厚）
    border_w = 6
    margin = 32
    draw.rectangle([margin, margin, WIDTH - margin, HEIGHT - margin],
                   outline=border_color, width=border_w)

    # 内层装饰（半透明色块）
    inner_margin = margin + border_w + 16
    draw.rectangle([inner_margin, inner_margin, WIDTH - inner_margin, HEIGHT - inner_margin],
                   outline=(border_color[0], border_color[1], border_color[2], 100),
                   width=2)

    # 顶部装饰条（霓虹粉/青渐变）
    for x in range(margin + border_w + 4, WIDTH - margin - border_w - 4, 2):
        t = (x - margin) / (WIDTH - 2 * margin)
        r = int(255 * (1 - t))
        g = int(50 * t)
        b = int(255 * t)
        draw.line([(x, inner_margin + 8), (x, inner_margin + 16)],
                  fill=(r, g, b, 255))

    # 标题（主）
    title_font = find_cjk_font(72)
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (WIDTH - tw) // 2
    ty = HEIGHT // 2 - th - 80
    draw.text((tx, ty), title, font=title_font, fill=title_color)

    # 副标题（小）
    sub_font = find_cjk_font(36)
    bbox2 = draw.textbbox((0, 0), subtitle, font=sub_font)
    sw = bbox2[2] - bbox2[0]
    sh = bbox2[3] - bbox2[1]
    sx = (WIDTH - sw) // 2
    sy = ty + th + 30
    draw.text((sx, sy), subtitle, font=sub_font, fill=(220, 220, 220, 255))

    # 副副标题（最下，额外信息）
    if extra_text:
        extra_font = find_cjk_font(28)
        bbox3 = draw.textbbox((0, 0), extra_text, font=extra_font)
        ew = bbox3[2] - bbox3[0]
        eh = bbox3[3] - bbox3[1]
        ex = (WIDTH - ew) // 2
        ey = sy + sh + 40
        draw.text((ex, ey), extra_text, font=extra_font, fill=(180, 180, 180, 255))

    # 底部小字（制作信息）
    bottom_font = find_cjk_font(20)
    bottom_text = "ai-video-pipeline v3.2  ·  MiniMax H3  ·  cel-shading MV"
    bbox4 = draw.textbbox((0, 0), bottom_text, font=bottom_font)
    bw = bbox4[2] - bbox4[0]
    bx = (WIDTH - bw) // 2
    by = HEIGHT - 80
    draw.text((bx, by), bottom_text, font=bottom_font, fill=(120, 120, 120, 255))

    img.save(out_path, "PNG")
    return out_path


def render_watermark(out_path: Path) -> Path:
    """渲染小水印（用于全片叠加，右下角）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 右下角小水印
    wm_font = find_cjk_font(22)
    wm_text = "v3.2 · MiniMax H3"
    bbox = draw.textbbox((0, 0), wm_text, font=wm_font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = WIDTH - w - 32
    y = HEIGHT - h - 32
    # 半透明背景
    draw.rectangle([x - 8, y - 4, x + w + 8, y + h + 4],
                   fill=(0, 0, 0, 150))
    draw.text((x, y), wm_text, font=wm_font, fill=(255, 200, 255, 255))
    img.save(out_path, "PNG")
    return out_path


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg 失败 rc={r.returncode}")
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--storyboard", default=str(ROOT / "output/pipeline_v3/sb/storyboard_v32.json"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    video_path = Path(args.video)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 渲染 intro/outro 卡
    intro_png = OVERLAYS / "intro_card_v32.png"
    outro_png = OVERLAYS / "outro_card_v32.png"
    OVERLAYS.mkdir(parents=True, exist_ok=True)

    render_card(intro_png, title="COLOR RIOT", subtitle="选学姐还是学妹？",
                extra_text="Mai Yoneyama cel-shading MV",
                title_color=(255, 80, 200, 255),   # magenta
                border_color=(255, 50, 200, 255))
    print(f"[overlay] intro_card -> {intro_png}", flush=True)

    render_card(outro_png, title="THE CHOICE", subtitle="—— 你的答案 ——",
                extra_text="ai-video-pipeline v3.2  ·  100% T2V  ·  0 Z-Image",
                title_color=(80, 230, 255, 255),   # cyan
                border_color=(80, 200, 255, 255))
    print(f"[overlay] outro_card -> {outro_png}", flush=True)

    # 全片水印
    wm_png = OVERLAYS / "watermark_v32.png"
    render_watermark(wm_png)
    print(f"[overlay] watermark -> {wm_png}", flush=True)

    # 用 ffmpeg 烧录到视频：intro 0-4s, outro 56-60s
    intro_dur = 4.0
    outro_start = 56.0
    outro_dur = 4.0

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(intro_png),
        "-i", str(outro_png),
        "-filter_complex",
        f"[1:v]format=rgba,fade=t=in:st=0:d=0.6:alpha=1,fade=t=out:st={intro_dur - 0.6}:d=0.6:alpha=1[ic];"
        f"[2:v]format=rgba,fade=t=in:st=0:d=0.6:alpha=1,fade=t=out:st={outro_dur - 0.6}:d=0.6:alpha=1[oc];"
        f"[0:v][ic]overlay=enable='between(t,0,{intro_dur})'[v1];"
        f"[v1][oc]overlay=enable='between(t,{outro_start},{outro_start + outro_dur})'[vout]",
        "-map", "[vout]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run(cmd)
    print(f"[overlay] video_with_overlay -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
