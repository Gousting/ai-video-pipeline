#!/usr/bin/env python3
"""same_v1 迭代 v2：升级 overlay 视觉 + 替换黑色 narration 卡为渐变背景。

改进：
- title-card / end-card：增加大字 + 副标 + 装饰元素 + 角色 tag
- subtitle-bar：字号 56→80，bar_ratio 0.16→0.20，加 outline 描边更显眼
- shot 1/9/17/19 占位视频：从纯黑替换为渐变背景（紫红 / 粉橙 / 暖橙 / 灰白）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overlay import PilProvider  # noqa: E402

SAME_DIR = Path(r"D:\ai-video-pipeline\output\same_v1")
OVERLAYS_DIR = SAME_DIR / "overlays"
CLIPS_DIR = SAME_DIR / "clips"


def render_card_v2(out: Path, *, w: int, h: int, tmpl: str, data: dict) -> Path:
    """V2 风格 title-card / end-card：渐变背景 + 大字 + accent bar + 角色 tag。"""
    from PIL import Image, ImageDraw, ImageFont
    bg_dark = (15, 8, 25)
    bg_light = (250, 245, 240)
    fg_dark = (255, 255, 255)
    fg_light = (20, 20, 25)
    accent_pink = (255, 90, 160)
    accent_orange = (255, 140, 80)
    if tmpl == "title-card":
        bg = bg_dark
        fg = fg_dark
        accent = accent_pink
    else:
        bg = bg_light
        fg = fg_light
        accent = accent_orange

    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    # 渐变背景（垂直）
    for y in range(h):
        t = y / h
        if tmpl == "title-card":
            r = int(bg[0] * (1 - t * 0.3) + 25 * t * 0.3)
            g = int(bg[1] * (1 - t * 0.3) + 8 * t * 0.3)
            b = int(bg[2] * (1 - t * 0.5) + 40 * t * 0.5)
        else:
            r = int(bg[0] * (1 - t * 0.3) + 245 * t * 0.3)
            g = int(bg[1] * (1 - t * 0.3) + 240 * t * 0.3)
            b = int(bg[2] * (1 - t * 0.3) + 230 * t * 0.3)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    def _font(size: int):
        for fp in (r"C:\Windows\Fonts\msyh.ttc",
                   r"C:\Windows\Fonts\simhei.ttf",
                   r"C:\Windows\Fonts\arial.ttf"):
            if Path(fp).is_file():
                try:
                    return ImageFont.truetype(fp, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    title = data.get("title", "")
    subtitle = data.get("subtitle", "")
    credit = data.get("credit", "")
    tagline = data.get("tagline", "")

    # 角色 tag（左上角 / 右上角）
    if tmpl == "title-card":
        # 左侧「学姐」tag
        tag_font = _font(int(h * 0.035))
        for i, (tag, color) in enumerate([("学 姐", accent_pink), ("学 妹", accent_orange)]):
            x = int(w * 0.08) + i * int(w * 0.36)
            y = int(h * 0.08)
            # 圆角矩形背景
            pad = int(h * 0.012)
            bbox = draw.textbbox((0, 0), tag, font=tag_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.rectangle([(x - pad, y - pad), (x + tw + pad, y + th + pad * 2)],
                           fill=color)
            draw.text((x, y), tag, font=tag_font, fill=(0, 0, 0))

    # accent bar 左侧
    bar_w = int(w * 0.04)
    bar_h = int(h * 0.025)
    draw.rectangle([(int(w*0.08), int(h*0.32)), (int(w*0.08)+bar_w, int(h*0.32)+bar_h*4)], fill=accent)

    # 主标题（更大）
    title_font = _font(int(h * 0.085))
    max_w = int(w * 0.78)
    lines = []
    cur = ""
    for ch in title:
        cand = cur + ch
        bbox = draw.textbbox((0, 0), cand, font=title_font)
        if bbox[2] - bbox[0] <= max_w:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    line_h = int(h * 0.10)
    total_h = line_h * len(lines)
    y_start = int(h * 0.42) - total_h // 2
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        tw = bbox[2] - bbox[0]
        x = int((w - tw) / 2)
        # outline 描边（黑色/白色）
        outline_color = (0, 0, 0) if tmpl == "title-card" else (255, 255, 255)
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
            draw.text((x + dx, y_start + i * line_h), line, font=title_font, fill=outline_color)
        draw.text((x, y_start + i * line_h), line, font=title_font, fill=fg)

    if subtitle:
        sub_font = _font(int(h * 0.035))
        bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        tw = bbox[2] - bbox[0]
        x = int((w - tw) / 2)
        sub_color = (200, 200, 200) if tmpl == "title-card" else (80, 80, 80)
        draw.text((x, y_start + total_h + 24), subtitle, font=sub_font, fill=sub_color)

    if tagline:
        tg_font = _font(int(h * 0.025))
        bbox = draw.textbbox((0, 0), tagline, font=tg_font)
        tw = bbox[2] - bbox[0]
        x = int((w - tw) / 2)
        tg_color = (150, 150, 150) if tmpl == "title-card" else (120, 120, 120)
        draw.text((x, int(h * 0.86)), tagline, font=tg_font, fill=tg_color)

    if credit:
        cr_font = _font(int(h * 0.028))
        bbox = draw.textbbox((0, 0), credit, font=cr_font)
        tw = bbox[2] - bbox[0]
        x = int((w - tw) / 2)
        cr_color = (130, 130, 130) if tmpl == "title-card" else (90, 90, 90)
        draw.text((x, int(h * 0.92)), credit, font=cr_font, fill=cr_color)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


def render_subtitle_v2(provider: PilProvider, composition: dict, text: str, out: Path) -> Path:
    """V2 风格 subtitle：字号 80，bar_ratio 0.22，加 outline。"""
    # 自定义 data
    composition = dict(composition)
    return provider.render(
        composition,
        {"text": text, "font_size": 84, "bar_ratio": 0.22,
         "text_color": (255, 255, 255, 255), "bg_color": (0, 0, 0, 200)},
        out,
    )


def replace_black_clips() -> None:
    """把 shot01/09/17/19 的纯黑占位视频换成渐变背景视频（紫红/粉橙/暖橙/灰白）。"""
    grads = {
        "shot01": {"top": (30, 10, 50), "bot": (90, 30, 90)},   # 紫红
        "shot09": {"top": (50, 20, 80), "bot": (130, 40, 110)},  # 深紫粉
        "shot17": {"top": (90, 50, 30), "bot": (200, 130, 80)},  # 暖橙
        "shot19": {"top": (220, 220, 215), "bot": (245, 240, 235)},  # 灰白
    }
    for stem, grad in grads.items():
        p = CLIPS_DIR / f"{stem}.mp4"
        # 读原时长
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        dur = float(r.stdout.strip() or 2.0)
        # 生成渐变视频
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", (f"gradients=size=720x1280:duration={dur}:speed=0.005:c0=0x{grad['top'][0]:02x}{grad['top'][1]:02x}{grad['top'][2]:02x}"
                   f":c1=0x{grad['bot'][0]:02x}{grad['bot'][1]:02x}{grad['bot'][2]:02x}:nb_colors=2"),
            "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-an", "-r", "24", str(p),
        ]
        r2 = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if r2.returncode != 0:
            print(f"  FAIL {stem}: {r2.stderr[-500:]}", flush=True)
        else:
            print(f"  {stem}.mp4 -> 渐变视频 ({dur}s)", flush=True)
        # 加静音轨（compose_final 需要）
        tmp = p.with_suffix(".tmp.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(p),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=32000",
            "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            str(tmp),
        ], capture_output=True, encoding="utf-8", errors="replace")
        if tmp.is_file():
            tmp.replace(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="same_v1 迭代 v2")
    ap.add_argument("--same-dir", default=str(SAME_DIR))
    args = ap.parse_args(argv)

    same_dir = Path(args.same_dir)
    overlays_dir = same_dir / "overlays"
    clips_dir = same_dir / "clips"

    sb = json.loads((same_dir / "storyboard_v1.json").read_text(encoding="utf-8"))
    composition = {
        "width": int(sb.get("composition", {}).get("width", 720)),
        "height": int(sb.get("composition", {}).get("height", 1280)),
        "duration": float(sb.get("total_duration", 60.0)),
    }

    # 1) 升级 title-card / end-card
    print("[1/3] 升级 title-card / end-card ...", flush=True)
    for overlay in sb.get("overlays", []):
        oid = overlay.get("id", "")
        tmpl = overlay.get("template", "")
        data = overlay.get("data", {})
        out = overlays_dir / f"{oid}.png"
        if oid in ("title-card", "end-card"):
            render_card_v2(out, w=composition["width"], h=composition["height"],
                           tmpl=tmpl, data=data)
            print(f"  {oid}.png -> OK ({out.stat().st_size} bytes)", flush=True)

    # 2) 升级 subtitle-bar
    print("\n[2/3] 升级 subtitle-bar (字号 84, bar_ratio 0.22) ...", flush=True)
    provider = PilProvider()
    for shot in sb.get("shots", []):
        idx = shot.get("index")
        text = shot.get("narration", "")
        if not text:
            continue
        out = overlays_dir / f"shot-{int(idx):02d}-subtitle-bar.png"
        try:
            render_subtitle_v2(provider, composition, text, out)
        except Exception as e:  # noqa: BLE001
            print(f"  shot_{idx:02d} FAIL: {e}", flush=True)
            continue
        print(f"  shot-{idx:02d}-subtitle-bar OK text='{text[:25]}...'", flush=True)

    # 3) 替换黑色 narration 卡为渐变视频
    print("\n[3/3] 替换黑色占位视频为渐变背景 ...", flush=True)
    replace_black_clips()

    print("\n=== 完成 ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
