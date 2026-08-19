#!/usr/bin/env python3
"""v3 衍生：混合 4 个 base 视频（senior_base + senior_closeup + junior_base + junior_closeup）。

提升镜头语言（VLM v2 反馈的核心短板）：每个 shot 选最合适的 base。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CLIPS_DIR = Path(r"D:\ai-video-pipeline\output\same_v1\clips")


def make_shot(*, base: Path, out: Path, duration: float = 3.5,
              crop: str | None = None, scale: int = 720, h: int = 1280,
              zoom: float = 1.0, h_off: int = 0, v_off: int = 0,
              rotate_deg: float = 0.0) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if crop is None and zoom == 1.0 and rotate_deg == 0.0:
        vf = f"loop=loop=-1:size=1,trim=0:{duration},setpts=N/(24*TB),scale={scale}:{h}"
    else:
        bw, bh = 720, 1280
        if zoom > 1.0:
            cw = int(bw / zoom)
            ch = int(bh / zoom)
            cx = (bw - cw) // 2 + h_off
            cy = (bh - ch) // 2 + v_off
            crop_expr = f"crop={cw}:{ch}:{cx}:{cy}"
        else:
            crop_expr = "crop=ih*9/16:ih:0:0" if scale != bw else "crop=720:1280:0:0"
        rot = f",rotate={rotate_deg}*PI/180" if rotate_deg else ""
        vf = (f"loop=loop=-1:size=1,trim=0:{duration},setpts=N/(24*TB),"
              f"{crop_expr},scale={scale}:{h}{rot}")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(base),
        "-vf", vf,
        "-an", "-r", "24",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败 rc={r.returncode} stderr: {r.stderr[-500:]}")


# v3 镜头分配：用 senior_closeup 替代部分 shot 增加变化
# 每行 (shot_idx, base_name, duration, crop_w, crop_h, crop_x, crop_y, zoom, rotate)
SENIOR_SHOTS = [
    (2,  "senior_base",     3.5, None, None, None, None, 1.0, 0.0),
    (3,  "senior_closeup",  3.5, None, None, None, None, 1.0, 0.0),   # 用 closeup base
    (4,  "senior_base",     3.5, 540, 800, 180, 200, 1.5, 0.0),
    (5,  "senior_closeup",  3.5, 360, 500, 180, 350, 2.5, 0.0),      # closeup 拉近眼睛
    (6,  "senior_base",     3.5, None, None, None, None, 1.0, 0.0),
    (7,  "senior_closeup",  3.5, 540, 360, 90, 900, 1.4, 0.0),     # closeup 拉近配饰
    (8,  "senior_base",     3.5, None, None, None, None, 1.0, 2.0),  # 微旋转 2°
]
JUNIOR_SHOTS = [
    (10, "junior_base",     3.5, None, None, None, None, 1.0, 0.0),
    (11, "junior_closeup",  3.5, None, None, None, None, 1.0, 0.0),  # 用 closeup base
    (12, "junior_base",     3.5, 540, 800, 180, 200, 1.5, 0.0),
    (13, "junior_closeup",  3.5, 360, 500, 180, 350, 2.5, 0.0),
    (14, "junior_base",     3.5, None, None, None, None, 1.0, 0.0),
    (15, "junior_closeup",  3.5, 540, 360, 90, 900, 1.4, 0.0),
    (16, "junior_base",     3.5, None, None, None, None, 1.0, -2.0),
]


def main():
    bases = {
        "senior_base": CLIPS_DIR / "senior_base.mp4",
        "senior_closeup": CLIPS_DIR / "senior_closeup.mp4",
        "junior_base": CLIPS_DIR / "junior_base.mp4",
        "junior_closeup": CLIPS_DIR / "junior_closeup.mp4",
    }
    for n, p in bases.items():
        if not p.is_file():
            print(f"ERROR: {n} 不存在 {p}", file=sys.stderr)
            return 1
        print(f"  {n}: {p.stat().st_size} bytes", flush=True)

    print("\n=== Senior 7 镜头 (含 closeup) ===", flush=True)
    for idx, base_name, dur, cw, ch, cx, cy, zoom, rot in SENIOR_SHOTS:
        out = CLIPS_DIR / f"shot{idx:02d}.mp4"
        try:
            make_shot(base=bases[base_name], out=out, duration=dur,
                      crop=f"{cw}:{ch}:{cx}:{cy}" if cw else None,
                      zoom=zoom, rotate_deg=rot)
            print(f"  shot_{idx:02d} <- {base_name} zoom={zoom} rot={rot}° -> OK {out.stat().st_size} bytes", flush=True)
        except RuntimeError as e:
            print(f"  shot_{idx:02d} FAIL: {e}", flush=True)

    print("\n=== Junior 7 镜头 (含 closeup) ===", flush=True)
    for idx, base_name, dur, cw, ch, cx, cy, zoom, rot in JUNIOR_SHOTS:
        out = CLIPS_DIR / f"shot{idx:02d}.mp4"
        try:
            make_shot(base=bases[base_name], out=out, duration=dur,
                      crop=f"{cw}:{ch}:{cx}:{cy}" if cw else None,
                      zoom=zoom, rotate_deg=rot)
            print(f"  shot_{idx:02d} <- {base_name} zoom={zoom} rot={rot}° -> OK {out.stat().st_size} bytes", flush=True)
        except RuntimeError as e:
            print(f"  shot_{idx:02d} FAIL: {e}", flush=True)

    # 重新加静音轨
    print("\n=== 加静音轨 ===", flush=True)
    for shot in sorted(CLIPS_DIR.glob("shot*.mp4")):
        tmp = shot.with_suffix(".tmp.mp4")
        r = subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(shot),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=32000",
            "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            str(tmp),
        ], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            tmp.replace(shot)
    print("  done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
