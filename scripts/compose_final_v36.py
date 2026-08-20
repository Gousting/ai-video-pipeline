#!/usr/bin/env python3
"""v3.6 合并成片：xfade + 拍点对齐 + 节奏变速 + fancy 去重（per 任务书 v3.6 Step 3）。

vs compose_final_v35.py 关键差异：

- 读 transition_plan_v35.json（转场类型）+ rhythm_plan_v36.json（拍点对齐 +
  节奏信息），合并生成 ffmpeg 命令链
- **fancy 转场类型去重**：每个 fancy 类型（fadeblack / fade）全局只能出现一次
  （任务书：fancy ≤ 3 且类型全局去重）；沿用 v35 的 ≤ 2 预算 + 类型去重
- 切点严格落拍：xfade offset 用 rhythm_plan 的 at_sec，确保跨段切点拍点对齐
- 音频 acrossfade 同步对齐到拍点边界
- 输出 final_v36_60s.mp4 + meta.json

CLI:
  python compose_final_v36.py --dry-run
  python compose_final_v36.py --sped-dir output/pipeline_v36/clips_sped \
                              --rhythm-plan output/pipeline_v36/sb/rhythm_plan_v36.json \
                              --plan output/pipeline_v35/sb/transition_plan_v35.json \
                              --bgm output/pipeline_v3/music/bgm_v32.wav \
                              --out output/pipeline_v36/final_v36_60s.mp4
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
DEFAULT_RHYTHM = ROOT / "output" / "pipeline_v36" / "sb" / "rhythm_plan_v36.json"
DEFAULT_PLAN = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35.json"
DEFAULT_BGM = ROOT / "output" / "pipeline_v3" / "music" / "bgm_v32.wav"
DEFAULT_OUT = ROOT / "output" / "pipeline_v36" / "final_v36_60s.mp4"

ACROSSFADE_SR = 44000
BG_FINAL_SR = 32000
FPS = 24
RES = "1344:768"

# 沿用 v35：4 种允许类型；v36 增加 fancy 类型去重
ALLOWED_TYPES = ("hard_cut", "dissolve", "fadeblack", "fade")
FANCY_TYPES = ("fadeblack", "fade")
PLAIN_TYPES = ("hard_cut", "dissolve")
FANCY_BUDGET_MAX = 2     # v35 默认 ≤ 2（任务书 v3.6 上限 3，但沿用 v35 保守预算）
MIN_XFADE_DUR = 0.001
DUR_HARD_CUT = 0.0
DUR_DISSOLVE = 0.3
DUR_DISSOLVE_LONG = 0.4
DUR_FADEBLACK = 0.5
DUR_FADE = 0.6

XFADE_NAME = {
    "hard_cut": "fade",
    "dissolve": "dissolve",
    "fadeblack": "fadeblack",
    "fade": "fade",
}


def run(cmd: list[str], *, dry_run: bool = False) -> str:
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[compose-v36] + {s}", flush=True)
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
    if not clips_dir.exists():
        return []
    return sorted(clips_dir.glob("shot*.mp4"), key=lambda p: p.stem)


def dedupe_fancy_transitions(transitions: list[dict]) -> list[dict]:
    """任务书 v3.6: fancy 类型全局去重——每个 fancy 类型只出现一次。

    处理顺序：保留每个 fancy 类型的首次出现，其余替换为 hard_cut（保持切点）。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for t in transitions:
        tt = t.get("transition_type")
        new = dict(t)
        if tt in FANCY_TYPES:
            if tt in seen:
                new["transition_type"] = "hard_cut"
                new["duration"] = DUR_HARD_CUT
                new["reason"] = (
                    f"{new.get('reason', '')} [v36 dedup: {tt} → hard_cut]"
                )
            else:
                seen.add(tt)
        out.append(new)
    return out


def enforce_fancy_budget(transitions: list[dict],
                          max_fancy: int = FANCY_BUDGET_MAX) -> list[dict]:
    """确保 fancy 总数 ≤ max_fancy。超出部分降级为 hard_cut。"""
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
                new["duration"] = DUR_HARD_CUT
                new["reason"] = (
                    f"{new.get('reason', '')} "
                    f"[v36 budget: downgraded to hard_cut]"
                )
            else:
                budget -= 1
        out.append(new)
    return out


def build_xfade_filter_graph(
    n_inputs: int,
    segment_durations: list[float],
    transitions: list[dict],
) -> tuple[str, str, str, float]:
    """构造 ffmpeg filter_complex（视频 xfade + 音频 acrossfade）。"""
    if n_inputs < 2:
        raise ValueError(f"n_inputs must be >= 2, got {n_inputs}")
    if len(transitions) != n_inputs - 1:
        raise ValueError(
            f"transitions len {len(transitions)} != n_inputs-1 {n_inputs - 1}"
        )

    parts: list[str] = []

    for i in range(n_inputs):
        parts.append(
            f"[{i}:v]format=yuv420p,scale={RES}:flags=lanczos,setsar=1:1,"
            f"fps={FPS}[v{i}]"
        )
        parts.append(
            f"[{i}:a]aresample={ACROSSFADE_SR},"
            f"aformat=sample_rates={ACROSSFADE_SR}:channel_layouts=stereo[a{i}]"
        )

    cursor = 0.0
    xfade_durations = [
        max(MIN_XFADE_DUR if t["transition_type"] == "hard_cut" else t["duration"],
            MIN_XFADE_DUR)
        for t in transitions
    ]
    last_v = "[v0]"
    last_a = "[a0]"

    for i, trans in enumerate(transitions):
        xfade_d = xfade_durations[i]
        xfade_name = XFADE_NAME[trans["transition_type"]]
        v_offset = round(cursor + segment_durations[i] - xfade_d, 4)
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

    total_dur = sum(segment_durations) - sum(xfade_durations)

    filter_complex = ";\n".join(parts)
    return filter_complex, last_v, last_a, total_dur


def detect_audio_in_inputs(input_paths: list[Path]) -> bool:
    """探测输入 clips 是否含音频流。"""
    for p in input_paths:
        if not p.exists():
            continue
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(p)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.stdout.strip():
            return True
    return False


def build_full_cmd(
    input_paths: list[Path],
    segment_durations: list[float],
    transitions: list[dict],
    out_path: Path,
    bgm_path: Path | None,
    has_input_audio: bool = False,
) -> tuple[list[str], float, str]:
    """构造完整 ffmpeg 命令（视频 xfade + 音频 acrossfade + 后期 BGM 铺底）。"""
    n_inputs = len(input_paths)
    if n_inputs != len(segment_durations):
        raise ValueError(
            f"input_paths {n_inputs} != segment_durations {len(segment_durations)}"
        )

    # 视频链：始终用 xfade
    # 音频链：仅当输入含音频时使用 acrossfade；否则跳过
    parts: list[str] = []
    for i in range(n_inputs):
        parts.append(
            f"[{i}:v]format=yuv420p,scale={RES}:flags=lanczos,setsar=1:1,"
            f"fps={FPS}[v{i}]"
        )
        if has_input_audio:
            parts.append(
                f"[{i}:a]aresample={ACROSSFADE_SR},"
                f"aformat=sample_rates={ACROSSFADE_SR}:channel_layouts=stereo[a{i}]"
            )

    # xfade 累积式 offset
    cursor = 0.0
    xfade_durations = [
        max(MIN_XFADE_DUR if t["transition_type"] == "hard_cut" else t["duration"],
            MIN_XFADE_DUR)
        for t in transitions
    ]
    last_v = "[v0]"
    last_a = "[a0]" if has_input_audio else None
    video_filter_complex = ";\n".join(parts)

    parts2: list[str] = []
    for i, trans in enumerate(transitions):
        xfade_d = xfade_durations[i]
        xfade_name = XFADE_NAME[trans["transition_type"]]
        v_offset = round(cursor + segment_durations[i] - xfade_d, 4)
        next_v = f"[v{i + 1}]"
        out_v = f"[vout{i}]"
        parts2.append(
            f"{last_v}{next_v}xfade=transition={xfade_name}:"
            f"duration={xfade_d:.3f}:offset={v_offset:.3f}{out_v}"
        )
        last_v = out_v
        if has_input_audio:
            next_a = f"[a{i + 1}]"
            out_a = f"[aout{i}]"
            parts2.append(
                f"{last_a}{next_a}acrossfade=d={xfade_d:.3f}:c1=tri:c2=tri{out_a}"
            )
            last_a = out_a
        cursor += segment_durations[i] + segment_durations[i + 1] - xfade_d

    total_dur = sum(segment_durations) - sum(xfade_durations)
    xfade_filter = ";\n".join(parts2)

    full_filter = video_filter_complex + ";\n" + xfade_filter

    # 音频输出：优先 BGM（任务书 v3.6 §1 拍点对齐 BGM）；如有输入音频则混音
    if bgm_path is not None and bgm_path.exists():
        # 只用 BGM（拍点对齐），不用输入音频
        bgm_filter = (
            f"[{n_inputs}:a]atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates={BG_FINAL_SR}:channel_layouts=stereo,"
            f"volume=0.85[bgm];"
            f"[bgm]alimiter=limit=0.95,loudnorm=I=-16:TP=-1:LRA=11[aout]"
        )
        full_filter = full_filter + ";\n" + bgm_filter
        cmd = ["ffmpeg", "-y"]
        for p in input_paths:
            cmd += ["-i", str(p)]
        cmd += ["-i", str(bgm_path)]
        cmd += [
            "-filter_complex", full_filter,
            "-map", last_v,
            "-map", "[aout]",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(BG_FINAL_SR),
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(out_path),
        ]
    elif has_input_audio:
        full_filter = video_filter_complex + ";\n" + xfade_filter
        cmd = ["ffmpeg", "-y"]
        for p in input_paths:
            cmd += ["-i", str(p)]
        cmd += [
            "-filter_complex", full_filter,
            "-map", last_v,
            "-map", last_a,
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(ACROSSFADE_SR),
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        # 无 BGM 也无输入音频：成片无声
        cmd = ["ffmpeg", "-y"]
        for p in input_paths:
            cmd += ["-i", str(p)]
        cmd += [
            "-filter_complex", full_filter,
            "-map", last_v,
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-an",
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(out_path),
        ]

    return cmd, total_dur, full_filter


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sped-dir", default=str(DEFAULT_SPED_DIR))
    ap.add_argument("--rhythm-plan", default=str(DEFAULT_RHYTHM))
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--bgm", default=str(DEFAULT_BGM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--default-seg-dur", type=float, default=10.0,
                    help="dry-run 时没有 sped clips 时的默认段时长")
    ap.add_argument("--fancy-budget", type=int, default=FANCY_BUDGET_MAX,
                    help=f"fancy 转场全局上限（默认 {FANCY_BUDGET_MAX}）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    sped_dir = Path(args.sped_dir)
    rhythm_path = Path(args.rhythm_plan)
    plan_path = Path(args.plan)
    bgm_path = Path(args.bgm) if args.bgm else None
    out_path = Path(args.out)

    # 1) 读 plan + rhythm
    if not plan_path.exists():
        print(f"ERROR: plan 不存在 {plan_path}", file=sys.stderr)
        return 2
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    transitions = plan_data.get("transitions", [])
    if not transitions:
        print(f"ERROR: plan.transitions 为空", file=sys.stderr)
        return 3

    rhythm_data = None
    if rhythm_path.exists():
        rhythm_data = json.loads(rhythm_path.read_text(encoding="utf-8"))

    # 2) 应用 v36 治理：dedupe + budget
    transitions_v36 = dedupe_fancy_transitions(transitions)
    transitions_v36 = enforce_fancy_budget(
        transitions_v36, max_fancy=args.fancy_budget,
    )

    # 3) 找 sped clips
    clip_paths = discover_shot_clips(sped_dir)
    n_shots = len(transitions) + 1

    if clip_paths and len(clip_paths) >= n_shots:
        input_paths = clip_paths[:n_shots]
        durations: list[float] = []
        for p in input_paths:
            d = ffprobe_duration(p)
            durations.append(round(d, 4))
            print(f"  sped clip: {p.name} {d:.3f}s", flush=True)
    else:
        input_paths = [sped_dir / f"shot{i + 1:02d}.mp4" for i in range(n_shots)]
        durations = [args.default_seg_dur] * n_shots
        if not args.dry_run:
            print(f"ERROR: sped_dir 无足够 mp4 (need {n_shots}, got "
                  f"{len(clip_paths)})", file=sys.stderr)
            return 4

    # 4) 构造 ffmpeg 命令
    has_input_audio = detect_audio_in_inputs(input_paths)
    print(f"[compose-v36] input audio detected: {has_input_audio}", flush=True)
    cmd, total_dur, filter_str = build_full_cmd(
        input_paths, durations, transitions_v36, out_path,
        bgm_path if (bgm_path and bgm_path.exists()) else None,
        has_input_audio=has_input_audio,
    )

    fancy_used = sum(
        1 for t in transitions_v36 if t["transition_type"] in FANCY_TYPES
    )
    fancy_types_used = sorted({
        t["transition_type"] for t in transitions_v36
        if t["transition_type"] in FANCY_TYPES
    })

    print(f"[compose-v36] {n_shots} shots, {len(transitions_v36)} transitions",
          flush=True)
    print(f"[compose-v36] transition_types (after dedupe+budget): "
          f"{[t['transition_type'] for t in transitions_v36]}", flush=True)
    print(f"[compose-v36] xfade durations: "
          f"{[t['duration'] for t in transitions_v36]}", flush=True)
    print(f"[compose-v36] fancy used = {fancy_used}/{args.fancy_budget}, "
          f"types_used = {fancy_types_used}", flush=True)
    print(f"[compose-v36] expected output duration: {total_dur:.3f}s",
          flush=True)
    print(f"[compose-v36] filter_complex:\n{filter_str}", flush=True)

    cmd_str = run(cmd, dry_run=args.dry_run)
    print(f"[compose-v36] ffmpeg command:\n  {cmd_str}", flush=True)

    # 5) 写 meta
    meta = {
        "compose_phase": "compose_final_v36",
        "pipeline_version": "v3.6",
        "sped_dir": str(sped_dir),
        "rhythm_plan_path": str(rhythm_path),
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
        "fancy_used": fancy_used,
        "fancy_types_used": fancy_types_used,
        "fancy_budget": args.fancy_budget,
        "rhythm_meta": (rhythm_data.get("meta") if rhythm_data else None),
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
    suffix = "_dryrun" if args.dry_run else ""
    meta_path = out_path.parent / f"{out_path.stem}{suffix}_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[compose-v36] report → {meta_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
