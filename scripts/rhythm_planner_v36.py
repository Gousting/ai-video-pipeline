#!/usr/bin/env python3
"""v3.6 节奏规划器：BGM 拍点 + 镜头时长 + 快慢慢呼吸 + 切点落拍（per 任务书 v3.6）。

vs v3.5 (transition_planner_v35.py) 关键差异：

- 接管镜头时长计算：每段时长 = 拍点整数倍（v35 是固定 10s，v36 重新对齐）
- 引入 **phase** 标签：slow_open / fast_middle / slow_tail，强制三段式呼吸
- 切点落拍：所有 cross-shot cut at_sec ∈ downbeat 网格（120 BPM, 2s 小节边界）
- 提供 per-shot 变速窗口（speed_windows）给 speed_segment_v36.py：
    - slow_open：保持或微慢放 (1.0x / 0.85x)
    - fast_middle：快放 1.2x-1.5x（局部窗口）
    - slow_tail：保持或微慢放
- **确定性**：相同输入两次运行结果一致（无随机）
- **不重新做转场类型**：转场类型由 transition_planner_v35 决定；本模块只决定
  **何时切 + 切多长 + 切前变速**

CLI:
  python rhythm_planner_v36.py --storyboard <sb.json> --beats <beats.json> \
                                --plan <transition_plan.json> --out <rhythm.json>
  python rhythm_planner_v36.py --dry-run   # 内嵌 SHOTS + 假 beats 跑一遍
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v35" / "sb" / "storyboard_v35.json"
DEFAULT_BEATS = ROOT / "output" / "pipeline_v3" / "music" / "beats.json"
DEFAULT_PLAN = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35.json"
DEFAULT_OUT = ROOT / "output" / "pipeline_v36" / "sb" / "rhythm_plan_v36.json"

# ----- 节拍常量（per beat_analyze_v32：120 BPM）-----
BPM = 120.0
BEAT_PERIOD = 60.0 / BPM            # 0.5s
BAR_PERIOD = 4.0 * BEAT_PERIOD      # 2.0s  (1 小节 4 拍)
PHASE_PERIOD = 8.0 * BEAT_PERIOD    # 4.0s  (2 小节 8 拍)

# ----- 节奏三段式常量（per 任务书 Step 1）-----
PHASE_SLOW_OPEN = "slow_open"
PHASE_FAST_MIDDLE = "fast_middle"
PHASE_SLOW_TAIL = "slow_tail"

SLOW_MIN_BEATS = 6                   # slow ≥ 3s (6 × 0.5s)
FAST_MIN_BEATS = 2                   # fast 0.8-1.5s 段至少 2 拍（允许更小）
FAST_MAX_BEATS = 4                   # fast 段 ≤ 2s

SPEED_FACTOR_SLOW_OPEN = 1.0         # 慢开场保持原速
SPEED_FACTOR_SLOW_TAIL = 1.0         # 慢收束保持原速
SPEED_FACTOR_FAST_MIDDLE = 1.35      # 中段快放 1.2x-1.5x，取中位 1.35x

# 切点对齐容差（per 任务书 V1：±0.05s）
CUT_ALIGN_TOLERANCE = 0.05
# 镜头时长对齐容差（per 任务书 V2：±0.1s = ±0.2 拍）
DURATION_ALIGN_TOLERANCE = 0.1


def snap_to_grid(t: float, grid: float) -> float:
    """把时间 t 吸附到 grid 整数倍上。"""
    n = round(t / grid)
    return round(n * grid, 4)


def load_beats(beats_path: Path) -> tuple[list[float], list[float], float]:
    """读 beats.json 返回 (beats, downbeats, beat_period_sec)。"""
    if not beats_path.exists():
        raise FileNotFoundError(f"beats.json 不存在: {beats_path}")
    data = json.loads(beats_path.read_text(encoding="utf-8"))
    beats = [float(b) for b in data["beats"]]
    downbeats = [float(b) for b in data["downbeats"]]
    period = float(data.get("beat_period_sec", BEAT_PERIOD))
    return beats, downbeats, period


def assign_phases(n_shots: int) -> list[str]:
    """把 n_shots 段切成三段式呼吸：slow_open / fast_middle / slow_tail。

    规则（per 任务书：前 3s 钩子、每 8-10s 小高潮、结尾 3-5s 冷却）：
      - n_shots == 6（v35 默认）：[slow, fast, fast, fast, fast, slow]
      - n_shots == 5：[slow, fast, fast, fast, slow]
      - n_shots == 4：[slow, fast, fast, slow]
      - n_shots == 3：[slow, fast, slow]
      - n_shots >= 7：第 1 + 最后 1 是 slow，中间全 fast
      - n_shots < 3：全 slow（兜底，不强制三段）
    """
    if n_shots < 3:
        return [PHASE_SLOW_OPEN] * n_shots
    out = [PHASE_SLOW_OPEN] + [PHASE_FAST_MIDDLE] * (n_shots - 2) + [PHASE_SLOW_TAIL]
    return out


def speed_factor_for_phase(phase: str) -> float:
    if phase == PHASE_SLOW_OPEN:
        return SPEED_FACTOR_SLOW_OPEN
    if phase == PHASE_SLOW_TAIL:
        return SPEED_FACTOR_SLOW_TAIL
    if phase == PHASE_FAST_MIDDLE:
        return SPEED_FACTOR_FAST_MIDDLE
    return 1.0


def build_speed_window(shot_idx: int, phase: str, duration_beats: int,
                       beat_period: float) -> dict:
    """构造单段的速度窗口：fast_middle 中段局部快放，其余整段保持。

    窗口语义：window[0] = 慢入 (1.0x) 窗口 end-beat；window[1] = 快出开始 beat。
    默认策略：
      - slow_open / slow_tail：整段 factor = 1.0（窗口空）
      - fast_middle：前 25% 慢入（factor=1.0），中间 50% 快放（factor=1.35），
        最后 25% 慢出（factor=1.0）。给出 start/end beat。
    """
    if phase in (PHASE_SLOW_OPEN, PHASE_SLOW_TAIL):
        return {
            "window_strategy": "hold",
            "factor": speed_factor_for_phase(phase),
            "windows": [],
            "total_beats": duration_beats,
        }
    # fast_middle
    n = duration_beats
    q1 = max(1, math.floor(n * 0.25))
    q3 = max(q1 + 1, math.ceil(n * 0.75))
    return {
        "window_strategy": "slow_in_fast_middle_slow_out",
        "factor_default": 1.0,
        "factor_fast": SPEED_FACTOR_FAST_MIDDLE,
        "windows": [
            {
                "kind": "hold",
                "start_beat": 0,
                "end_beat": q1,
                "factor": 1.0,
                "rationale": "fast_middle 慢入（前 25%）",
            },
            {
                "kind": "speed",
                "start_beat": q1,
                "end_beat": q3,
                "factor": SPEED_FACTOR_FAST_MIDDLE,
                "rationale": (
                    f"fast_middle 快放 (×{SPEED_FACTOR_FAST_MIDDLE:.2f}, "
                    f"中段 50%)"
                ),
            },
            {
                "kind": "hold",
                "start_beat": q3,
                "end_beat": n,
                "factor": 1.0,
                "rationale": "fast_middle 慢出（后 25%）",
            },
        ],
        "total_beats": n,
    }


def plan_rhythm(shots: list[dict], beats: list[float],
                downbeats: list[float], beat_period: float,
                transition_plan: list[dict] | None = None) -> dict:
    """核心函数：storyboard shots + beats → rhythm_plan。

    返回 dict 含:
      - rhythm_plan[]: 每段 {index, phase, start_beat, duration_beats,
        duration_sec, target_duration_sec, start_sec, end_sec, speed}
      - cut_points[]: 每段边界 at_sec（自动对齐到 beat 网格）
      - meta: bpm / total / phases_summary
    """
    n = len(shots)
    if n == 0:
        return {"rhythm_plan": [], "cut_points": [], "meta": {}}

    phases = assign_phases(n)

    # 1) 累加 cursor 用整数拍点（每段保持原 duration_beats，不重新切分）
    rhythm_plan: list[dict] = []
    cursor_beat = 0
    for i, shot in enumerate(shots):
        # 段时长（拍）：snap 到整数倍；保留 v35 的 10s = 20 拍
        raw_dur = float(shot.get("duration_sec", 10.0))
        dur_beats = max(1, round(raw_dur / beat_period))
        dur_sec = dur_beats * beat_period
        phase = phases[i]
        speed_win = build_speed_window(i, phase, dur_beats, beat_period)

        rhythm_plan.append({
            "index": shot.get("index", i + 1),
            "title": shot.get("title", ""),
            "phase": phase,
            "start_beat": cursor_beat,
            "start_sec": round(cursor_beat * beat_period, 4),
            "duration_beats": dur_beats,
            "duration_sec": dur_sec,
            "target_duration_sec": dur_sec,
            "end_sec": round((cursor_beat + dur_beats) * beat_period, 4),
            "speed_factor_default": speed_factor_for_phase(phase),
            "speed_window": speed_win,
            "char_focus": shot.get("char_focus", ""),
            "include_senior": bool(shot.get("include_senior", False)),
            "include_junior": bool(shot.get("include_junior", False)),
            "downbeat_start_original": shot.get("downbeat_start", None),
        })
        cursor_beat += dur_beats

    # 2) 切点 = 段边界，全部落在拍点上（必对齐）
    cut_points: list[dict] = []
    for i in range(1, n):
        prev = rhythm_plan[i - 1]
        at_sec = prev["end_sec"]
        at_beat = prev["start_beat"] + prev["duration_beats"]
        cut_points.append({
            "at_sec": round(at_sec, 4),
            "at_beat": at_beat,
            "from_shot": prev["index"],
            "to_shot": rhythm_plan[i]["index"],
            "phase_boundary": f"{prev['phase']} → {rhythm_plan[i]['phase']}",
            "downbeat_aligned": (
                abs(at_sec - snap_to_grid(at_sec, BAR_PERIOD)) < CUT_ALIGN_TOLERANCE
            ),
        })

    # 3) 切点 vs transition_plan 的 at_sec 一致性校验（v35 plan 的 at_sec 应已被
    #    拍点对齐——v35 cursor 累加 shot.duration_sec=10s 正好是整数拍）
    cross_check = []
    if transition_plan:
        for i, t in enumerate(transition_plan):
            if i >= len(cut_points):
                break
            cp = cut_points[i]
            cross_check.append({
                "index": i,
                "v35_at_sec": t.get("at_sec"),
                "v36_at_sec": cp["at_sec"],
                "delta_sec": round(abs(t.get("at_sec", 0) - cp["at_sec"]), 4),
                "aligned_to_beat_grid": (
                    abs(cp["at_sec"] - snap_to_grid(cp["at_sec"], beat_period))
                    < CUT_ALIGN_TOLERANCE
                ),
            })

    total_beats = cursor_beat
    total_sec = round(total_beats * beat_period, 4)

    return {
        "planner_version": "v3.6",
        "pipeline": "ai-video-pipeline v3.6",
        "bpm": BPM,
        "beat_period_sec": beat_period,
        "bar_period_sec": BAR_PERIOD,
        "phase_period_sec": PHASE_PERIOD,
        "cut_align_tolerance_sec": CUT_ALIGN_TOLERANCE,
        "duration_align_tolerance_sec": DURATION_ALIGN_TOLERANCE,
        "rhythm_plan": rhythm_plan,
        "cut_points": cut_points,
        "transition_plan_cross_check": cross_check,
        "meta": {
            "n_shots": n,
            "n_cuts": len(cut_points),
            "total_beats": total_beats,
            "total_duration_sec": total_sec,
            "phases": {
                PHASE_SLOW_OPEN: sum(1 for r in rhythm_plan
                                       if r["phase"] == PHASE_SLOW_OPEN),
                PHASE_FAST_MIDDLE: sum(1 for r in rhythm_plan
                                         if r["phase"] == PHASE_FAST_MIDDLE),
                PHASE_SLOW_TAIL: sum(1 for r in rhythm_plan
                                       if r["phase"] == PHASE_SLOW_TAIL),
            },
            "speed_factor_slow_open": SPEED_FACTOR_SLOW_OPEN,
            "speed_factor_fast_middle": SPEED_FACTOR_FAST_MIDDLE,
            "speed_factor_slow_tail": SPEED_FACTOR_SLOW_TAIL,
            "rule_summary": [
                f"每段时长 = {beat_period:.2f}s × 整数拍（v35 固定 10s = 20 拍）",
                "切点 = 段边界 at_sec 必对齐到 beat 网格（容差 ±0.05s）",
                f"phase 三段式：slow_open / fast_middle / slow_tail",
                f"slow 段：整段 speed_factor=1.0，≥ {SLOW_MIN_BEATS} 拍（{SLOW_MIN_BEATS*beat_period:.1f}s）",
                f"fast_middle 段：中段 50% 窗口 ×{SPEED_FACTOR_FAST_MIDDLE:.2f} 快放，前后各 25% 慢入慢出",
                f"fancy 转场（fadeblack/fade）由 transition_planner_v35 给出（≤ 2，v36 沿用 v35 预算）",
            ],
        },
    }


# ---------- 内嵌 SHOTS（与 storyboard_v35 字面一致，用于 --dry-run）----------
DRY_RUN_SHOTS = [
    {"index": 1, "title": "学姐开场", "duration_sec": 10, "char_focus": "senior",
     "include_senior": True, "include_junior": False},
    {"index": 2, "title": "学妹登场", "duration_sec": 10, "char_focus": "junior",
     "include_senior": False, "include_junior": True},
    {"index": 3, "title": "学姐氛围", "duration_sec": 10, "char_focus": "senior",
     "include_senior": True, "include_junior": False},
    {"index": 4, "title": "学妹活力", "duration_sec": 10, "char_focus": "junior",
     "include_senior": False, "include_junior": True},
    {"index": 5, "title": "双人对比", "duration_sec": 10, "char_focus": "both",
     "include_senior": True, "include_junior": True},
    {"index": 6, "title": "收束", "duration_sec": 10, "char_focus": "both",
     "include_senior": True, "include_junior": True},
]

DRY_RUN_BEATS = [i * BEAT_PERIOD for i in range(145)]
DRY_RUN_DOWNBEATS = [i * BAR_PERIOD for i in range(36)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--beats", default=str(DEFAULT_BEATS))
    ap.add_argument("--plan", default=str(DEFAULT_PLAN),
                    help="可选：transition_plan_v35.json，做切点交叉校验")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--dry-run", action="store_true",
                    help="用内嵌 DRY_RUN_SHOTS 跑一遍，不读 storyboard")
    args = ap.parse_args(argv)

    transition_plan_data = None
    if args.dry_run:
        shots = DRY_RUN_SHOTS
        beats = DRY_RUN_BEATS
        downbeats = DRY_RUN_DOWNBEATS
        beat_period = BEAT_PERIOD
        print("[rhythm-dry-run] 使用内嵌 DRY_RUN_SHOTS (6 shots) + 假 beats",
              flush=True)
    else:
        sb_path = Path(args.storyboard)
        if not sb_path.exists():
            print(f"ERROR: storyboard 不存在 {sb_path}", file=sys.stderr)
            return 2
        sb = json.loads(sb_path.read_text(encoding="utf-8"))
        shots = sb.get("shots", [])
        beats, downbeats, beat_period = load_beats(Path(args.beats))
        plan_path = Path(args.plan)
        if plan_path.exists():
            transition_plan_data = json.loads(
                plan_path.read_text(encoding="utf-8")).get("transitions", [])
        print(f"[rhythm] {len(shots)} shots, {len(beats)} beats, "
              f"beat_period={beat_period}s", flush=True)

    payload = plan_rhythm(shots, beats, downbeats, beat_period,
                          transition_plan=transition_plan_data)

    # 写文件：dry-run 也写一份方便 validate 读取
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"[rhythm] -> {out_path}", flush=True)

    # 打印摘要
    meta = payload["meta"]
    phases = meta["phases"]
    print(f"[rhythm] phases: {phases}", flush=True)
    print(f"[rhythm] total_beats={meta['total_beats']} "
          f"total_sec={meta['total_duration_sec']:.3f}", flush=True)
    for r in payload["rhythm_plan"]:
        print(f"  shot{r['index']:02d} {r['phase']:<13s} "
              f"beats [{r['start_beat']:3d}..{r['start_beat']+r['duration_beats']:3d}] "
              f"= {r['duration_sec']:5.2f}s "
              f"speed={r['speed_factor_default']:.2f} "
              f"title={r['title']}", flush=True)
    print(f"[rhythm] {len(payload['cut_points'])} cut_points:", flush=True)
    for c in payload["cut_points"]:
        print(f"  cut@{c['at_sec']:.2f}s shot{c['from_shot']}→shot{c['to_shot']} "
              f"({c['phase_boundary']}) beat_aligned={c['downbeat_aligned']}",
              flush=True)
    if payload["transition_plan_cross_check"]:
        print(f"[rhythm] cross-check v35 at_sec vs v36 at_sec:", flush=True)
        for cc in payload["transition_plan_cross_check"]:
            mark = "OK" if cc["delta_sec"] <= CUT_ALIGN_TOLERANCE else "DIFF"
            print(f"  [{mark}] i={cc['index']} "
                  f"v35={cc['v35_at_sec']:.2f}s v36={cc['v36_at_sec']:.2f}s "
                  f"Δ={cc['delta_sec']:.3f}s "
                  f"beat_aligned={cc['aligned_to_beat_grid']}",
                  flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
