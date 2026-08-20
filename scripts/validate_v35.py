#!/usr/bin/env python3
"""v3.5 自检：转场治理四断言（per 任务书 v3.5 Step 4）。

断言 1 (P1)：storyboard_v35 所有 shot 的 scene + timed_shot_list 文本，
             正则扫 `(?i)(explode|burst|wipe|slash|split|color .*trans|
             transition)` 命中数 == 0
断言 2 (P2)：transition_plan_v35 对相同 storyboard 两次运行结果一致
             （确定性，非随机）
断言 3 (P3)：plan 里所有 transition_type ∈ {hard_cut, dissolve,
             fadeblack, fade} 且全局花哨类（fadeblack/fade 之外）≤ 2
             —— 注意：花哨 = {fadeblack, fade}；plain = {hard_cut,
             dissolve}；本断言要求 fancy count ≤ 2
断言 4 (P4)：plan 的 at_sec 单调递增且在 [0, total_duration]

退出码：全 PASS 返回 0；任一 FAIL 返回 1。

CLI:
  python validate_v35.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")

# 默认路径
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v35" / "sb" / "storyboard_v35.json"
DEFAULT_PLAN = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35.json"

# 转场类型枚举（与 transition_planner_v35.py 对齐）
ALLOWED_TYPES = ("hard_cut", "dissolve", "fadeblack", "fade")
FANCY_TYPES = ("fadeblack", "fade")
PLAIN_TYPES = ("hard_cut", "dissolve")
FANCY_BUDGET_MAX = 2

# P1 禁用词正则（per 任务书 Step 4 断言 1）
TRANSITION_BANNED_RE = re.compile(
    r"(?i)(explode|burst|wipe|slash|split|color\s+\w*\s*trans|transition)"
)


def _print_assertion(name: str, passed: bool, detail: str = "") -> bool:
    tag = "PASS" if passed else "FAIL"
    print(f"[{name}] {tag} {detail}", flush=True)
    return passed


def assert_p1_no_transition_words(storyboard_path: Path) -> tuple[bool, dict]:
    """断言 1：storyboard_v35 全 shot 文本无转场词残留。"""
    if not storyboard_path.exists():
        return False, {"error": f"storyboard 不存在: {storyboard_path}"}
    sb = json.loads(storyboard_path.read_text(encoding="utf-8"))
    shots = sb.get("shots", [])
    hits: list[dict] = []
    for shot in shots:
        idx = shot.get("index")
        scene = shot.get("scene", "") or ""
        timeline = shot.get("timed_shot_list", []) or []
        for src_name, text in [("scene", scene)] + [(f"timed_shot_list[{i}]", ln)
                                                      for i, ln in enumerate(timeline)]:
            for m in TRANSITION_BANNED_RE.finditer(text):
                hits.append({
                    "shot": idx,
                    "source": src_name,
                    "match": m.group(0),
                    "context": text[max(0, m.start() - 20):m.end() + 20],
                })
    passed = (len(hits) == 0)
    return passed, {"n_hits": len(hits), "hits": hits}


def assert_p2_deterministic(storyboard_path: Path) -> tuple[bool, dict]:
    """断言 2：相同 storyboard 两次 plan 结果一致（确定性）。"""
    # 导入 planner 函数（不在 dry-run 模式下写文件，直接调用纯函数）
    sys.path.insert(0, str(ROOT / "scripts"))
    from transition_planner_v35 import plan_transitions, DRY_RUN_SHOTS  # type: ignore

    if storyboard_path.exists():
        sb = json.loads(storyboard_path.read_text(encoding="utf-8"))
        shots = sb.get("shots", [])
    else:
        shots = DRY_RUN_SHOTS

    plan1 = plan_transitions(shots)
    plan2 = plan_transitions(shots)
    plan3 = plan_transitions(shots)
    same12 = (plan1 == plan2)
    same13 = (plan1 == plan3)
    passed = same12 and same13
    return passed, {
        "shots_source": "storyboard" if storyboard_path.exists() else "DRY_RUN_SHOTS",
        "n_transitions": len(plan1),
        "same_run_1_2": same12,
        "same_run_1_3": same13,
    }


def assert_p3_type_budget(plan_path: Path) -> tuple[bool, dict]:
    """断言 3：所有 transition_type ∈ ALLOWED_TYPES 且 fancy 数量 ≤ 2。"""
    if not plan_path.exists():
        return False, {"error": f"plan 不存在: {plan_path}"}
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    transitions = data.get("transitions", [])

    bad_types: list[dict] = []
    counts = {t: 0 for t in ALLOWED_TYPES}
    for i, t in enumerate(transitions):
        tt = t.get("transition_type", "")
        if tt not in ALLOWED_TYPES:
            bad_types.append({"index": i, "type": tt})
        else:
            counts[tt] += 1
    fancy_count = sum(counts[t] for t in FANCY_TYPES)
    passed = (len(bad_types) == 0) and (fancy_count <= FANCY_BUDGET_MAX)
    return passed, {
        "counts": counts,
        "fancy_count": fancy_count,
        "fancy_budget_max": FANCY_BUDGET_MAX,
        "bad_types": bad_types,
    }


def assert_p4_at_sec_monotonic(plan_path: Path) -> tuple[bool, dict]:
    """断言 4：at_sec 单调递增且在 [0, total_duration]。"""
    if not plan_path.exists():
        return False, {"error": f"plan 不存在: {plan_path}"}
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    transitions = data.get("transitions", [])
    meta = data.get("plan_meta", {})
    total = meta.get("total_duration_sec", 0.0)

    at_secs = [t.get("at_sec", -1) for t in transitions]
    errors: list[str] = []
    if not at_secs:
        errors.append("no transitions")
    else:
        # 单调递增（严格）
        for i in range(1, len(at_secs)):
            if at_secs[i] <= at_secs[i - 1]:
                errors.append(f"non-monotonic at i={i}: {at_secs[i-1]} → {at_secs[i]}")
        # 范围
        if at_secs[0] < 0:
            errors.append(f"first at_sec out of range: {at_secs[0]}")
        if at_secs[-1] > total + 0.001:
            errors.append(f"last at_sec {at_secs[-1]} > total_duration {total}")
    passed = (len(errors) == 0)
    return passed, {
        "at_secs": at_secs,
        "total_duration_sec": total,
        "errors": errors,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    args = ap.parse_args(argv)

    sb_path = Path(args.storyboard)
    plan_path = Path(args.plan)

    print("=" * 60, flush=True)
    print("v3.5 转场治理 自检 (validate_v35.py)", flush=True)
    print("=" * 60, flush=True)
    print(f"[setup] storyboard = {sb_path}", flush=True)
    print(f"[setup] plan       = {plan_path}", flush=True)

    results: dict[str, tuple[bool, dict]] = {}

    # P1：storyboard 无转场词
    p1_pass, p1_detail = assert_p1_no_transition_words(sb_path)
    results["P1"] = _print_assertion(
        "P1", p1_pass,
        f"no transition words in storyboard_v35 (hits={p1_detail.get('n_hits', '?')})"
    ) or results.get("P1", False)
    if not p1_pass:
        print(f"     hits detail: {json.dumps(p1_detail.get('hits', []), ensure_ascii=False)[:400]}",
              flush=True)
    results["P1"] = p1_pass

    # P2：确定性
    p2_pass, p2_detail = assert_p2_deterministic(sb_path)
    results["P2"] = _print_assertion(
        "P2", p2_pass,
        f"plan_transitions deterministic over 3 runs "
        f"(same_1_2={p2_detail.get('same_run_1_2')}, same_1_3={p2_detail.get('same_run_1_3')})"
    )
    print(f"     detail: {p2_detail}", flush=True)

    # P3：type 枚举 + fancy budget
    p3_pass, p3_detail = assert_p3_type_budget(plan_path)
    results["P3"] = _print_assertion(
        "P3", p3_pass,
        f"types ∈ allowed & fancy≤budget "
        f"(counts={p3_detail.get('counts')}, fancy={p3_detail.get('fancy_count')}/"
        f"{FANCY_BUDGET_MAX})"
    )
    if not p3_pass:
        print(f"     bad_types: {p3_detail.get('bad_types')}", flush=True)

    # P4：at_sec 单调
    p4_pass, p4_detail = assert_p4_at_sec_monotonic(plan_path)
    results["P4"] = _print_assertion(
        "P4", p4_pass,
        f"at_sec monotonic in [0, total] "
        f"(at_secs={p4_detail.get('at_secs')}, total={p4_detail.get('total_duration_sec')})"
    )
    if not p4_pass:
        print(f"     errors: {p4_detail.get('errors')}", flush=True)

    # 总结
    print("=" * 60, flush=True)
    all_pass = all(results.values())
    summary = ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items())
    print(f"[summary] {summary}", flush=True)
    print(f"[summary] all_pass = {all_pass}", flush=True)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
