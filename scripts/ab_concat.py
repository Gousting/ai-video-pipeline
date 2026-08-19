#!/usr/bin/env python3
"""A 组 8 段 → final_a.mp4 拼接（768x1344 → 720x1280, 0.25s acrossfade）。

策略：
- 视频：xfade 链，offset 累加 (8s - 0.25s)
- 音频：acrossfade 链，duration 0.25s
- 输出：720x1280, h264, aac, 62.25s ~ 60s

CLI:
  python ab_concat.py                # 拼接所有 8 段
  python ab_concat.py --out-dir <d>  # 指定输出目录
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
OUT_DIR = ROOT / "output" / "abtest"
OUT_FINAL = OUT_DIR / "final_a.mp4"
TMP_DIR = OUT_DIR / "tmp_concat"

N_SEGMENTS = 8
SHIFT_S = 8.0      # 每段时长
FADE_S = 0.25      # 段间转场
TARGET_W = 720
TARGET_H = 1280


def run(cmd: list[str], cwd: str | None = None) -> str:
    """执行命令并返回 stdout。"""
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:], flush=True)
        raise RuntimeError(f"命令失败 rc={r.returncode}: {' '.join(cmd[:3])}...")
    return r.stdout


def probe_duration(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(r.stdout.strip() or 0.0)


def concat_via_xfade(shots: list[Path], out: Path) -> None:
    """用 ffmpeg filter_complex 拼 8 段：scale + xfade + acrossfade。"""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    # 1) 先把每段 scale 到 720x1280 + 音视频重新编码成统一格式（避免 xfade 兼容问题）
    scaled = []
    for i, s in enumerate(shots, 1):
        sout = TMP_DIR / f"shot_{i:02d}_720x1280.mp4"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(s),
            "-vf", f"scale={TARGET_W}:{TARGET_H}:flags=lanczos,format=yuv420p",
            "-r", "24",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(sout),
        ]
        run(cmd)
        scaled.append(sout)
        print(f"[concat] scaled {s.name} -> {sout.name}", flush=True)

    # 2) 拼 xfade 链
    #    8 段 + 7 个 xfade
    #    video xfade offset = i * (SHIFT_S - FADE_S)  对 i=1..7
    #    audio acrossfade offset 一致
    n = len(scaled)
    inputs = []
    for s in scaled:
        inputs.extend(["-i", str(s)])

    # 视频 xfade 链
    v_filters = []
    a_filters = []
    for i in range(n - 1):
        offset = (SHIFT_S - FADE_S) * (i + 1)
        if i == 0:
            v_filters.append(
                f"[0:v][1:v]xfade=transition=fade:duration={FADE_S}:offset={offset:.3f}[v{i+1}]"
            )
            a_filters.append(
                f"[0:a][1:a]acrossfade=d={FADE_S}:c1=tri:c2=tri[a{i+1}]"
            )
        else:
            v_filters.append(
                f"[v{i}][{i+1}:v]xfade=transition=fade:duration={FADE_S}:offset={offset:.3f}[v{i+1}]"
            )
            a_filters.append(
                f"[a{i}][{i+1}:a]acrossfade=d={FADE_S}:c1=tri:c2=tri[a{i+1}]"
            )

    last_v = f"[v{n-1}]"
    last_a = f"[a{n-1}]"
    filter_complex = ";\n".join(v_filters + a_filters)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", last_v, "-map", last_a,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    print(f"[concat] xfade 链起跑 ({n} 输入, {n-1} xfade)...", flush=True)
    t0 = time.time()
    run(cmd)
    dt = time.time() - t0
    print(f"[concat] 完成 -> {out} ({out.stat().st_size / 1e6:.2f} MB) 耗时 {dt:.1f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = sorted(out_dir.glob("a_shot0*.mp4"))
    if len(shots) < N_SEGMENTS:
        print(f"ERROR: 只找到 {len(shots)} 段 (期望 {N_SEGMENTS})", file=sys.stderr)
        return 2

    out = out_dir / "final_a.mp4"
    concat_via_xfade(shots, out)

    # 报告
    final_dur = probe_duration(out)
    print(f"[concat] final_a.mp4 duration = {final_dur:.2f}s", flush=True)
    summary = {
        "output": str(out),
        "duration_sec": final_dur,
        "size_bytes": out.stat().st_size,
        "segments": len(shots),
        "seg_duration_sec": SHIFT_S,
        "fade_sec": FADE_S,
        "expected_total_sec": len(shots) * SHIFT_S - (len(shots) - 1) * FADE_S,
        "resolution": f"{TARGET_W}x{TARGET_H}",
        "video_codec": "h264",
        "audio_codec": "aac",
    }
    (out_dir / "concat_meta.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
