#!/usr/bin/env python3
"""v3.6.4 合并成片：方案 B = 硬切 concat demuxer + dissolve xfade 留 MARGIN。

per 任务书 v364（oc_task_v364.txt）vs v363 关键差异：

- **输出改为竖屏 768x1344**（v363 是 1344x768 横屏，把竖屏源压扁，违规）
  → 所有 scale 目标 768:1344，竖屏 9:16 比例
- **pix_fmt 强制 yuv420p**（v363 是 yuv444p 不标准）
- **硬切段间 → concat demuxer**（沿用 v363 方案 B）
- **dissolve 段间 → xfade 留 0.06s MARGIN**（沿用 v363）
- 节奏按参考视频重构：6 段快慢呼吸（slow_open→build→fast→peak→fast→tail）
- 总时长 ≈ 48s (10+8+6+10+6+8)，比 v363 略短（v363 是 60s 平均分配）

CLI:
  python compose_final_v364.py --dry-run
  python compose_final_v364.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_SPED_DIR = ROOT / "output" / "pipeline_v36" / "clips_v364_trimmed"
DEFAULT_RAW_DIR = ROOT / "output" / "pipeline_v36" / "clips_v364"
DEFAULT_BGM = ROOT / "output" / "pipeline_v3" / "music" / "bgm_v32.wav"
DEFAULT_OUT = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v364.mp4"
DEFAULT_TMP = ROOT / "output" / "pipeline_v36" / "tmp" / "v364"
DEFAULT_RHYTHM = ROOT / "output" / "pipeline_v36" / "shots_v364" / "rhythm_plan_v364.json"

ACROSSFADE_SR = 44000
BG_FINAL_SR = 32000
FPS = 24
# **关键 v364 改动**：竖屏 768x1344（v363 是横屏 1344x768，压扁了竖屏源）
RES_W = 768
RES_H = 1344

# 任务书 §2.3 默认转场：3 硬切 + 2 dissolve
DEFAULT_TRANSITIONS = [
    {"type": "hard_cut", "duration": 0.0},
    {"type": "hard_cut", "duration": 0.0},
    {"type": "hard_cut", "duration": 0.0},
    {"type": "dissolve",  "duration": 0.3},
    {"type": "dissolve",  "duration": 0.4},
]
# 段时长（任务书 §2.2 重构版）：10+8+6+10+6+8 = 48s
SEGMENT_DURATIONS_SEC = (10.0, 8.0, 6.0, 10.0, 6.0, 8.0)

ALLOWED_TYPES = ("hard_cut", "dissolve", "fadeblack", "fade")
FANCY_TYPES = ("fadeblack", "fade")
PLAIN_TYPES = ("hard_cut", "dissolve")
FANCY_BUDGET_MAX = 2
MIN_XFADE_DUR = 0.001

XFADE_NAME = {
    "hard_cut": "fade",
    "dissolve": "dissolve",
    "fadeblack": "fadeblack",
    "fade": "fade",
}

# MARGIN（边距）：offset + xfade_d 必须严格 < (第一路流时长) - SAFE_GAP_SEC
# 1 帧 @ 24fps = 0.0417s；取 0.06s > 1 帧，更稳。
SAFE_GAP_SEC = 0.06
# 期望总时长（手算）：trim 0.5s × 6 shots = 3s 扣减
# (10.125 + 8 + 6.583 + 10.125 + 6.583 + 8) - 0.3 - 0.4 - 3.0 = 45.716s
# H3 输出时长略长于 target 是常见 rounding，按实际测得 ≈ 45.7s
TRIM_HEAD_SEC = 0.5  # 任务书 §3.3：消除 H3 源内容白淡入
EXPECTED_TOTAL_DUR_SEC = 45.7


def run(cmd: list[str], *, dry_run: bool = False, check: bool = True) -> str:
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[compose-v364] + {s}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed rc={r.returncode}")
    return s


def ffprobe_stream_duration(path: Path) -> float:
    """读 video stream 实际时长（**不**信 format.duration，可能被音频骗）。"""
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


def dedupe_fancy_transitions(transitions: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for t in transitions:
        tt = t.get("transition_type")
        new = dict(t)
        if tt in FANCY_TYPES:
            if tt in seen:
                new["transition_type"] = "hard_cut"
                new["duration"] = 0.0
                new["reason"] = (
                    f"{new.get('reason', '')} [v364 dedup: {tt} → hard_cut]"
                )
            else:
                seen.add(tt)
        out.append(new)
    return out


def enforce_fancy_budget(transitions: list[dict],
                          max_fancy: int = FANCY_BUDGET_MAX) -> list[dict]:
    fancy_count = sum(1 for t in transitions
                      if t.get("transition_type") in FANCY_TYPES)
    if fancy_count <= max_fancy:
        return transitions
    out: list[dict] = []
    budget = max_fancy
    for t in transitions:
        new = dict(t)
        if new.get("transition_type") in FANCY_TYPES:
            if budget <= 0:
                new["transition_type"] = "hard_cut"
                new["duration"] = 0.0
                new["reason"] = (
                    f"{new.get('reason', '')} "
                    f"[v364 budget: downgraded to hard_cut]"
                )
            else:
                budget -= 1
        out.append(new)
    return out


def group_consecutive_shots(transitions: list[dict],
                            n_shots: int) -> list[dict]:
    """把 N 段按 dissolve 切分成若干 group（每 group 内全 hard_cut）。"""
    groups: list[dict] = []
    cur_shots: list[int] = []
    for i in range(n_shots):
        cur_shots.append(i)
        if i == n_shots - 1:
            groups.append({
                "shot_indices": cur_shots,
                "transition_after": None,
                "i_transition": None,
            })
        else:
            tr = transitions[i]
            if tr["transition_type"] == "dissolve":
                groups.append({
                    "shot_indices": cur_shots,
                    "transition_after": {
                        "type": tr["transition_type"],
                        "duration": tr["duration"],
                        "i_transition": i,
                    },
                    "i_transition": i,
                })
                cur_shots = []
    return groups


def concat_group(clips: list[Path], out_path: Path,
                 *, dry_run: bool = False,
                 trim_head_sec: float = 0.5) -> float:
    """用 ffmpeg concat demuxer 拼一组 shot → out_path（无音轨）。

    返回 out_path 的 video stream duration。
    **v364 关键改动**：scale 目标改为 768:1344 竖屏，pix_fmt 强制 yuv420p。
    trim 由调用方在 pre-trim clips 中完成（参见 trim_shots_head）。
    """
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
        "-vf", (f"scale={RES_W}:{RES_H}:flags=lanczos:force_original_aspect_ratio=decrease,"
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


def trim_shots_head(src_dir: Path, dst_dir: Path, *,
                    trim_head_sec: float = 0.5,
                    dry_run: bool = False,
                    force: bool = False) -> list[Path]:
    """把 src_dir 下所有 shot*.mp4 头部 trim N 秒（去 H3 白淡入）→ dst_dir。

    返回 dst_dir 下的所有 trimmed shot clip 路径。
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    src_clips = sorted(src_dir.glob("shot*.mp4"),
                       key=lambda p: int(p.stem.replace("shot", "")))
    out_paths: list[Path] = []
    for src in src_clips:
        dst = dst_dir / src.name
        out_paths.append(dst)
        if not force and dst.exists() and dst.stat().st_size > 100_000:
            print(f"[compose-v364] trim 跳过 {dst.name} (已存在)",
                  flush=True)
            continue
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{trim_head_sec:.3f}",
            "-i", str(src),
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-an", "-r", str(FPS),
            "-pix_fmt", "yuv420p",
            "-vf", (f"scale={RES_W}:{RES_H}:flags=lanczos:"
                    f"force_original_aspect_ratio=decrease,"
                    f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"setsar=1:1,format=yuv420p"),
            "-movflags", "+faststart",
            "-fflags", "+genpts",
            str(dst),
        ]
        run(cmd, dry_run=dry_run)
        print(f"[compose-v364] trim {src.name} → {dst.name} "
              f"(head - {trim_head_sec}s)", flush=True)
    return out_paths


def xfade_two(left: Path, right: Path, out_path: Path,
              xfade_dur: float, xfade_name: str,
              offset: float, left_dur: float, right_dur: float,
              *, dry_run: bool = False) -> float:
    """用 ffmpeg xfade filter 把两段拼起来（视频），返回 out 时长。

    **v364 关键改动**：scale 目标改为 768:1344 竖屏，pix_fmt 强制 yuv420p。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[0:v]format=yuv420p,scale={RES_W}:{RES_H}:flags=lanczos:force_original_aspect_ratio=decrease,"
        f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1:1,fps={FPS}[lv];"
        f"[1:v]format=yuv420p,scale={RES_W}:{RES_H}:flags=lanczos:force_original_aspect_ratio=decrease,"
        f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1:1,fps={FPS}[rv];"
        f"[lv][rv]xfade=transition={xfade_name}:duration={xfade_dur:.3f}:"
        f"offset={offset:.3f}[vout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(left),
        "-i", str(right),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-an", "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-fflags", "+genpts",
        str(out_path),
    ]
    run(cmd, dry_run=dry_run)
    if dry_run:
        return round(left_dur + right_dur - xfade_dur, 4)
    return ffprobe_stream_duration(out_path)


def mix_bgm(video_path: Path, bgm_path: Path, duration: float,
            out_path: Path, *, dry_run: bool = False) -> str:
    """铺 BGM → out_path。"""
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
    ap.add_argument("--clips-dir", default=str(DEFAULT_RAW_DIR),
                    help="v364 H3 生成出来的 6 段 clip 目录 (默认 clips_v364/)")
    ap.add_argument("--bgm", default=str(DEFAULT_BGM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tmp", default=str(DEFAULT_TMP))
    ap.add_argument("--rhythm-plan", default=str(DEFAULT_RHYTHM))
    ap.add_argument("--safe-gap", type=float, default=SAFE_GAP_SEC,
                    help=f"xfade offset + duration < 流时长 - 安全余量 "
                         f"(默认 {SAFE_GAP_SEC}s)")
    ap.add_argument("--trim-head", type=float, default=TRIM_HEAD_SEC,
                    help=f"每段 shot 头部 trim 秒数（任务书 §3.3 消除白淡入，"
                         f"默认 {TRIM_HEAD_SEC}s）")
    ap.add_argument("--force-trim", action="store_true",
                    help="强制重新 trim 已存在的 trimmed clips")
    ap.add_argument("--fancy-budget", type=int, default=FANCY_BUDGET_MAX)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    clips_dir = Path(args.clips_dir)
    bgm_path = Path(args.bgm) if args.bgm else None
    out_path = Path(args.out)
    tmp_dir = Path(args.tmp)
    safe_gap = float(args.safe_gap)
    trim_head = float(args.trim_head)

    # **任务书 §3.3 强制指令**：H3 源内容首 ~0.5s 白淡入 → 先 trim 再 compose
    trimmed_dir = DEFAULT_SPED_DIR  # 默认 clips_v364_trimmed/
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    print(f"[compose-v364] Step 0: trim shots head {trim_head}s "
          f"→ {trimmed_dir}", flush=True)
    trim_shots_head(clips_dir, trimmed_dir,
                    trim_head_sec=trim_head,
                    dry_run=args.dry_run,
                    force=args.force_trim)
    clips_dir = trimmed_dir  # 后续 compose 用 trimmed clips

    # ---- 转场计划（任务书 §2.3：3 硬切 + 2 dissolve）----
    transitions = [
        {"transition_type": t["type"], "duration": t["duration"],
         "reason": "v364 default per oc_task §2.3"}
        for t in DEFAULT_TRANSITIONS
    ]
    transitions_v36 = dedupe_fancy_transitions(transitions)
    transitions_v36 = enforce_fancy_budget(
        transitions_v36, max_fancy=args.fancy_budget,
    )

    n_shots = len(transitions_v36) + 1
    clip_paths = discover_shot_clips(clips_dir)
    if not clip_paths or len(clip_paths) < n_shots:
        print(f"ERROR: clips 不足 (need {n_shots}, got {len(clip_paths)})",
              file=sys.stderr)
        return 4
    input_paths = clip_paths[:n_shots]
    durations = [ffprobe_stream_duration(p) for p in input_paths]
    durations = [round(d, 4) for d in durations]
    print(f"[compose-v364] {n_shots} shots, {len(transitions_v36)} transitions",
          flush=True)
    for p, d in zip(input_paths, durations):
        print(f"  sped clip: {p.name} {d:.3f}s", flush=True)
    print(f"[compose-v364] transition_types (after v364 dedupe+budget): "
          f"{[t['transition_type'] for t in transitions_v36]}", flush=True)

    tmp_dir.mkdir(parents=True, exist_ok=True)

    # ---- Step 1: 按 dissolve 分组，每组内 concat demuxer ----
    groups = group_consecutive_shots(transitions_v36, n_shots)
    print(f"[compose-v364] groups: "
          f"{[(g['shot_indices'], g['transition_after']) for g in groups]}",
          flush=True)

    group_paths: list[Path] = []
    group_durations: list[float] = []
    for gi, g in enumerate(groups):
        g_clips = [input_paths[i] for i in g["shot_indices"]]
        g_out = tmp_dir / f"group_{gi:02d}.mp4"
        if args.dry_run:
            g_dur = sum(durations[i] for i in g["shot_indices"])
        else:
            g_dur = concat_group(g_clips, g_out, dry_run=args.dry_run)
        group_paths.append(g_out)
        group_durations.append(round(g_dur, 4))
        print(f"[compose-v364] group {gi}: shots={[i+1 for i in g['shot_indices']]} "
              f"duration={g_dur:.3f}s → {g_out.name}", flush=True)

    # ---- Step 2: 把各组用 xfade（仅 dissolve 边界）串起来 ----
    current = group_paths[0]
    current_dur = group_durations[0]
    xfade_specs: list[dict] = []
    offsets_used: list[float] = []

    for gi in range(1, len(groups)):
        next_group = group_paths[gi]
        tr_after = groups[gi - 1]["transition_after"]
        if tr_after is None or tr_after["type"] == "hard_cut":
            # 直接 concat（demuxer），零 xfade 边界风险
            list_file = tmp_dir / f"chain_{gi:02d}.list.txt"
            with list_file.open("w", encoding="utf-8") as f:
                f.write(f"file '{current.as_posix()}'\n")
                f.write(f"file '{next_group.as_posix()}'\n")
            chain_out = tmp_dir / f"chain_{gi:02d}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c:v", "copy",
                "-an",
                "-movflags", "+faststart",
                str(chain_out),
            ]
            run(cmd, dry_run=args.dry_run)
            chain_dur = current_dur + group_durations[gi]
            xfade_specs.append({
                "between": f"group_{gi-1:02d} → group_{gi:02d}",
                "method": "concat_demuxer",
                "xfade_dur": 0.0,
                "offset": 0.0,
                "type": "hard_cut",
                "input1_dur": current_dur,
                "input2_dur": group_durations[gi],
                "output_dur": round(chain_dur, 4),
            })
            current = chain_out
            current_dur = chain_dur
            offsets_used.append(0.0)
        else:
            # dissolve：xfade + MARGIN 边距
            xfade_d = max(tr_after["duration"], MIN_XFADE_DUR)
            xfade_name = XFADE_NAME[tr_after["type"]]
            # **核心安全约束**：offset + xfade_d < current_dur - safe_gap
            offset = current_dur - xfade_d - safe_gap
            # 断言安全（绝对不许碰边界）
            assert offset + xfade_d < current_dur - 0.05, (
                f"unsafe offset: offset+xfade_d={offset + xfade_d:.4f} "
                f"not < current_dur - 0.05 = {current_dur - 0.05:.4f}"
            )
            chain_out = tmp_dir / f"chain_{gi:02d}.mp4"
            chain_dur = xfade_two(current, next_group, chain_out,
                                  xfade_d, xfade_name, offset,
                                  current_dur, group_durations[gi],
                                  dry_run=args.dry_run)
            xfade_specs.append({
                "between": f"group_{gi-1:02d} → group_{gi:02d}",
                "method": "xfade_with_margin",
                "xfade_dur": round(xfade_d, 4),
                "offset": round(offset, 4),
                "type": tr_after["type"],
                "xfade_name": xfade_name,
                "input1_dur": round(current_dur, 4),
                "input2_dur": round(group_durations[gi], 4),
                "offset_plus_xfade_d": round(offset + xfade_d, 4),
                "input1_dur_minus_safe_gap": round(current_dur - safe_gap, 4),
                "safety_asserted":
                    offset + xfade_d < current_dur - 0.05,
                "output_dur": round(chain_dur, 4),
            })
            current = chain_out
            current_dur = chain_dur
            offsets_used.append(round(offset, 4))

    final_video = current
    final_video_dur = current_dur
    print(f"[compose-v364] xfade chain complete: final video duration = "
          f"{final_video_dur:.3f}s", flush=True)

    # ---- Step 3: 铺 BGM ----
    if bgm_path and bgm_path.exists():
        bgm_cmd = mix_bgm(final_video, bgm_path, final_video_dur, out_path,
                          dry_run=args.dry_run)
    else:
        cmd = ["ffmpeg", "-y", "-i", str(final_video), "-c:v", "copy",
               "-an", "-movflags", "+faststart", str(out_path)]
        bgm_cmd = run(cmd, dry_run=args.dry_run)
        print(f"[compose-v364] WARN: BGM not found, output is silent",
              file=sys.stderr)

    print(f"[compose-v364] final output → {out_path}", flush=True)

    # ---- Step 4: 写 meta ----
    fancy_used = sum(1 for t in transitions_v36
                     if t["transition_type"] in FANCY_TYPES)
    meta = {
        "compose_phase": "compose_final_v364",
        "pipeline_version": "v3.6.4",
        "clips_dir": str(clips_dir),
        "rhythm_plan_path": str(args.rhythm_plan),
        "input_paths": [str(p) for p in input_paths],
        "segment_durations_sec": durations,
        "target_segment_durations_sec": list(SEGMENT_DURATIONS_SEC),
        "transitions_after_v364_governance": [
            {"type": t.get("transition_type"), "duration": t.get("duration"),
             "reason": t.get("reason")}
            for t in transitions_v36
        ],
        "groups": [
            {
                "i": gi,
                "shot_indices": g["shot_indices"],
                "transition_after": g["transition_after"],
                "out_path": str(group_paths[gi]),
                "duration": group_durations[gi],
            }
            for gi, g in enumerate(groups)
        ],
        "xfade_specs": xfade_specs,
        "offsets": offsets_used,
        "safe_gap_sec": safe_gap,
        "fancy_used": fancy_used,
        "fancy_budget": args.fancy_budget,
        "expected_total_dur_sec": EXPECTED_TOTAL_DUR_SEC,
        "actual_video_duration_sec": round(final_video_dur, 4),
        "resolution": {"w": RES_W, "h": RES_H, "orientation": "portrait_9_16"},
        "pix_fmt": "yuv420p",
        "fps": FPS,
        "output": str(out_path),
        "bgm_path": str(bgm_path) if bgm_path else None,
        "ffmpeg_cmds": [
            {"step": f"group_{gi:02d}_concat",
             "cmd": f"concat_demuxer → {group_paths[gi].name}",
             "out_dur": group_durations[gi]}
            for gi in range(len(groups))
        ] + [
            {"step": f"chain_{i+1:02d}", "spec": xfade_specs[i]}
            for i in range(len(xfade_specs))
        ] + [
            {"step": "bgm_mix", "cmd": bgm_cmd},
        ],
        "vs_v363_changes": {
            "resolution": "v363: 1344x768 (横屏, 压扁源) → v364: 768x1344 (竖屏)",
            "pix_fmt": "v363: yuv444p (非标) → v364: yuv420p (标准)",
            "rhythm": "v363: 6 段均匀 10s → v364: 10+8+6+10+6+8 = 48s 快慢呼吸",
            "characters": "v363: senior=黑发/junior=棕发 → v364: xuejie=棕发/xuemei=黑发 (per task §3.2)",
            "prompts_rebuilt": True,
            "t2v_regenerated": True,
            "first_frame_yavg_check_added": True,
        },
        "dry_run": bool(args.dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    suffix = "_dryrun" if args.dry_run else ""
    meta_path = out_path.parent / f"{out_path.stem}{suffix}_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[compose-v364] meta → {meta_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
