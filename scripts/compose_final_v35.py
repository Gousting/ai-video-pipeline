#!/usr/bin/env python3
"""v3.5 合并成片：ffmpeg xfade 真实视觉转场（per 任务书 v3.5）。

vs compose_final_v34.py 关键差异：

- 不再用 concat demuxer 硬拼；改用 ffmpeg **xfade filter** 在每个段间应用
  transition_planner_v35 规划的视觉转场（hard_cut / dissolve / fadeblack /
  fade）
- xfade offset **累积式**：offset[i] = sum(seg_dur[0:i]) + seg_dur[i] -
  xfade_d[i]，保证每一段不被截断（参考 v31_concat.py 累积逻辑）
- 音频链 acrossfade，44000Hz 采样率，stereo（参考既有实现）
- 支持 `--dry-run`：打印 ffmpeg 命令链不执行；用假 shot 路径占位让命令链完整

CLI:
  python compose_final_v35.py --dry-run
  python compose_final_v35.py --clips-dir output/pipeline_v35/clips \
                              --plan output/pipeline_v35/sb/transition_plan_v35.json \
                              --bgm output/pipeline_v35/music/bgm.wav \
                              --out output/pipeline_v35/final_v35.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_CLIPS_DIR = ROOT / "output" / "pipeline_v35" / "clips"
DEFAULT_PLAN = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35.json"
DEFAULT_BGM = ROOT / "output" / "pipeline_v35" / "music" / "bgm_v35.wav"
DEFAULT_OUT = ROOT / "output" / "pipeline_v35" / "final_v35.mp4"

# v3.4 复用：32000 采样率 + loudnorm；任务书 v3.5 要求 audio 走 acrossfade 时
# 用 44000Hz（参考既有实现），保持 BGM 链路与 v34 等价（v34 链路最终输出仍是
# 32000Hz loudnorm；这里 44000 是 acrossfade 内部采样率）。
ACROSSFADE_SR = 44000
BG_FINAL_SR = 32000   # 最终成片音频采样率（与 v34 一致）
FPS = 24
RES = "1344:768"

# ffmpeg xfade transition 名称映射（仅 4 种 v35 允许的类型）
XFADE_NAME = {
    "hard_cut":  "fade",        # 极短 fade 等效硬切
    "dissolve":  "dissolve",
    "fadeblack": "fadeblack",
    "fade":      "fade",
}
MIN_XFADE_DUR = 0.001   # ffmpeg 不接受 0 时用极短时长替代


def run(cmd: list[str], *, dry_run: bool = False) -> str:
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[compose] + {s}", flush=True)
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


def discover_shot_clips(clips_dir: Path) -> list[Path]:
    """返回 shot01.mp4 .. shotNN.mp4 按 shot 号排序。"""
    if not clips_dir.exists():
        return []
    return sorted(clips_dir.glob("shot*.mp4"),
                   key=lambda p: p.stem)


def default_shot_durations(n_shots: int, seg_dur: float = 10.0) -> list[float]:
    """dry-run 时没有真实 mp4：返回 n_shots × seg_dur 默认时长。"""
    return [seg_dur] * n_shots


def build_xfade_filter_graph(
    n_inputs: int,
    segment_durations: list[float],
    transitions: list[dict],
) -> tuple[str, str, str, float]:
    """构造 ffmpeg filter_complex 字符串（视频 xfade + 音频 acrossfade）。

    返回 (filter_complex, out_v_label, out_a_label, total_output_duration_sec)
    """
    if n_inputs < 2:
        raise ValueError(f"n_inputs must be >= 2, got {n_inputs}")
    if len(transitions) != n_inputs - 1:
        raise ValueError(f"transitions len {len(transitions)} != n_inputs-1 {n_inputs - 1}")

    parts: list[str] = []

    # ---- 1) 每个输入先做格式/尺度归一 ----
    for i in range(n_inputs):
        parts.append(
            f"[{i}:v]format=yuv420p,scale={RES}:flags=lanczos,setsar=1:1,fps={FPS}[v{i}]"
        )
        parts.append(
            f"[{i}:a]aresample={ACROSSFADE_SR},aformat=sample_rates={ACROSSFADE_SR}:channel_layouts=stereo[a{i}]"
        )

    # ---- 2) 累积式 offset 计算 ----
    # offset[i] = cursor + segment_durations[i] - xfade_d[i]
    # cursor 推进 = segment_durations[i] + segment_durations[i+1] - xfade_d[i]
    cursor = 0.0
    xfade_durations = [max(MIN_XFADE_DUR if t["transition_type"] == "hard_cut" else t["duration"],
                            MIN_XFADE_DUR) for t in transitions]
    last_v = "[v0]"
    last_a = "[a0]"

    for i, trans in enumerate(transitions):
        xfade_d = xfade_durations[i]
        xfade_name = XFADE_NAME[trans["transition_type"]]
        # video offset = cursor + seg_dur[i] - xfade_d
        v_offset = round(cursor + segment_durations[i] - xfade_d, 4)
        # 音频 acrossfade 没有 offset 参数，d 与 xfade d 一致即可
        next_v = f"[v{i + 1}]"
        next_a = f"[a{i + 1}]"
        out_v = f"[vout{i}]"
        out_a = f"[aout{i}]"
        parts.append(
            f"{last_v}{next_v}xfade=transition={xfade_name}:"
            f"duration={xfade_d:.3f}:offset={v_offset:.3f}{out_v}"
        )
        parts.append(
            f"{last_a}{next_a}acrossfade=d={xfade_d:.3f}:c1=tri:c2=tri{out_a}"
        )
        last_v = out_v
        last_a = out_a
        cursor += segment_durations[i] + segment_durations[i + 1] - xfade_d

    # 总输出时长（不含最后一小段 audio tail，因为 acrossfade 末尾对齐）
    total_dur = cursor + segment_durations[-1] - xfade_durations[-1] if False else cursor
    # 修正：最后一轮 i = n_inputs - 2，cursor 已推进到包含 input n-2 + input n-1 之后
    # 但 input n-1 的尾部还没算。完整输出时长：
    total_dur = sum(segment_durations) - sum(xfade_durations)

    filter_complex = ";\n".join(parts)
    return filter_complex, last_v, last_a, total_dur


def build_full_cmd(
    input_paths: list[Path],
    segment_durations: list[float],
    transitions: list[dict],
    out_path: Path,
    bgm_path: Path | None,
    *,
    dry_run: bool = False,
) -> tuple[list[str], float, str]:
    """构造完整 ffmpeg 命令，返回 (cmd_list, output_duration_sec, filter_str)。"""
    n_inputs = len(input_paths)
    if n_inputs != len(segment_durations):
        raise ValueError(f"input_paths {n_inputs} != segment_durations {len(segment_durations)}")

    filter_complex, out_v, out_a, total_dur = build_xfade_filter_graph(
        n_inputs, segment_durations, transitions,
    )

    # BGM 链路：v34 范式 — drop 原 audio，铺 BGM，loudnorm
    if bgm_path is not None and bgm_path.exists():
        bgm_filter = (
            f"[{n_inputs}:a]atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates={BG_FINAL_SR}:channel_layouts=stereo,"
            f"volume=0.85[bgm];"
            f"[bgm]alimiter=limit=0.95,loudnorm=I=-16:TP=-1:LRA=11[aout]"
        )
        full_filter = filter_complex + ";\n" + bgm_filter
        cmd = ["ffmpeg", "-y"]
        for p in input_paths:
            cmd += ["-i", str(p)]
        cmd += ["-i", str(bgm_path)]
        cmd += [
            "-filter_complex", full_filter,
            "-map", out_v,
            "-map", "[aout]",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(BG_FINAL_SR),
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        # 无 BGM：直接用 acrossfade 输出的音频
        full_filter = filter_complex
        cmd = ["ffmpeg", "-y"]
        for p in input_paths:
            cmd += ["-i", str(p)]
        cmd += [
            "-filter_complex", full_filter,
            "-map", out_v,
            "-map", out_a,
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(ACROSSFADE_SR),
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(out_path),
        ]

    return cmd, total_dur, filter_complex


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips-dir", default=str(DEFAULT_CLIPS_DIR))
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--bgm", default=str(DEFAULT_BGM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--default-seg-dur", type=float, default=10.0,
                    help="dry-run 时没有真实 mp4 时的默认段时长")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    clips_dir = Path(args.clips_dir)
    plan_path = Path(args.plan)
    bgm_path = Path(args.bgm) if args.bgm else None
    out_path = Path(args.out)

    # 1) 读取 plan
    if not plan_path.exists():
        print(f"ERROR: plan 不存在 {plan_path}", file=sys.stderr)
        return 2
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    transitions = plan_data.get("transitions", [])
    if not transitions:
        print(f"ERROR: plan.transitions 为空", file=sys.stderr)
        return 3

    # 2) 找 shot clips
    clip_paths = discover_shot_clips(clips_dir)
    n_shots = len(transitions) + 1

    if clip_paths and len(clip_paths) >= n_shots:
        # 真实路径：用前 n_shots 个 mp4
        input_paths = clip_paths[:n_shots]
        durations: list[float] = []
        for p in input_paths:
            d = ffprobe_duration(p)
            durations.append(round(d, 4))
            print(f"  shot clip: {p.name} {d:.3f}s", flush=True)
    else:
        # dry-run 或 clips 不足：用 placeholder
        input_paths = [clips_dir / f"shot{i + 1:02d}.mp4" for i in range(n_shots)]
        durations = default_shot_durations(n_shots, args.default_seg_dur)
        if not args.dry_run:
            print(f"ERROR: clips_dir 无足够 mp4（need {n_shots}, got {len(clip_paths)}）",
                  file=sys.stderr)
            return 4

    # 3) 构造 ffmpeg 命令
    cmd, total_dur, filter_str = build_full_cmd(
        input_paths, durations, transitions, out_path,
        bgm_path if (bgm_path and bgm_path.exists()) else None,
        dry_run=args.dry_run,
    )

    print(f"[compose-v35] {n_shots} shots, {len(transitions)} transitions", flush=True)
    print(f"[compose-v35] transition_types: "
          f"{[t['transition_type'] for t in transitions]}", flush=True)
    print(f"[compose-v35] xfade durations: "
          f"{[t['duration'] for t in transitions]}", flush=True)
    print(f"[compose-v35] expected output duration: {total_dur:.3f}s", flush=True)
    print(f"[compose-v35] filter_complex:\n{filter_str}", flush=True)
    cmd_str = run(cmd, dry_run=args.dry_run)
    print(f"[compose-v35] ffmpeg command:\n  {cmd_str}", flush=True)

    # 4) 写 meta
    meta = {
        "compose_phase": "compose_final_v35",
        "pipeline_version": "v3.5",
        "clips_dir": str(clips_dir),
        "plan_path": str(plan_path),
        "input_paths": [str(p) for p in input_paths],
        "segment_durations_sec": durations,
        "transitions": [
            {"at_sec": t["at_sec"], "type": t["transition_type"],
             "duration": t["duration"], "reason": t["reason"]}
            for t in transitions
        ],
        "output": str(out_path),
        "expected_output_duration_sec": round(total_dur, 4),
        "ffmpeg_filter_complex": filter_str,
        "ffmpeg_cmd": cmd_str,
        "bgm_path": str(bgm_path) if bgm_path else None,
        "acrossfade_sr": ACROSSFADE_SR,
        "bg_final_sr": BG_FINAL_SR,
        "fps": FPS,
        "resolution": RES,
        "dry_run": bool(args.dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if not args.dry_run:
        meta_path = out_path.parent / f"{out_path.stem}_meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[compose-v35] report → {meta_path}", flush=True)
    else:
        # dry-run 也写一份方便查看
        meta_path = out_path.parent / f"{out_path.stem}_dryrun_meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[compose-v35] dry-run report → {meta_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
