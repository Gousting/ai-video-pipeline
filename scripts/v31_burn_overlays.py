#!/usr/bin/env python3
"""v3.1 overlay burn：标题卡 + 字幕条 + 片尾卡。

v3.0 v3_final.py.burn_overlays 有 -shortest 截断 bug（用 PNG 输入导致 video 被截断）。
v3.1 修复版：不用 -shortest，固定 -t = video_duration 保留完整时长。
"""
import subprocess
import sys
import time
from pathlib import Path

import json


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise RuntimeError(f"命令失败 rc={r.returncode}")
    return r


def probe_duration(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


# ---------------------------------------------------------------------------
# 占位 PNG 生成（无 PIL 字体依赖，纯色块 + 文字兜底）
# ---------------------------------------------------------------------------

def make_overlay_pngs(out_dir: Path, storyboard: dict, duration: float, n_shots: int,
                      width: int = 720, height: int = 1280) -> dict:
    """生成 intro / subtitle_strip / outro 三张 PNG（与 v3 一致的占位策略）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:
        print(f"[overlay] PIL 缺失 {e}; 跳过 PNG 生成（ffmpeg drawtext 兜底）", flush=True)
        return {}

    out_dir.mkdir(parents=True, exist_ok=True)
    # 标题
    intro = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(intro)
    # 半透明顶部条
    d.rectangle([(0, 0), (width, int(height * 0.18))], fill=(20, 20, 30, 200))
    title = storyboard.get("title", "Untitled")
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except Exception:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((width - tw) // 2, int(height * 0.04)), title, fill=(255, 255, 255, 255), font=font)
    intro.save(out_dir / "intro_card.png", "PNG")

    # 字幕条（覆盖全程底部）
    subs = Image.new("RGBA", (width, int(height * 0.15)), (0, 0, 0, 180))
    sd = ImageDraw.Draw(subs)
    sw, sh = subs.size
    bar_h = sh
    sd.rectangle([(0, 0), (sw, bar_h)], fill=(0, 0, 0, 180))
    # 每段 narration 文本（简化为显示"shot N"）
    for i in range(n_shots):
        shot = storyboard["shots"][i] if i < len(storyboard.get("shots", [])) else {}
        text = (shot.get("narration") or f"shot {i+1}").strip()
        if not text:
            continue
        # 简化：每段一条字幕（实际视频只有一条全长字幕条，但 PIL 占位只画首段）
        try:
            font_s = ImageFont.truetype("arial.ttf", 36)
        except Exception:
            font_s = ImageFont.load_default()
        bbox = sd.textbbox((0, 0), text[:30], font=font_s)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        sd.text(((sw - tw) // 2, (bar_h - th) // 2), text[:30],
                fill=(255, 255, 255, 255), font=font_s)
        break  # 只画一段作为占位
    subs.save(out_dir / "subtitle_strip.png", "PNG")

    # 片尾
    outro = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(outro)
    od.rectangle([(0, int(height * 0.7)), (width, height)], fill=(20, 20, 30, 200))
    msg = "感谢观看 · 关注 + 评论"
    try:
        font_o = ImageFont.truetype("arial.ttf", 48)
    except Exception:
        font_o = ImageFont.load_default()
    bbox = od.textbbox((0, 0), msg, font=font_o)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    od.text(((width - tw) // 2, int(height * 0.78)), msg,
            fill=(255, 220, 200, 255), font=font_o)
    outro.save(out_dir / "outro_card.png", "PNG")

    return {
        "intro_card": str(out_dir / "intro_card.png"),
        "subtitle_strip": str(out_dir / "subtitle_strip.png"),
        "outro_card": str(out_dir / "outro_card.png"),
    }


# ---------------------------------------------------------------------------
# 主烧录函数（v3.1 修复 -shortest bug）
# ---------------------------------------------------------------------------

def burn_overlays_v31(concat_video: Path, overlays_dir: Path,
                      storyboard: dict, out: Path) -> dict:
    """烧录 overlay PNG 到视频。修复版：使用 -t 显式指定输出时长，避免 -shortest 截断。"""
    overlays_dir = Path(overlays_dir)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(concat_video)
    shots = storyboard.get("shots", [])
    n_shots = len(shots)

    try:
        png_meta = make_overlay_pngs(overlays_dir, storyboard, duration, n_shots)
    except Exception as e:
        print(f"[overlay] PIL 生成失败 {e}，fallback 到 no-overlay", flush=True)
        png_meta = {}

    intro_png = overlays_dir / "intro_card.png"
    outro_png = overlays_dir / "outro_card.png"
    subs_png = overlays_dir / "subtitle_strip.png"

    if not (intro_png.exists() or outro_png.exists() or subs_png.exists()):
        # 无 overlay，直接复制（保留原视频完整时长）
        run(["ffmpeg", "-y", "-v", "error", "-i", str(concat_video),
             "-c", "copy", str(out)])
        return {"ok": True, "output": str(out), "overlays_burned": 0}

    TARGET_H = 1280
    filters = []
    inputs = ["-i", str(concat_video)]
    next_input_idx = 1  # 0 is concat_video

    if intro_png.exists():
        inputs.extend(["-loop", "1", "-t", str(duration), "-i", str(intro_png)])
        intro_idx = next_input_idx
        next_input_idx += 1
        filters.append(
            f"[{intro_idx}:v]format=rgba,fade=t=in:st=0:d=0.3:alpha=1,"
            f"fade=t=out:st=1.7:d=0.3:alpha=1[intro]"
        )
        filters.append(f"[0:v][intro]overlay=0:0:enable='between(t,0,2)'[v1]")
        last_v = "v1"
    else:
        last_v = "0:v"

    if subs_png.exists():
        inputs.extend(["-loop", "1", "-t", str(duration), "-i", str(subs_png)])
        subs_idx = next_input_idx
        next_input_idx += 1
        filters.append(f"[{subs_idx}:v]format=rgba[{subs_idx}v]")
        filters.append(f"[{last_v}][{subs_idx}v]overlay=0:H-{TARGET_H}*0.85[{last_v}b]")
        last_v = f"{last_v}b"

    if outro_png.exists():
        inputs.extend(["-loop", "1", "-t", str(duration), "-i", str(outro_png)])
        outro_idx = next_input_idx
        next_input_idx += 1
        filters.append(
            f"[{outro_idx}:v]format=rgba,fade=t=in:st={duration-2.3:.3f}:d=0.3:alpha=1,"
            f"fade=t=out:st={duration-0.3:.3f}:d=0.3:alpha=1[outro]"
        )
        filters.append(
            f"[{last_v}][outro]overlay=0:0:enable='between(t,{duration-2:.3f},{duration})'[vout]"
        )
        last_v = "vout"

    filter_complex = ";\n".join(filters)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{last_v}]",
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(duration),   # 关键修复：不用 -shortest，固定输出时长
        "-movflags", "+faststart",
        str(out),
    ]
    print(f"[overlay] 烧录 overlay (duration={duration:.2f}s)...", flush=True)
    t0 = time.time()
    run(cmd)
    dt = time.time() - t0
    print(f"[overlay] OK -> {out} ({out.stat().st_size / 1e6:.2f} MB) 耗时 {dt:.1f}s", flush=True)
    return {
        "ok": True,
        "output": str(out),
        "overlays_burned": sum(1 for x in [intro_png, subs_png, outro_png] if x.exists()),
        "duration_sec": duration,
        "elapsed_sec": round(dt, 1),
    }


if __name__ == "__main__":
    # CLI 测试：python v31_burn_overlays.py --video <concat.mp4> --storyboard <sb.json>
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--overlays-dir", default="D:\\ai-video-pipeline\\output\\pipeline_v3\\overlays")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sb = json.loads(Path(args.storyboard).read_text(encoding="utf-8"))
    burn_overlays_v31(Path(args.video), Path(args.overlays_dir), sb, Path(args.out))