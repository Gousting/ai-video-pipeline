#!/usr/bin/env python3
"""从 R2V 基础视频生成多镜头变体（ffmpeg crop/zoom/loop）。

为每个 shot 生成对应变体：
- 全长：3.5s = 84 帧（24fps）
- 基础视频 2s = 49 帧，先 loop 再 trim
- 不同景别用 ffmpeg crop + scale 实现

shot 编号对应 storyboard_v1.json：
  2-8:   senior（7 镜头）
  10-16: junior（7 镜头）
  1/9/17/18/19: 标题/字幕卡，不生成视频（用 overlay）

CLI:
    python scripts/derive_shots.py --same-dir D:/ai-video-pipeline/output/same_v1
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run_ffmpeg(args: list[str], *, timeout: int = 120) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败 rc={proc.returncode}\nstderr: {proc.stderr[-1500:]}")


def make_shot(*, base: Path, out: Path, duration: float = 3.5,
              crop: str | None = None, scale: int = 720, h: int = 1280,
              zoom: float = 1.0, h_off: int = 0, v_off: int = 0) -> None:
    """从一个基础 mp4 生成一个镜头变体。

    Args:
        base: 基础视频（49 帧 / 2s）
        out: 输出 mp4
        duration: 输出时长（秒）
        crop: ffmpeg crop 表达式，如 "576:768:72:256"（宽:高:x:y）
        scale: 输出宽度
        h: 输出高度
        zoom: zoom 因子（>1 拉近，<1 拉远）
        h_off: crop 水平偏移
        v_off: crop 垂直偏移
    """
    out.parent.mkdir(parents=True, exist_ok=True)

    # crop + scale + loop
    # base 是 720x1280 9:16
    if crop is None and zoom == 1.0:
        vf = f"loop=loop=-1:size=1,trim=0:{duration},setpts=N/(24*TB),scale={scale}:{h}"
    else:
        # 先 zoom（拉近用 crop）
        bw, bh = 720, 1280
        if zoom > 1.0:
            # 取中心区域，缩小 crop 范围 = 拉近
            cw = int(bw / zoom)
            ch = int(bh / zoom)
            cx = (bw - cw) // 2 + h_off
            cy = (bh - ch) // 2 + v_off
            crop_expr = f"crop={cw}:{ch}:{cx}:{cy}"
        else:
            # 缩小后黑边
            cw = bw
            ch = bh
            crop_expr = f"crop={cw}:{ch}:0:0"
        vf = f"loop=loop=-1:size=1,trim=0:{duration},setpts=N/(24*TB),{crop_expr},scale={scale}:{h}"

    cmd = [
        "-i", str(base),
        "-vf", vf,
        "-an",
        "-r", "24",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        str(out),
    ]
    run_ffmpeg(cmd, timeout=180)


# Senior shots 2-8 的 crop 参数（9:16 portrait，基础视频也是 720x1280）
SENIOR_SHOTS = [
    # (index, duration, crop_w, crop_h, crop_x, crop_y, zoom, label)
    (2,  3.5, None, None, None, None, 1.0, "full body standing"),       # 全身
    (3,  3.5, 720, 800,  0, 200, 1.4, "half body bust"),                # 半身
    (4,  3.5, 540, 800, 180, 200, 1.5, "side profile"),                  # 侧脸
    (5,  3.5, 360, 500, 180, 350, 2.5, "extreme eye close-up"),          # 眼睛
    (6,  3.5, None, None, None, None, 1.0, "ID card full"),                # ID卡
    (7,  3.5, 540, 360, 90, 900, 1.4, "skull detail"),                   # 配饰特写
    (8,  3.5, None, None, None, None, 1.0, "dynamic pose"),               # 动态姿势
]

JUNIOR_SHOTS = [
    (10, 3.5, None, None, None, None, 1.0, "full body standing"),
    (11, 3.5, 720, 800,  0, 200, 1.4, "half body bust"),
    (12, 3.5, 540, 800, 180, 200, 1.5, "side profile"),
    (13, 3.5, 360, 500, 180, 350, 2.5, "extreme eye close-up"),
    (14, 3.5, None, None, None, None, 1.0, "ID card full"),
    (15, 3.5, 540, 360, 90, 900, 1.4, "teddy charm detail"),
    (16, 3.5, None, None, None, None, 1.0, "energetic jump"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从基础视频衍生多镜头")
    ap.add_argument("--same-dir", required=True, help="output/same_v1 目录")
    args = ap.parse_args(argv)

    root = Path(args.same_dir)
    clips_dir = root / "clips"
    senior_base = clips_dir / "senior_base.mp4"
    junior_base = clips_dir / "junior_base.mp4"

    if not senior_base.is_file():
        print(f"ERROR: {senior_base} 不存在", file=sys.stderr)
        return 2
    if not junior_base.is_file():
        print(f"ERROR: {junior_base} 不存在", file=sys.stderr)
        return 2

    print(f"=== 生成 senior 7 个镜头变体 ===", flush=True)
    for idx, dur, cw, ch, cx, cy, zoom, label in SENIOR_SHOTS:
        out = clips_dir / f"shot{idx:02d}.mp4"
        print(f"  shot_{idx} ({label}) duration={dur}s zoom={zoom} -> {out.name}", flush=True)
        try:
            make_shot(
                base=senior_base, out=out, duration=dur,
                crop=f"{cw}:{ch}:{cx}:{cy}" if cw else None,
                zoom=zoom,
            )
            print(f"    OK {out.stat().st_size} bytes", flush=True)
        except RuntimeError as e:
            print(f"    FAIL: {e}", flush=True)

    print(f"\n=== 生成 junior 7 个镜头变体 ===", flush=True)
    for idx, dur, cw, ch, cx, cy, zoom, label in JUNIOR_SHOTS:
        out = clips_dir / f"shot{idx:02d}.mp4"
        print(f"  shot_{idx} ({label}) duration={dur}s zoom={zoom} -> {out.name}", flush=True)
        try:
            make_shot(
                base=junior_base, out=out, duration=dur,
                crop=f"{cw}:{ch}:{cx}:{cy}" if cw else None,
                zoom=zoom,
            )
            print(f"    OK {out.stat().st_size} bytes", flush=True)
        except RuntimeError as e:
            print(f"    FAIL: {e}", flush=True)

    # 检查产物
    print("\n=== 镜头产物清单 ===", flush=True)
    for shot in sorted(clips_dir.glob("shot*.mp4")):
        size = shot.stat().st_size
        print(f"  {shot.name}  {size} bytes", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
