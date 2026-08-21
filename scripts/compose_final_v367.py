#!/usr/bin/env python3
"""v3.6.7 合并成片: R2V 独立片段直接 concat + 统一 BGM 配音。

任务书 oc_task_v367.txt §4 §6:
- 拼接: 横屏 1344x576 / yuv420p / 24fps
- R2V 片段独立 (无链式), 直接 concat demuxer 即可
- BGM: 全部片段生成完成后, 统一铺 bgm_v32.wav (120BPM) 在整个 40s 成片上
- 不在每个片段单独配音 (否则音乐随片段硬切换, 破坏整体感)

vs compose_final_v366.py 关键差异:
- 输入 clips_v367/ (v367 r2v 生成的 6 段, 无需 dissolve)
- 输出 final_v36_60s_v367.mp4
- 拼接: 直接 concat (R2V 段间已有参考一致性, 不需要 xfade)

CLI:
    python compose_final_v367.py
    python compose_final_v367.py --with-dissolve
    python compose_final_v367.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_RAW_DIR = ROOT / "output" / "pipeline_v36" / "clips_v367"
DEFAULT_BGM = ROOT / "output" / "pipeline_v3" / "music" / "bgm_v32.wav"
DEFAULT_OUT = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v367.mp4"
DEFAULT_TMP = ROOT / "output" / "pipeline_v36" / "tmp" / "v367"
DEFAULT_RHYTHM = ROOT / "output" / "pipeline_v36" / "shots_v367" / "rhythm_plan_v367.json"

FPS = 24
RES_W = 1344
RES_H = 576

ACROSSFADE_SR = 44000
BG_FINAL_SR = 32000

SAFE_GAP_SEC = 0.06
EXPECTED_TOTAL_DUR_SEC = 40.0


def run(cmd: list[str], *, dry_run: bool = False, check: bool = True) -> str:
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[compose-v367] + {s}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed rc={r.returncode}")
    return s


def ffprobe_stream_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration,nb_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    if not lines:
        return 0.0
    try:
        return float(lines[0])
    except (ValueError, IndexError):
        return 0.0


def discover_shot_clips(clips_dir: Path) -> list[Path]:
    if not clips_dir.exists():
        return []
    return sorted(clips_dir.glob("shot*.mp4"),
                  key=lambda p: int(p.stem.replace("shot", "")))


def concat_all(clips: list[Path], out_path: Path,
               *, dry_run: bool = False) -> float:
    """直接 concat demuxer (R2V 段间已有参考一致性, 不需要 xfade)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".list.txt")
    with list_file.open("w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.as_posix()}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-vf", (f"scale={RES_W}:{RES_H}:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1:1,format=yuv420p"),
        "-an", "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-fflags", "+genpts",
        str(out_path),
    ]
    run(cmd, dry_run=dry_run)
    if dry_run:
        return sum(ffprobe_stream_duration(c) for c in clips)
    return ffprobe_stream_duration(out_path)


def mix_bgm(video_path: Path, bgm_path: Path, duration: float,
            out_path: Path, *, dry_run: bool = False) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bgm_filter = (
        f"[1:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
        f"aformat=sample_rates={BG_FINAL_SR}:channel_layouts=stereo,"
        f"volume=0.85[bgm];"
        f"[bgm]alimiter=limit=0.95,loudnorm=I=-16:TP=-1:LRA=11[aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(bgm_path),
        "-filter_complex", bgm_filter,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(BG_FINAL_SR),
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    return run(cmd, dry_run=dry_run)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips-dir", default=str(DEFAULT_RAW_DIR))
    ap.add_argument("--bgm", default=str(DEFAULT_BGM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tmp", default=str(DEFAULT_TMP))
    ap.add_argument("--rhythm-plan", default=str(DEFAULT_RHYTHM))
    ap.add_argument("--with-dissolve", action="store_true",
                    help="(备用) 段间 dissolve xfade")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    clips_dir = Path(args.clips_dir)
    bgm_path = Path(args.bgm) if args.bgm else None
    out_path = Path(args.out)
    tmp_dir = Path(args.tmp)

    tmp_dir.mkdir(parents=True, exist_ok=True)

    clip_paths = discover_shot_clips(clips_dir)
    if not clip_paths or len(clip_paths) < 6:
        print(f"ERROR: clips 不足 (need 6, got {len(clip_paths)})",
              file=sys.stderr)
        return 4

    durations = [round(ffprobe_stream_duration(p), 4) for p in clip_paths]
    print(f"[compose-v367] {len(clip_paths)} shots:", flush=True)
    for p, d in zip(clip_paths, durations):
        print(f"  {p.name} {d:.3f}s", flush=True)

    concat_v = tmp_dir / "concat_all.mp4"
    concat_dur = concat_all(clip_paths, concat_v, dry_run=args.dry_run)
    method = "concat_demuxer"
    print(f"[compose-v367] concat 完成: {concat_dur:.3f}s ({concat_v.name})",
          flush=True)

    if bgm_path and bgm_path.exists():
        bgm_cmd = mix_bgm(concat_v, bgm_path, concat_dur, out_path,
                          dry_run=args.dry_run)
    else:
        cmd = ["ffmpeg", "-y", "-i", str(concat_v), "-c:v", "copy",
               "-an", "-movflags", "+faststart", str(out_path)]
        bgm_cmd = run(cmd, dry_run=args.dry_run)
        print(f"[compose-v367] WARN: BGM not found, output is silent",
              file=sys.stderr)

    print(f"[compose-v367] final output -> {out_path}", flush=True)

    meta = {
        "compose_phase": "compose_final_v367",
        "pipeline_version": "v3.6.7",
        "clips_dir": str(clips_dir),
        "rhythm_plan_path": str(args.rhythm_plan),
        "input_paths": [str(p) for p in clip_paths],
        "segment_durations_sec": durations,
        "method": method,
        "with_dissolve": bool(args.with_dissolve),
        "safe_gap_sec": SAFE_GAP_SEC,
        "expected_total_dur_sec": EXPECTED_TOTAL_DUR_SEC,
        "actual_video_duration_sec": round(concat_dur, 4),
        "resolution": {"w": RES_W, "h": RES_H, "orientation": "horizontal_2_36_1"},
        "pix_fmt": "yuv420p",
        "fps": FPS,
        "output": str(out_path),
        "bgm_path": str(bgm_path) if bgm_path else None,
        "vs_v366_changes": {
            "method_r2v": True,  # 替代 v366 链式 i2v
            "no_chain_dependency": True,
            "unified_bgm_only_at_end": True,
        },
        "dry_run": bool(args.dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    suffix = "_dryrun" if args.dry_run else ""
    meta_path = out_path.parent / f"{out_path.stem}{suffix}_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[compose-v367] meta -> {meta_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
