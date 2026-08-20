#!/usr/bin/env python3
"""v3.5 转场规划器：按相邻镜头内容语义匹配转场类型（per 任务书 v3.5）。

vs v3.4 关键差异：

- 不再写死转场词：转场类型由相邻镜头 (i, i+1) 的内容语义信号决定
- 决策依据：角色焦点 / 共享物体 / 共享色板 / 共享场景 / 运动方向延续
- 默认倾向 hard_cut / 短 dissolve (0.2-0.4s)；花哨 (fadeblack/fade) 全局 ≤ 2
- **确定性**：相同 storyboard 两次运行结果一致（无随机）
- 每个转场都有 `reason` 字段说明为什么选这个（来自内容语义）

CLI:
  python transition_planner_v35.py --storyboard <sb.json> --out <plan.json>
  python transition_planner_v35.py --dry-run   # 用内嵌 SHOTS 样例跑一遍
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v35" / "sb" / "storyboard_v35.json"
DEFAULT_OUT = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35.json"

# ----- 允许的转场类型枚举（v35 锁定：4 种；花哨 = fadeblack + fade）-----
ALLOWED_TYPES = ("hard_cut", "dissolve", "fadeblack", "fade")
FANCY_TYPES = ("fadeblack", "fade")
PLAIN_TYPES = ("hard_cut", "dissolve")
FANCY_BUDGET_MAX = 2

# ----- 转场时长约束（per 任务书 Step 2 默认倾向短）-----
DUR_HARD_CUT = 0.0
DUR_DISSOLVE = 0.3      # 短 dissolve (0.2-0.4s)
DUR_DISSOLVE_LONG = 0.4 # 强连续时稍长
DUR_FADEBLACK = 0.5
DUR_FADE = 0.6

# ----- 语义信号词典：用于从 scene + timed_shot_list 文本抽取 token -----
KEY_OBJECTS = (
    "ring", "petal", "ribbon", "cardigan", "blazer", "column",
    "book", "garden", "cat", "cherry tree", "cherry petal",
    "frame", "pendant", "hair", "eye", "hand", "tower",
    "checkerboard", "barrette",
)
KEY_COLORS = (
    "magenta", "cyan", "lemon yellow", "neon CMYK", "cobalt",
    "navy", "cream", "halftone", "fluorescent green", "rainbow-gradient",
)
KEY_LOCATIONS = (
    "garden", "column", "cherry tree", "frame", "studio", "campus",
)
MOTION_VERBS_WALKING = ("walking", "walk away", "side by side", "back-to-back")

_TOKEN_RE_CACHE = {k: re.compile(r"\b" + re.escape(k) + r"\b", re.IGNORECASE)
                    for k in KEY_OBJECTS + KEY_COLORS + KEY_LOCATIONS}


def _contains_token(text: str, token: str) -> bool:
    """case-insensitive substring match，对多词 token 直接用 in。"""
    if not text:
        return False
    pat = _TOKEN_RE_CACHE.get(token)
    if pat is not None:
        return bool(pat.search(text))
    return token.lower() in text.lower()


def compute_shot_signals(shot: dict) -> dict:
    """从单段 shot 抽取语义信号向量（不依赖随机）。"""
    scene = shot.get("scene", "") or ""
    timeline = shot.get("timed_shot_list", []) or []
    timeline_text = " ".join(timeline)
    full = scene + " " + timeline_text

    # 1) 角色焦点（结构化字段，不是从文本推断）
    char_focus = shot.get("char_focus", "")
    has_senior = bool(shot.get("include_senior"))
    has_junior = bool(shot.get("include_junior"))

    # 2) 物体 / 色板 / 场景 token
    objects = {tok for tok in KEY_OBJECTS if _contains_token(full, tok)}
    colors = {tok for tok in KEY_COLORS if _contains_token(full, tok)}
    locations = {tok for tok in KEY_LOCATIONS if _contains_token(full, tok)}

    # 3) 运动：camera.type + 第一/最后 timeline 行
    cam = shot.get("camera", {}) or {}
    cam_type = cam.get("type", "")
    walking = any(_contains_token(full, v) for v in MOTION_VERBS_WALKING)

    # 4) 终幅/起幅 cue
    ending_cue = shot.get("ending_frame_cue", "")

    return {
        "index": shot.get("index"),
        "char_focus": char_focus,
        "has_senior": has_senior,
        "has_junior": has_junior,
        "objects": frozenset(objects),
        "colors": frozenset(colors),
        "locations": frozenset(locations),
        "cam_type": cam_type,
        "walking": walking,
        "ending_cue": ending_cue,
        # 给 reason 用
        "duration_sec": shot.get("duration_sec", 10.0),
    }


def _char_continuity_score(a: dict, b: dict) -> tuple[int, str]:
    """角色焦点延续性分值 (0..3)。"""
    af, bf = a["char_focus"], b["char_focus"]
    same_lead = (af == bf) and af in ("senior", "junior")
    same_group = (af == bf) and af == "both"
    solo_to_group = (af in ("senior", "junior")) and (bf == "both")
    group_to_solo = (af == "both") and (bf in ("senior", "junior"))
    swap = (af == "senior" and bf == "junior") or (af == "junior" and bf == "senior")
    if same_lead:
        return 3, f"same character focus ({af})"
    if same_group:
        return 3, f"same group scene ({af})"
    if solo_to_group:
        return 1, f"introduce partner ({af} → both)"
    if group_to_solo:
        return 1, f"split to solo ({af} → {bf})"
    if swap:
        return 0, f"swap character focus ({af} → {bf})"
    return 0, "character focus differs"


def _token_continuity(a_set: frozenset, b_set: frozenset, kind: str) -> tuple[int, str]:
    """通用 token 集合延续分值：|A∩B|。"""
    common = sorted(a_set & b_set)
    n = len(common)
    if n == 0:
        return 0, f"no shared {kind}"
    if n == 1:
        return 1, f"shared {kind}: {common[0]}"
    return min(n, 3), f"shared {kind}×{n}: {', '.join(common[:4])}"


def pair_score(a: dict, b: dict) -> tuple[int, dict]:
    """相邻镜头对 (a, b) 的内容语义总分（决定转场类型）。"""
    char_score, char_reason = _char_continuity_score(a, b)
    obj_score, obj_reason = _token_continuity(a["objects"], b["objects"], "objects")
    color_score, color_reason = _token_continuity(a["colors"], b["colors"], "colors")
    loc_score, loc_reason = _token_continuity(a["locations"], b["locations"], "location")

    motion_bonus = 0
    motion_reason = "no motion continuation"
    if a["walking"] and b["walking"]:
        motion_bonus = 2
        motion_reason = "both walking (motion continuation)"
    elif a["cam_type"] and a["cam_type"] == b["cam_type"]:
        motion_bonus = 1
        motion_reason = f"same camera type ({a['cam_type']})"

    score = char_score + obj_score + color_score + loc_score + motion_bonus
    return score, {
        "char": {"score": char_score, "reason": char_reason},
        "objects": {"score": obj_score, "reason": obj_reason},
        "colors": {"score": color_score, "reason": color_reason},
        "location": {"score": loc_score, "reason": loc_reason},
        "motion": {"score": motion_bonus, "reason": motion_reason},
        "total": score,
    }


def decide(score: int, detail: dict, fancy_used: int) -> tuple[str, float, str]:
    """根据 score + fancy_used 决定 (transition_type, duration, reason)。"""
    char_reason = detail["char"]["reason"]
    obj_reason = detail["objects"]["reason"]
    color_reason = detail["colors"]["reason"]
    loc_reason = detail["location"]["reason"]
    motion_reason = detail["motion"]["reason"]
    s = score

    fancy_left = FANCY_BUDGET_MAX - fancy_used

    # ---- 强延续：score 高 → dissolve (plain)，不需要花哨 ----
    if s >= 6:
        # 强延续 + 运动延续 → dissolve 长一点 (0.4s)
        if detail["motion"]["score"] >= 2:
            return ("dissolve", DUR_DISSOLVE_LONG,
                    f"strong content continuity (score={s}); {char_reason}; "
                    f"{motion_reason}; objects: {obj_reason}; use dissolve (plain)")
        return ("dissolve", DUR_DISSOLVE,
                f"strong content continuity (score={s}); {char_reason}; "
                f"objects: {obj_reason}; colors: {color_reason}; use dissolve (plain)")

    if s >= 3:
        # 中等延续 → dissolve (plain, 短)
        return ("dissolve", DUR_DISSOLVE,
                f"moderate continuity (score={s}); {char_reason}; "
                f"objects: {obj_reason}; use short dissolve (plain)")

    # ---- 弱延续 / 无延续：默认 hard_cut ----
    # 但有同色板 + 同场景（强风格延续） → 用 fade（花哨，预算内）
    if (detail["colors"]["score"] >= 2 and detail["location"]["score"] >= 1
            and fancy_left > 0):
        return ("fade", DUR_FADE,
                f"strong style/palette continuation only (score={s}); "
                f"{color_reason}; {loc_reason}; use fade (fancy, budget {fancy_left}→{fancy_left-1})")

    # 同色板 + 运动延续（场景切换但风格统一）→ fadeblack（花哨，预算内）
    if (detail["motion"]["score"] >= 2 and detail["colors"]["score"] >= 1
            and fancy_left > 0):
        return ("fadeblack", DUR_FADEBLACK,
                f"motion continuation with style unity (score={s}); "
                f"{motion_reason}; {color_reason}; use fadeblack (fancy, budget {fancy_left}→{fancy_left-1})")

    # 默认：硬切
    return ("hard_cut", DUR_HARD_CUT,
            f"strong content contrast (score={s}); {char_reason}; "
            f"{obj_reason}; use hard_cut (default)")


def plan_transitions(shots: list[dict]) -> list[dict]:
    """核心函数：shots → transition_plan。"""
    if not shots:
        return []
    sigs = [compute_shot_signals(s) for s in shots]
    plan: list[dict] = []
    fancy_used = 0
    cursor = 0.0

    for i in range(len(shots) - 1):
        a_sig = sigs[i]
        b_sig = sigs[i + 1]
        score, detail = pair_score(a_sig, b_sig)
        t_type, duration, reason = decide(score, detail, fancy_used)
        if t_type in FANCY_TYPES:
            fancy_used += 1
        cursor += shots[i].get("duration_sec", 10.0)
        plan.append({
            "at_sec": round(cursor, 4),
            "from_shot": shots[i].get("index"),
            "to_shot": shots[i + 1].get("index"),
            "transition_type": t_type,
            "duration": round(duration, 3),
            "reason": reason,
            "score": score,
            "signals_detail": detail,
        })

    return plan


def plan_meta(shots: list[dict], plan: list[dict]) -> dict:
    total = sum(s.get("duration_sec", 10.0) for s in shots)
    return {
        "planner_version": "v3.5",
        "pipeline": "ai-video-pipeline v3.5",
        "allowed_types": list(ALLOWED_TYPES),
        "fancy_budget_max": FANCY_BUDGET_MAX,
        "fancy_types": list(FANCY_TYPES),
        "plain_types": list(PLAIN_TYPES),
        "n_shots": len(shots),
        "n_transitions": len(plan),
        "total_duration_sec": round(total, 4),
        "type_counts": {t: sum(1 for p in plan if p["transition_type"] == t)
                         for t in ALLOWED_TYPES},
        "deterministic": True,
        "rule_summary": [
            "score>=6 + motion continuation → dissolve (0.4s, plain)",
            "score>=6 (no motion) → dissolve (0.3s, plain)",
            "score 3..5 → short dissolve (0.3s, plain)",
            "score<3 + strong style continuation → fade (fancy, budget)",
            "score<3 + motion continuation + style unity → fadeblack (fancy, budget)",
            "score<3 default → hard_cut (0.0s)",
            "fancy (fadeblack+fade) global <= 2",
        ],
    }


# ----- CLI -----

# 内嵌最小 SHOTS（用于 --dry-run 自测；与 storyboard_v35.py 字面一致）
DRY_RUN_SHOTS = [
    {
        "index": 1, "title": "学姐开场", "duration_sec": 10, "downbeat_start": 0.0,
        "char_focus": "senior", "include_senior": True, "include_junior": False,
        "camera": {"type": "whip pan + push in", "amplitude": "medium", "speed": "fast"},
        "scene": ("extreme close-up of senior's right hand holding a cherry blossom petal, "
                  "silver ring catches neon magenta light, neon CMYK pop-art background with "
                  "halftone dot pattern in flat color blocks."),
        "timed_shot_list": [
            "[0-2s] extreme close-up of senior's hand reaching toward camera",
            "[2-4s] camera pushes in to silver ring on index finger catching magenta+cyan light",
            "[4-10s] medium close-up side profile of senior's face, rainbow-gradient iris visible",
        ],
        "ending_frame_cue": "senior's amber eye at frame edge, cyan-blue hair highlight dominant",
    },
    {
        "index": 2, "title": "学妹登场", "duration_sec": 10, "downbeat_start": 10.0,
        "char_focus": "junior", "include_senior": False, "include_junior": True,
        "camera": {"type": "whip pan + dolly out", "amplitude": "medium", "speed": "fast"},
        "scene": ("extreme close-up of junior's twin tails with bright orange ribbons, "
                  "yellow-green eye with rainbow iris flecks visible between hair strands, "
                  "cream sailor cardigan sleeve visible."),
        "timed_shot_list": [
            "[0-3s] extreme close-up of junior's twin tails with bright orange ribbons",
            "[3-5s] camera dollies back to reveal yellow-green eye between hair strands",
            "[5-10s] medium close-up side profile of junior's face, plush star pendant visible",
        ],
        "ending_frame_cue": "junior's yellow-green eye dominant, orange ribbon mid-frame",
    },
    {
        "index": 3, "title": "学姐氛围", "duration_sec": 10, "downbeat_start": 20.0,
        "char_focus": "senior", "include_senior": True, "include_junior": False,
        "camera": {"type": "pan left + tilt down", "amplitude": "medium", "speed": "medium"},
        "scene": ("senior leaning against a pop-art column reading a book, cobalt blue + "
                  "lemon yellow background with diagonal composition lines."),
        "timed_shot_list": [
            "[0-4s] medium shot of senior leaning against an abstract pop-art column, navy blazer",
            "[4-10s] medium close-up of senior turning head toward camera, cyan-blue hair highlight",
        ],
        "ending_frame_cue": "senior's face 3/4 view, cyan-blue hair highlight prominent",
    },
    {
        "index": 4, "title": "学妹活力", "duration_sec": 10, "downbeat_start": 30.0,
        "char_focus": "junior", "include_senior": False, "include_junior": True,
        "camera": {"type": "push in + tilt up", "amplitude": "small", "speed": "medium"},
        "scene": ("junior crouched feeding a small orange cat in a stylized garden, "
                  "electric magenta + lemon yellow halftone background."),
        "timed_shot_list": [
            "[0-4s] medium shot of junior crouched in a stylized garden, orange cat licks her palm",
            "[4-10s] push in to medium close-up of junior's smiling face, plush star pendant bobs",
        ],
        "ending_frame_cue": "junior's bright smile, orange ribbons mid-frame",
    },
    {
        "index": 5, "title": "双人对比", "duration_sec": 10, "downbeat_start": 40.0,
        "char_focus": "both", "include_senior": True, "include_junior": True,
        "camera": {"type": "whip pan + static", "amplitude": "large", "speed": "fast"},
        "scene": ("senior and junior standing back-to-back in a flat-color pop-art frame, "
                  "senior left in navy blazer, junior right in cream cardigan."),
        "timed_shot_list": [
            "[0-4s] medium two-shot: senior left + junior right standing back-to-back",
            "[4-10s] medium shot both facing camera, navy blazer vs cream cardigan",
        ],
        "ending_frame_cue": "both faces symmetric, neon CMYK pop-art background dominant",
    },
    {
        "index": 6, "title": "收束", "duration_sec": 10, "downbeat_start": 50.0,
        "char_focus": "both", "include_senior": True, "include_junior": True,
        "camera": {"type": "pull back + tilt up", "amplitude": "medium", "speed": "slow"},
        "scene": ("senior and junior walking away side by side under a stylized cherry tree, "
                  "magenta+cyan+lemon yellow halftone overlay."),
        "timed_shot_list": [
            "[0-4s] wide back-view both walking away under stylized cherry tree",
            "[4-10s] extreme wide back-view both figures get smaller, neon CMYK gradient sky",
        ],
        "ending_frame_cue": "both silhouettes small in frame, neon CMYK gradient sky dominant",
    },
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--dry-run", action="store_true",
                    help="用内嵌 DRY_RUN_SHOTS 跑一遍，不读 storyboard，不写 plan.json")
    args = ap.parse_args(argv)

    if args.dry_run:
        shots = DRY_RUN_SHOTS
        print("[plan-dry-run] 使用内嵌 DRY_RUN_SHOTS (6 shots)", flush=True)
    else:
        sb_path = Path(args.storyboard)
        if not sb_path.exists():
            print(f"ERROR: storyboard 不存在 {sb_path}", file=sys.stderr)
            return 2
        sb = json.loads(sb_path.read_text(encoding="utf-8"))
        shots = sb.get("shots", [])

    plan = plan_transitions(shots)
    meta = plan_meta(shots, plan)

    if not args.dry_run:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "plan_meta": meta,
            "transitions": plan,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[plan] 转场规划 -> {out_path}", flush=True)
    else:
        # dry-run 也写到一份带时间戳的临时 plan，方便 validate 读取
        dry_path = ROOT / "output" / "pipeline_v35" / "sb" / "transition_plan_v35_dryrun.json"
        dry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "plan_meta": meta,
            "transitions": plan,
        }
        dry_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[plan-dry-run] dry-run 输出 -> {dry_path}", flush=True)

    print(f"[plan] {len(plan)} 个转场; type_counts={meta['type_counts']}", flush=True)
    print(f"[plan] fancy used = {meta['type_counts'].get('fadeblack', 0) + meta['type_counts'].get('fade', 0)} / {FANCY_BUDGET_MAX}",
          flush=True)
    for p in plan:
        print(f"  → t={p['at_sec']:.1f}s  shot{p['from_shot']}→shot{p['to_shot']}  "
              f"{p['transition_type']:<10s} ({p['duration']:.2f}s)  "
              f"score={p['score']}  | {p['reason']}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
