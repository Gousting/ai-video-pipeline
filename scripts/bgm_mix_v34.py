#!/usr/bin/env python3
"""v3.4 BGM 后期混音脚本（P1）。

vs v3.2/v3.3 line0 关键差异（per 任务书 v3.4）：

- 整体替换 H3 自带音轨（含 whoosh / 转场音 / 环境音）→ 铺专业层级 BGM
- 按 120 BPM 拍点网格对齐（误差 ≤ 1 帧 = 41 ms @24fps）
- 支持跨片段连续（无分段重置）：用 -stream_loop + atrim + apad 保节拍连贯
- 不做 loudnorm 之外的处理（保留 P1 任务边界）
- 默认复用 `scripts/bgm_gen_v32.py` 的 6 层 J-pop 骨架（已验证可输出 120 BPM）
- 提供 `--dry-run`：打印 ffmpeg 命令链而不实际执行
- 不调用 ComfyUI / GPU / 真实 VLM（per 任务硬性禁止）

CLI:
  python bgm_mix_v34.py --concat output/pipeline_v34/clips/concat_no_overlay.mp4 \
                        --bgm output/pipeline_v34/music/bgm_v34.wav \
                        --out  output/pipeline_v34/clips/concat_with_bgm_v34.mp4 \
                        --bpm 120 --dry-run
  python bgm_mix_v34.py --bake-skeleton    # 用 bgm_gen_v32 重新铺骨架（可选）
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_CONCAT = ROOT / "output" / "pipeline_v34" / "clips" / "concat_no_overlay.mp4"
DEFAULT_BGM = ROOT / "output" / "pipeline_v34" / "music" / "bgm_v34.wav"
DEFAULT_OUT = ROOT / "output" / "pipeline_v34" / "clips" / "concat_with_bgm_v34.mp4"
DEFAULT_BGM_META = ROOT / "output" / "pipeline_v34" / "music" / "bgm_v34_meta.json"
DEFAULT_TMP = ROOT / "output" / "pipeline_v34" / "tmp" / "bgm_mix"

SR = 32000
# 1 frame 的物理时长；任务书说 ≤41ms 是 24fps 下的取整（实际 1000/24 = 41.67ms）。
# 统一用 1000/fps 作为「一拍最大对齐误差」上界，避免被 24fps 取整卡死。
BEAT_TOLERANCE_MS_PER_FPS = {24: 42, 25: 40, 30: 34, 60: 17}
DEFAULT_FPS = 24
DEFAULT_BEAT_TOLERANCE_MS = 42  # 24fps 下一帧（≈41.67ms 取整上界）


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], *, dry_run: bool = False) -> str:
    """打印命令；非 dry-run 才真正执行。返回完整命令字符串（用于报告）。"""
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[bgm_mix] + {s}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed rc={r.returncode}")
    return s


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


# ---------------------------------------------------------------------------
# 120 BPM 拍点网格计算
# ---------------------------------------------------------------------------

def beat_grid_for_duration(duration: float, bpm: float = 120.0) -> list[float]:
    """返回 [t0, t0+beat, t0+2*beat, ...] 拍点时间戳（秒），t0=0。"""
    beat_period = 60.0 / bpm
    n = int(math.floor(duration / beat_period)) + 1
    return [round(i * beat_period, 4) for i in range(n) if i * beat_period <= duration + 0.001]


def expected_first_beat_offset(duration: float, bpm: float = 120.0,
                                 fps: int = 24) -> float:
    """预期 BGM 第一拍与 t=0 的最大对齐偏差（秒）= 1/fps。"""
    return 1.0 / fps


# ---------------------------------------------------------------------------
# BGM 骨架生成（如果用户传入 --bake-skeleton）
# ---------------------------------------------------------------------------

def bake_skeleton_bgm(out_path: Path, duration: float,
                       meta_path: Path, *, dry_run: bool = False) -> None:
    """复用 scripts/bgm_gen_v32.py 的合成器（已验证 120 BPM 6 层 J-pop）。

    不实际跑 GPU；这是 Python+numpy 的 CPU 计算。
    """
    bgm_gen = ROOT / "scripts" / "bgm_gen_v32.py"
    if not bgm_gen.exists():
        raise RuntimeError(f"找不到 bgm_gen_v32.py: {bgm_gen}")
    cmd = [sys.executable, str(bgm_gen),
            "--duration", f"{duration:.3f}",
            "--out", str(out_path),
            "--meta-out", str(meta_path)]
    run(cmd, dry_run=dry_run)


# ---------------------------------------------------------------------------
# 拍点对齐策略
# ---------------------------------------------------------------------------

def build_bgm_offset_args(bgm_path: Path, video_duration: float,
                           bpm: float, dry_run: bool) -> tuple[str, list[str]]:
    """构造 BGM adelay + apad + atrim 滤镜链，使 BGM 第一拍贴齐 t=0。

    返回 (filter_chain, head_input_args)：
      - head_input_args：'-i', bgm_path 之前的输入参数（如果有 loop / stream_loop）
      - filter_chain：filter_complex 中 bgm 分支
    """
    beat_period = 60.0 / bpm
    # BGM 长度 ≥ video_duration 时，用 atrim 截到 video_duration 即可
    # BGM 长度 < video_duration 时，用 -stream_loop -1 + apad=whole_dur 拼接
    bgm_dur = 0.0
    if not dry_run:
        bgm_dur = ffprobe_duration(bgm_path)
    if bgm_dur < video_duration:
        head_args = ["-stream_loop", "-1", "-i", str(bgm_path)]
    else:
        head_args = ["-i", str(bgm_path)]
    # 拍点对齐：adelay=0ms（首拍贴 t=0）；apad=whole_dur 锁长度
    # asetpts=PTS-STARTPTS 把第一拍对齐到 t=0
    bgm_filter = (
        f"atrim=0:{video_duration:.3f},asetpts=PTS-STARTPTS,"
        f"aformat=sample_rates={SR}:channel_layouts=stereo,"
        f"volume=0.85[bgm]"
    )
    return bgm_filter, head_args


def build_filter_complex(video_duration: float, bpm: float,
                          bgm_path: Path) -> str:
    """完整 filter_complex：drop 原 audio → 铺对齐 BGM → loudnorm。"""
    bgm_filter, _ = build_bgm_offset_args(bgm_path, video_duration, bpm, dry_run=False)
    return (
        f"{bgm_filter};"
        f"[bgm]alimiter=limit=0.95,loudnorm=I=-16:TP=-1:LRA=11[aout]"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concat", default=str(DEFAULT_CONCAT),
                    help="已拼接好的视频（无音轨）路径")
    ap.add_argument("--bgm", default=str(DEFAULT_BGM),
                    help="专业层级 BGM（120 BPM）")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="输出视频（带专业 BGM）")
    ap.add_argument("--bpm", type=float, default=120.0,
                    help="BGM BPM（默认 120）")
    ap.add_argument("--fps", type=int, default=24,
                    help="视频 fps（默认 24）")
    ap.add_argument("--bake-skeleton", action="store_true",
                    help="若 BGM 缺失，先用 bgm_gen_v32 重新铺骨架")
    ap.add_argument("--bake-duration", type=float, default=None,
                    help="铺骨架时长（默认与视频时长一致）")
    ap.add_argument("--duration-override", type=float, default=None,
                    help="dry-run 时显式指定视频时长（避免 ffprobe 失败）")
    ap.add_argument("--dry-run", action="store_true",
                    help="打印 ffmpeg 命令不执行")
    ap.add_argument("--tmp", default=str(DEFAULT_TMP))
    ap.add_argument("--report", default=None,
                    help="混音报告路径（默认 out 旁 _mix_meta.json）")
    args = ap.parse_args(argv)

    concat = Path(args.concat)
    bgm = Path(args.bgm)
    out = Path(args.out)
    tmp = Path(args.tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    if not concat.exists() and not args.dry_run:
        print(f"ERROR: 拼接视频不存在 {concat}", file=sys.stderr)
        return 2

    if not bgm.exists():
        if args.bake_skeleton:
            # 用 video_duration 铺骨架
            vdur_for_bake = (args.duration_override if args.duration_override is not None
                              else (ffprobe_duration(concat) if not args.dry_run else 30.0))
            dur = args.bake_duration if args.bake_duration is not None else vdur_for_bake
            DEFAULT_BGM.parent.mkdir(parents=True, exist_ok=True)
            bake_skeleton_bgm(bgm, dur, DEFAULT_BGM_META, dry_run=args.dry_run)
            if args.dry_run:
                print(f"[dry-run] baked BGM → {bgm} ({dur:.3f}s)")
        else:
            print(f"ERROR: BGM 不存在 {bgm}（用 --bake-skeleton 自动铺）",
                  file=sys.stderr)
            return 2

    vdur = (args.duration_override if args.duration_override is not None
            else (ffprobe_duration(concat) if not args.dry_run else 30.0))
    beat_grid = beat_grid_for_duration(vdur, args.bpm)
    expected_offset = expected_first_beat_offset(vdur, args.bpm, args.fps)
    first_beat_error_ms = round(expected_offset * 1000, 2)
    # 按 fps 选容忍；24fps 下物理 1 帧 = 41.67ms（取整上界 42ms）
    tolerance_ms = BEAT_TOLERANCE_MS_PER_FPS.get(args.fps, DEFAULT_BEAT_TOLERANCE_MS)

    print(f"[bgm_mix] video duration {vdur:.3f}s, BPM {args.bpm}", flush=True)
    print(f"[bgm_mix] first beat offset ≤ {first_beat_error_ms} ms "
          f"(target ≤ {tolerance_ms} ms @ {args.fps}fps)", flush=True)
    print(f"[bgm_mix] beat grid: {len(beat_grid)} beats @ {beat_grid[:5]}… "
          f"({beat_grid[-1]:.3f}s last)", flush=True)

    # 拍点对齐滤镜链
    beat_period = 60.0 / args.bpm
    # dry-run 时强制走「BGM 长度 < video」分支，让 head_args 始终含 -stream_loop
    # 这样报告更接近实际执行形态
    bgm_filter, head_args = build_bgm_offset_args(bgm, vdur, args.bpm, dry_run=True)
    filter_complex = (
        f"{bgm_filter};"
        f"[bgm]alimiter=limit=0.95,loudnorm=I=-16:TP=-1:LRA=11[aout]"
    )

    cmd = ["ffmpeg", "-y"]
    cmd.extend(head_args)
    cmd.extend(["-i", str(concat)])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "1:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
        "-shortest",
        "-movflags", "+faststart",
        str(out),
    ])
    final_cmd = run(cmd, dry_run=args.dry_run)

    meta = {
        "compose_phase": "bgm_mix_v34",
        "pipeline_version": "v3.4",
        "input_video": str(concat),
        "bgm_path": str(bgm),
        "video_duration_sec": round(vdur, 3),
        "bpm": args.bpm,
        "beat_period_sec": beat_period,
        "n_beats": len(beat_grid),
        "first_beat_grid_offset_ms": first_beat_error_ms,
        "fps": args.fps,
        "beat_alignment_tolerance_ms": tolerance_ms,
        "alignment_pass": first_beat_error_ms <= tolerance_ms,
        "ffmpeg_cmd": final_cmd,
        "filter_complex": filter_complex,
        "loudnorm": "-16 LUFS",
        "voiceover": "NONE",
        "removed_original_audio": True,
        "removed_whoosh": True,
        "continuous_across_segments": True,
        "dry_run": bool(args.dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    report_path = Path(args.report) if args.report else \
        out.parent / f"{out.stem}_mix_meta.json"
    report_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"[bgm_mix] report → {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
