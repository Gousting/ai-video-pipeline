#!/usr/bin/env python3
"""v3.6 自检：节奏治理五断言（per 任务书 v3.6 Step 4）。

断言 1 (V1)：所有切点 at_sec 落拍点网格（容差 ±0.05s）
断言 2 (V2)：镜头时长 ∈ 拍点整数倍（容差 ±0.1s）
断言 3 (V3)：fancy 转场全局 ≤ 3 且类型无重复（任务书 v3.6 升级）
断言 4 (V4)：存在 slow_open / fast_middle / slow_tail 三段式
断言 5 (V5)：变速参数合法（factor ∈ [0.5, 2.0] 且 setpts 表达式可解析）

CLI:
  python validate_v36.py
  python validate_v36.py --rhythm-plan <f> --plan <f> --compose-meta <f>

退出码：全 PASS 返回 0；任一 FAIL 返回 1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")

DEFAULT_RHYTHM = ROOT / "output" / "pipeline_v36" / "sb" / "rhythm_plan_v36.json"
DEFAULT_PLAN = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35.json"
DEFAULT_COMPOSE_META = ROOT / "output" / "pipeline_v36" / "final_v36_60s_meta.json"
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v35" / "sb" / "storyboard_v35.json"

CUT_ALIGN_TOLERANCE = 0.05
DURATION_ALIGN_TOLERANCE = 0.1
ALLOWED_TYPES = ("hard_cut", "dissolve", "fadeblack", "fade")
FANCY_TYPES = ("fadeblack", "fade")
FANCY_BUDGET_MAX = 3                  # 任务书 v3.6 上限（v35 是 2；v36 升级为 3）
SPEED_FACTOR_MIN = 0.5
SPEED_FACTOR_MAX = 2.0


def _print_assertion(name: str, passed: bool, detail: str = "") -> bool:
    tag = "PASS" if passed else "FAIL"
    print(f"[{name}] {tag} {detail}", flush=True)
    return passed


def assert_v1_cuts_on_beat_grid(rhythm_path: Path, plan_path: Path,
                                 beat_period: float = 0.5,
                                 bar_period: float = 2.0) -> tuple[bool, dict]:
    """V1：所有切点 at_sec 落拍点网格（容差 ±0.05s）。

    检查 rhythm_plan 的 cut_points 与 transition_plan 的 at_sec 双源对齐。
    """
    if not rhythm_path.exists():
        return False, {"error": f"rhythm_plan 不存在: {rhythm_path}"}

    rp = json.loads(rhythm_path.read_text(encoding="utf-8"))
    rhythm_cuts = rp.get("cut_points", [])
    errors: list[str] = []
    aligned_count = 0

    # A) rhythm_plan.cut_points at_sec 对齐到 beat 网格
    for c in rhythm_cuts:
        at = float(c.get("at_sec", -1))
        snapped_beat = round(at / beat_period) * beat_period
        if abs(at - snapped_beat) > CUT_ALIGN_TOLERANCE:
            errors.append(
                f"rhythm cut@{at:.3f}s off beat-grid "
                f"(nearest={snapped_beat:.3f}s, Δ={abs(at - snapped_beat):.3f}s)"
            )
        else:
            aligned_count += 1

    # B) transition_plan 的 at_sec 也对齐
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_cuts = plan.get("transitions", [])
        for c in plan_cuts:
            at = float(c.get("at_sec", -1))
            snapped_beat = round(at / beat_period) * beat_period
            if abs(at - snapped_beat) > CUT_ALIGN_TOLERANCE:
                errors.append(
                    f"plan cut@{at:.3f}s off beat-grid "
                    f"(Δ={abs(at - snapped_beat):.3f}s)"
                )
            else:
                aligned_count += 1

    passed = (len(errors) == 0) and (len(rhythm_cuts) > 0)
    return passed, {
        "n_rhythm_cuts": len(rhythm_cuts),
        "n_aligned": aligned_count,
        "errors": errors[:10],
        "tolerance_sec": CUT_ALIGN_TOLERANCE,
        "beat_period_sec": beat_period,
    }


def assert_v2_shot_duration_beat_multiple(rhythm_path: Path,
                                            beat_period: float = 0.5,
                                            tolerance: float = DURATION_ALIGN_TOLERANCE,
                                            ) -> tuple[bool, dict]:
    """V2：所有镜头时长 ∈ 拍点整数倍（容差 ±0.1s）。"""
    if not rhythm_path.exists():
        return False, {"error": f"rhythm_plan 不存在: {rhythm_path}"}

    rp = json.loads(rhythm_path.read_text(encoding="utf-8"))
    rhythm_plan = rp.get("rhythm_plan", [])
    errors: list[str] = []
    aligned = 0
    for r in rhythm_plan:
        d = float(r.get("duration_sec", 0))
        snapped = round(d / beat_period) * beat_period
        delta = abs(d - snapped)
        if delta > tolerance:
            errors.append(
                f"shot{r.get('index')} dur={d:.3f}s off beat-mult "
                f"(nearest={snapped:.3f}s, Δ={delta:.3f}s)"
            )
        else:
            aligned += 1

    passed = (len(errors) == 0) and (len(rhythm_plan) > 0)
    return passed, {
        "n_shots": len(rhythm_plan),
        "n_aligned": aligned,
        "errors": errors[:10],
        "tolerance_sec": tolerance,
        "beat_period_sec": beat_period,
    }


def assert_v3_fancy_budget_and_dedup(plan_path: Path,
                                      budget_max: int = FANCY_BUDGET_MAX,
                                      ) -> tuple[bool, dict]:
    """V3：fancy 转场全局 ≤ budget_max 且类型无重复。"""
    if not plan_path.exists():
        return False, {"error": f"plan 不存在: {plan_path}"}

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    transitions = plan.get("transitions", [])

    bad_types: list[dict] = []
    fancy_types_used: list[str] = []
    for i, t in enumerate(transitions):
        tt = t.get("transition_type", "")
        if tt not in ALLOWED_TYPES:
            bad_types.append({"index": i, "type": tt})
        if tt in FANCY_TYPES:
            fancy_types_used.append(tt)

    fancy_count = len(fancy_types_used)
    duplicates = len(fancy_types_used) - len(set(fancy_types_used))
    over_budget = fancy_count > budget_max

    passed = (
        len(bad_types) == 0
        and not over_budget
        and duplicates == 0
    )
    return passed, {
        "n_transitions": len(transitions),
        "fancy_count": fancy_count,
        "fancy_types_used": fancy_types_used,
        "fancy_unique_types": sorted(set(fancy_types_used)),
        "duplicates": duplicates,
        "budget_max": budget_max,
        "over_budget": over_budget,
        "bad_types": bad_types,
    }


def assert_v4_three_phase_breathing(rhythm_path: Path) -> tuple[bool, dict]:
    """V4：存在 slow_open / fast_middle / slow_tail 三段式。"""
    if not rhythm_path.exists():
        return False, {"error": f"rhythm_plan 不存在: {rhythm_path}"}

    rp = json.loads(rhythm_path.read_text(encoding="utf-8"))
    phases = rp.get("meta", {}).get("phases", {}) or {}

    has_slow_open = phases.get("slow_open", 0) >= 1
    has_fast_middle = phases.get("fast_middle", 0) >= 1
    has_slow_tail = phases.get("slow_tail", 0) >= 1

    # 顺序约束：rhythm_plan 中 slow_open 必须在前面、slow_tail 必须在末尾
    rp_list = rp.get("rhythm_plan", [])
    order_ok = True
    if rp_list:
        first_phase = rp_list[0].get("phase", "")
        last_phase = rp_list[-1].get("phase", "")
        order_ok = (first_phase == "slow_open") and (last_phase == "slow_tail")

    passed = has_slow_open and has_fast_middle and has_slow_tail and order_ok
    return passed, {
        "phases": phases,
        "has_slow_open": has_slow_open,
        "has_fast_middle": has_fast_middle,
        "has_slow_tail": has_slow_tail,
        "order_ok": order_ok,
        "n_shots": len(rp_list),
    }


_SETPTS_RE = re.compile(
    r"setpts=\(?PTS-STARTPTS\)?\*(?P<factor>[0-9.]+)\*enable=between\(t,"
    r"(?P<start>[0-9.]+),(?P<end>[0-9.]+)\)"
)


def assert_v5_speed_params_legal(rhythm_path: Path,
                                   fmin: float = SPEED_FACTOR_MIN,
                                   fmax: float = SPEED_FACTOR_MAX) -> tuple[bool, dict]:
    """V5：变速参数合法（factor ∈ [fmin, fmax] 且 setpts 表达式可解析）。"""
    if not rhythm_path.exists():
        return False, {"error": f"rhythm_plan 不存在: {rhythm_path}"}

    rp = json.loads(rhythm_path.read_text(encoding="utf-8"))
    rhythm_plan = rp.get("rhythm_plan", [])

    errors: list[str] = []
    valid_count = 0
    for r in rhythm_plan:
        sw = r.get("speed_window", {}) or {}
        strategy = sw.get("window_strategy", "hold")
        if strategy == "hold":
            f = float(sw.get("factor", 1.0))
            if not (fmin <= f <= fmax):
                errors.append(
                    f"shot{r.get('index')} hold factor {f} out of range"
                )
            else:
                valid_count += 1
        else:
            for w in sw.get("windows", []):
                f = float(w.get("factor", 1.0))
                s_b = float(w.get("start_beat", 0))
                e_b = float(w.get("end_beat", 0))
                if not (fmin <= f <= fmax):
                    errors.append(
                        f"shot{r.get('index')} window factor {f} out of range"
                    )
                if not (s_b >= 0 and e_b > s_b):
                    errors.append(
                        f"shot{r.get('index')} window invalid beats: "
                        f"{s_b}..{e_b}"
                    )
            if not errors or all(
                f"shot{r.get('index')}" not in e for e in errors
            ):
                valid_count += 1

    passed = len(errors) == 0
    return passed, {
        "n_shots": len(rhythm_plan),
        "n_valid": valid_count,
        "errors": errors[:10],
        "factor_range": [fmin, fmax],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rhythm-plan", default=str(DEFAULT_RHYTHM))
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--compose-meta", default=str(DEFAULT_COMPOSE_META))
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    args = ap.parse_args(argv)

    rhythm_path = Path(args.rhythm_plan)
    plan_path = Path(args.plan)
    compose_meta_path = Path(args.compose_meta)
    sb_path = Path(args.storyboard)

    beat_period = 0.5
    bar_period = 2.0
    if rhythm_path.exists():
        rp = json.loads(rhythm_path.read_text(encoding="utf-8"))
        beat_period = float(rp.get("beat_period_sec", 0.5))
        bar_period = float(rp.get("bar_period_sec", 2.0))

    print("=" * 60, flush=True)
    print("v3.6 节奏治理 自检 (validate_v36.py)", flush=True)
    print("=" * 60, flush=True)
    print(f"[setup] rhythm_plan  = {rhythm_path}", flush=True)
    print(f"[setup] plan         = {plan_path}", flush=True)
    print(f"[setup] compose_meta = {compose_meta_path}", flush=True)
    print(f"[setup] beat_period  = {beat_period}s", flush=True)

    results: dict[str, bool] = {}

    # V1
    v1_pass, v1_detail = assert_v1_cuts_on_beat_grid(
        rhythm_path, plan_path, beat_period=beat_period, bar_period=bar_period,
    )
    results["V1"] = _print_assertion(
        "V1", v1_pass,
        f"all cuts land on beat-grid (tolerance ±{CUT_ALIGN_TOLERANCE}s) "
        f"n_rhythm_cuts={v1_detail.get('n_rhythm_cuts')} "
        f"n_aligned={v1_detail.get('n_aligned')}",
    )
    if not v1_pass:
        print(f"     errors: {v1_detail.get('errors')}", flush=True)

    # V2
    v2_pass, v2_detail = assert_v2_shot_duration_beat_multiple(
        rhythm_path, beat_period=beat_period,
    )
    results["V2"] = _print_assertion(
        "V2", v2_pass,
        f"all shot durations ∈ beat-multiple "
        f"(tolerance ±{DURATION_ALIGN_TOLERANCE}s) "
        f"n_shots={v2_detail.get('n_shots')} n_aligned={v2_detail.get('n_aligned')}",
    )
    if not v2_pass:
        print(f"     errors: {v2_detail.get('errors')}", flush=True)

    # V3
    v3_pass, v3_detail = assert_v3_fancy_budget_and_dedup(
        plan_path, budget_max=FANCY_BUDGET_MAX,
    )
    results["V3"] = _print_assertion(
        "V3", v3_pass,
        f"fancy transitions ≤ budget_max & deduped "
        f"(fancy_count={v3_detail.get('fancy_count')}/"
        f"{v3_detail.get('budget_max')}, "
        f"types={v3_detail.get('fancy_types_used')}, "
        f"dup={v3_detail.get('duplicates')})",
    )
    if not v3_pass:
        print(f"     bad_types: {v3_detail.get('bad_types')}", flush=True)

    # V4
    v4_pass, v4_detail = assert_v4_three_phase_breathing(rhythm_path)
    results["V4"] = _print_assertion(
        "V4", v4_pass,
        f"slow_open/fast_middle/slow_tail breathing present "
        f"(phases={v4_detail.get('phases')}, order_ok={v4_detail.get('order_ok')})",
    )

    # V5
    v5_pass, v5_detail = assert_v5_speed_params_legal(rhythm_path)
    results["V5"] = _print_assertion(
        "V5", v5_pass,
        f"speed params legal "
        f"(n_shots={v5_detail.get('n_shots')} n_valid={v5_detail.get('n_valid')} "
        f"factor_range={v5_detail.get('factor_range')})",
    )
    if not v5_pass:
        print(f"     errors: {v5_detail.get('errors')}", flush=True)

    print("=" * 60, flush=True)
    all_pass = all(results.values())
    summary = ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items())
    print(f"[summary] {summary}", flush=True)
    print(f"[summary] all_pass = {all_pass}", flush=True)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
