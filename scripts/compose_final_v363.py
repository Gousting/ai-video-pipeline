#!/usr/bin/env python3
"""v3.6.3 合并成片：方案 B = 硬切 concat demuxer + dissolve xfade 留 MARGIN。

per 任务书 v363（oc_task_v363.txt）。

vs compose_final_v362.py 关键差异（v363 终极修复）：

- **bug 终极修复（方案 B）**：v362 用 5 个串联 xfade 即便 offset 算对，ffmpeg
  原生 xfade 在「offset + xfade_d ≈ 上一段流时长」边界会丢段或只出第一段
  （v36/v361/v362 三版均踩中）。v363 改：
  - 硬切段间 → ffmpeg **concat demuxer**（`-f concat -safe 0`，v3.4/v3.5 已验证稳定）
  - dissolve 段间 → **xfade + 严格 MARGIN 边距**（offset + xfade_d < 流时长 - 0.05）
- **零串联 xfade 边界风险**：3 处硬切走 concat（无 xfade 边界），仅 2 处 dissolve
  走 xfade，每段 offset 都断言安全。
- **流级时长读 ffprobe**：用 video stream duration（不是 format.duration，避免被
  audio stream 时长骗）。
- **保留 fancy dedupe + BGM 混音**（沿用 v36 链路）。

CLI:
  python compose_final_v363.py --dry-run
  python compose_final_v363.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_SPED_DIR = ROOT / "output" / "pipeline_v36" / "clips_sped"
DEFAULT_PLAN = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35.json"
DEFAULT_BGM = ROOT / "output" / "pipeline_v3" / "music" / "bgm_v32.wav"
DEFAULT_OUT = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v363.mp4"
DEFAULT_TMP = ROOT / "output" / "pipeline_v36" / "tmp" / "v363"

ACROSSFADE_SR = 44000
BG_FINAL_SR = 32000
FPS = 24
RES_W = 1344
RES_H = 768

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
# 期望总时长（手算）：sum(durations) - sum(dissolve_durs)
# 10.125 + 8.75*4 + 10.125 - 0.3 - 0.4 = 55.25 - 0.7 = 54.55s
EXPECTED_TOTAL_DUR_SEC = 54.55


def run(cmd: list[str], *, dry_run: bool = False, check: bool = True) -> str:
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[compose-v363] + {s}", flush=True)
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
                    f"{new.get('reason', '')} [v36 dedup: {tt} → hard_cut]"
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
                    f"[v36 budget: downgraded to hard_cut]"
                )
            else:
                budget -= 1
        out.append(new)
    return out


def group_consecutive_shots(transitions: list[dict],
                            n_shots: int) -> list[dict]:
    """把 N 段按 dissolve 切分成若干 group（每 group 内全 hard_cut）。

    返回 [{"shot_indices": [i_start..i_end], "transitions_after":
    [t_after_this_group]}, ...]。

    例：
      transitions = [HC, HC, HC, D, D]  (5 transitions, 6 shots)
      → groups = [
           {shots: [0,1,2,3], transition_after: D(0.3), i_transition: 3},
           {shots: [4],      transition_after: D(0.4), i_transition: 4},
           {shots: [5],      transition_after: None,    i_transition: None},
         ]
    """
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
                 *, dry_run: bool = False) -> float:
    """用 ffmpeg concat demuxer 拼一组 shot → out_path（无音轨）。

    返回 out_path 的 video stream duration。
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
        "-vf", f"scale={RES_W}:{RES_H}:flags=lanczos,setsar=1:1",
        "-an", "-r", str(FPS),
        "-movflags", "+faststart",
        "-fflags", "+genpts",
        str(out_path),
    ]
    run(cmd, dry_run=dry_run)
    if dry_run:
        return sum(ffprobe_stream_duration(c) for c in clips)
    return ffprobe_stream_duration(out_path)


def xfade_two(left: Path, right: Path, out_path: Path,
              xfade_dur: float, xfade_name: str,
              offset: float, left_dur: float, right_dur: float,
              *, dry_run: bool = False) -> float:
    """用 ffmpeg xfade filter 把两段拼起来（视频），返回 out 时长。

    left_dur / right_dur 由调用方传入（避免 dry-run 时 ffprobe 取到 0）。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[0:v]format=yuv420p,scale={RES_W}:{RES_H}:flags=lanczos,"
        f"setsar=1:1,fps={FPS}[lv];"
        f"[1:v]format=yuv420p,scale={RES_W}:{RES_H}:flags=lanczos,"
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
    ap.add_argument("--sped-dir", default=str(DEFAULT_SPED_DIR))
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--bgm", default=str(DEFAULT_BGM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tmp", default=str(DEFAULT_TMP))
    ap.add_argument("--safe-gap", type=float, default=SAFE_GAP_SEC,
                    help=f"xfade offset + duration < 流时长 - 安全余量 "
                         f"(默认 {SAFE_GAP_SEC}s)")
    ap.add_argument("--fancy-budget", type=int, default=FANCY_BUDGET_MAX)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    sped_dir = Path(args.sped_dir)
    plan_path = Path(args.plan)
    bgm_path = Path(args.bgm) if args.bgm else None
    out_path = Path(args.out)
    tmp_dir = Path(args.tmp)
    safe_gap = float(args.safe_gap)

    if not plan_path.exists():
        print(f"ERROR: plan 不存在 {plan_path}", file=sys.stderr)
        return 2
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    transitions = plan_data.get("transitions", [])
    if not transitions:
        print(f"ERROR: plan.transitions 为空", file=sys.stderr)
        return 3

    transitions_v36 = dedupe_fancy_transitions(transitions)
    transitions_v36 = enforce_fancy_budget(
        transitions_v36, max_fancy=args.fancy_budget,
    )

    n_shots = len(transitions_v36) + 1
    clip_paths = discover_shot_clips(sped_dir)
    if not clip_paths or len(clip_paths) < n_shots:
        print(f"ERROR: sped clips 不足 (need {n_shots}, "
              f"got {len(clip_paths)})", file=sys.stderr)
        return 4
    input_paths = clip_paths[:n_shots]
    durations = [ffprobe_stream_duration(p) for p in input_paths]
    durations = [round(d, 4) for d in durations]
    print(f"[compose-v363] {n_shots} shots, {len(transitions_v36)} transitions",
          flush=True)
    for p, d in zip(input_paths, durations):
        print(f"  sped clip: {p.name} {d:.3f}s", flush=True)
    print(f"[compose-v363] transition_types (after v36 dedupe+budget): "
          f"{[t['transition_type'] for t in transitions_v36]}", flush=True)

    tmp_dir.mkdir(parents=True, exist_ok=True)

    # ---- Step 1: 按 dissolve 分组，每组内 concat demuxer ----
    groups = group_consecutive_shots(transitions_v36, n_shots)
    print(f"[compose-v363] groups: "
          f"{[(g['shot_indices'], g['transition_after']) for g in groups]}",
          flush=True)

    group_paths: list[Path] = []
    group_durations: list[float] = []
    for gi, g in enumerate(groups):
        g_clips = [input_paths[i] for i in g["shot_indices"]]
        g_out = tmp_dir / f"group_{gi:02d}.mp4"
        # 在 dry-run 时，concat_group 内部 ffprobe 取到 0；
        # 用各 shot 实测时长求和做兜底。
        if args.dry_run:
            g_dur = sum(durations[i] for i in g["shot_indices"])
        else:
            g_dur = concat_group(g_clips, g_out, dry_run=args.dry_run)
        group_paths.append(g_out)
        group_durations.append(round(g_dur, 4))
        print(f"[compose-v363] group {gi}: shots={[i+1 for i in g['shot_indices']]} "
              f"duration={g_dur:.3f}s → {g_out.name}", flush=True)

    # ---- Step 2: 把各组用 xfade（仅 dissolve 边界）串起来 ----
    # 边界 = group[gi] 的 transition_after（dissolve）才用 xfade；
    # 其余（应该是 hard_cut 或 None）直接 concat demuxer。
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
    print(f"[compose-v363] xfade chain complete: final video duration = "
          f"{final_video_dur:.3f}s", flush=True)

    # ---- Step 3: 铺 BGM ----
    if bgm_path and bgm_path.exists():
        bgm_cmd = mix_bgm(final_video, bgm_path, final_video_dur, out_path,
                          dry_run=args.dry_run)
    else:
        # 无 BGM：直接复制
        cmd = ["ffmpeg", "-y", "-i", str(final_video), "-c:v", "copy",
               "-an", "-movflags", "+faststart", str(out_path)]
        bgm_cmd = run(cmd, dry_run=args.dry_run)
        print(f"[compose-v363] WARN: BGM not found, output is silent",
              file=sys.stderr)

    print(f"[compose-v363] final output → {out_path}", flush=True)

    # ---- Step 4: 写 meta ----
    fancy_used = sum(1 for t in transitions_v36
                     if t["transition_type"] in FANCY_TYPES)
    meta = {
        "compose_phase": "compose_final_v363",
        "pipeline_version": "v3.6.3",
        "sped_dir": str(sped_dir),
        "plan_path": str(plan_path),
        "input_paths": [str(p) for p in input_paths],
        "segment_durations_sec": durations,
        "transitions_original": [
            {"at_sec": t.get("at_sec"), "type": t.get("transition_type"),
             "duration": t.get("duration"), "reason": t.get("reason")}
            for t in transitions
        ],
        "transitions_after_v36_governance": [
            {"at_sec": t.get("at_sec"), "type": t.get("transition_type"),
             "duration": t.get("duration"), "reason": t.get("reason")}
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
        "bug_fix": {
            "issue_v36_v361_v362": (
                "v36/v361/v362 三版成片均失败：video stream 截断到 shot01 "
                "（245 帧 / 10.2s）或翻倍（109.5s / 2627 帧）。offset 数值 "
                "v362 已正确（迭代式 = Hermes 算死），但 ffmpeg xfade filter "
                "在「offset + xfade_d ≈ 第一路流时长」边界处不产出或丢段。"
                "三版代码逻辑正确，是 ffmpeg 原生边界 bug。"
            ),
            "fix_v363_plan_b": (
                "方案 B：硬切段间 → ffmpeg concat demuxer（v3.4/v3.5 已验证稳"
                "定，零 xfade 边界问题）；dissolve 段间 → xfade 但每段 offset "
                "严格满足 offset + xfade_d < 流时长 - safe_gap（safe_gap="
                "0.06s > 1 帧 @ 24fps）。总 xfade 数从 5 降到 2，边界风险"
                "面最小。"
            ),
            "rationale": (
                "concat demuxer 拼接是 ffmpeg 最稳定的拼接方式（v34/v35 已大"
                "量验证）。xfade 仅用于 dissolve 过渡（shot4→5, shot5→6 共 2 "
                "处），且每处 offset 留 0.06s 边距避免边界 bug。理论总时长 "
                "≈ 54.55s（≈ 1310 帧 @ 24fps）。"
            ),
        },
        "dry_run": bool(args.dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    suffix = "_dryrun" if args.dry_run else ""
    meta_path = out_path.parent / f"{out_path.stem}{suffix}_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[compose-v363] meta → {meta_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
