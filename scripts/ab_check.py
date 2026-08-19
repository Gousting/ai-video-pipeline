#!/usr/bin/env python3
"""抽 N 帧 + 合成 filmstrip -> 落盘 + 报尺寸，便于肉眼/VLM 自查。

CLI:
  python ab_check.py --video <mp4> --out-dir <dir> --n 6
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from filmstrip import save_filmstrip  # noqa: E402


def extract_frames(video: Path, n: int) -> list[Image.Image]:
    """均匀抽 n 帧。"""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(probe.stdout.strip() or 8.0)
    ts = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    for i, t in enumerate(ts):
        out = video.with_suffix(f".tmp_check_{i}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True, encoding="utf-8", errors="replace", check=True,
        )
        frames.append(Image.open(out).convert("RGB"))
        out.unlink(missing_ok=True)
    return frames


def probe(video: Path) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration:stream=width,height,r_frame_rate,codec_name",
         "-of", "default=noprint_wrappers=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    info = {}
    for line in p.stdout.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--n", type=int, default=6)
    args = ap.parse_args()

    v = Path(args.video)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = v.stem

    info = probe(v)
    print(f"[check] {v.name}: {info}", flush=True)

    frames = extract_frames(v, args.n)
    fs_path = out / f"{name}_filmstrip_{args.n}.jpg"
    save_filmstrip(frames, fs_path, labels=True)
    print(f"[check] filmstrip: {fs_path} ({fs_path.stat().st_size} bytes)", flush=True)

    # 单独保存每帧便于详细查看
    for i, fr in enumerate(frames):
        f_path = out / f"{name}_frame_{i+1:02d}.jpg"
        fr.save(f_path, "JPEG", quality=85)
    print(f"[check] {len(frames)} 帧单独保存 -> {out}/{name}_frame_*.jpg", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
