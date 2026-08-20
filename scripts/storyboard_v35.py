#!/usr/bin/env python3
"""v3.5 分镜表：生成层去转场词（per 任务书 v3.5）。

vs storyboard_v32.py 关键差异：

- 删除每个 shot 的 transition_in / transition_out 字段（不再写死转场类型）
- scene 描述重写：只描述镜头的内容、画面、构图、角色动作，**不含任何
  转场特效词**（explode / burst / wipe / slash / split / ink / radial
  sunburst 等）。scene 保留「静态视觉内容」描述。
- timed_shot_list 删除所有 TRANSITION 行（如 "COLOR EXPLOSION TRANSITION
  — ..." 整行删除），只保留镜头内容的时间线。
- 同一题材「选学姐还是学妹」双角色对比，结构对齐 v32：6 段 × 10s = 60s。
- 转场规划职责从生成层剥离，由 transition_planner_v35.py 在剪辑层按
  内容语义匹配。

CLI:
  python storyboard_v35.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
OUT_PATH = ROOT / "output" / "pipeline_v35" / "sb" / "storyboard_v35.json"
BEATS_JSON = ROOT / "output" / "pipeline_v3" / "music" / "beats.json"

SEG_DURATIONS = [10, 10, 10, 10, 10, 10]
N_SHOTS = len(SEG_DURATIONS)

# 6 段 MV 风格分镜（学姐 vs 学妹 双角色对比，**不含任何转场特效词**）
#
# 设计要点：
#   - scene 描述：静态视觉内容（角色 / 构图 / 光线 / 服装 / 背景静态图案）
#   - timed_shot_list：只描述镜头内容的时间线，不出现 TRANSITION 行
#   - camera / include_senior / include_junior / ending_frame_cue 全部保留
#   - **不出现任何 transition_* 字段**（由 transition_planner_v35.py 接管）
SHOTS = [
    {
        "index": 1,
        "title": "学姐开场",
        "duration_sec": 10,
        "downbeat_start": 0.0,
        "char_focus": "senior",
        "include_senior": True,
        "include_junior": False,
        "camera": {"type": "whip pan + push in", "amplitude": "medium", "speed": "fast"},
        "scene": (
            "extreme close-up of senior's right hand holding a cherry blossom "
            "petal, silver ring catches neon magenta light, neon CMYK pop-art "
            "background with halftone dot pattern in flat color blocks. Static "
            "pop-art sticker of a star shape in upper-left corner. Cel-shaded "
            "flat color, hand-painted anime lineart."
        ),
        "timed_shot_list": [
            "[0-2s] extreme close-up of senior's hand reaching toward camera from off-screen left, neon CMYK background with halftone dot pattern, comic panel grid overlay as static composition frame",
            "[2-4s] camera pushes in to extreme close-up of silver ring on index finger catching magenta+cyan light, hand slowly rotates to reveal cherry petal",
            "[4-10s] medium close-up side profile of senior's face, rainbow-gradient iris visible, long black hair with cyan-blue highlight strands catches fluorescent green rim light, abstract radial line background pulses softly behind her"
        ],
        "ending_frame_cue": "senior's amber eye at frame edge, cyan-blue hair highlight dominant",
    },
    {
        "index": 2,
        "title": "学妹登场",
        "duration_sec": 10,
        "downbeat_start": 10.0,
        "char_focus": "junior",
        "include_senior": False,
        "include_junior": True,
        "camera": {"type": "whip pan + dolly out", "amplitude": "medium", "speed": "fast"},
        "scene": (
            "extreme close-up of junior's twin tails with bright orange ribbons "
            "fluttering, yellow-green eye with rainbow iris flecks visible "
            "between hair strands, cream sailor cardigan sleeve visible. "
            "Background: halftone dot pattern in flat color blocks, static ink "
            "splash sticker as composition element. Cel-shaded with bright flat "
            "colors."
        ),
        "timed_shot_list": [
            "[0-3s] extreme close-up of junior's twin tails with bright orange ribbons fluttering, fabric whip across foreground, halftone dots dominant",
            "[3-5s] camera dollies back to reveal yellow-green eye with rainbow iris flecks between hair strands, cheek with brighter blush, eye looks directly at camera",
            "[5-10s] medium close-up side profile of junior's face, chestnut twin tails bouncing, abstract checkerboard tiles rotate behind her, plush star pendant visible on orange ribbon at neck"
        ],
        "ending_frame_cue": "junior's yellow-green eye dominant, orange ribbon mid-frame",
    },
    {
        "index": 3,
        "title": "学姐氛围",
        "duration_sec": 10,
        "downbeat_start": 20.0,
        "char_focus": "senior",
        "include_senior": True,
        "include_junior": False,
        "camera": {"type": "pan left + tilt down", "amplitude": "medium", "speed": "medium"},
        "scene": (
            "senior leaning against a pop-art column reading a book, cobalt "
            "blue + lemon yellow background with diagonal composition lines, "
            "static ink splash sticker in upper-right corner. Cel-shaded flat "
            "color blocks, no 3D render."
        ),
        "timed_shot_list": [
            "[0-4s] medium shot of senior leaning against an abstract pop-art column, navy blazer with white piping sharp against cobalt blue flat background, silver ring on index finger holds the book edge, eyes reading",
            "[4-10s] medium close-up of senior turning head toward camera, cyan-blue hair highlight catches fluorescent green rim light, rainbow-gradient iris appears, faint smile on lips"
        ],
        "ending_frame_cue": "senior's face 3/4 view, cyan-blue hair highlight prominent",
    },
    {
        "index": 4,
        "title": "学妹活力",
        "duration_sec": 10,
        "downbeat_start": 30.0,
        "char_focus": "junior",
        "include_senior": False,
        "include_junior": True,
        "camera": {"type": "push in + tilt up", "amplitude": "small", "speed": "medium"},
        "scene": (
            "junior crouched feeding a small orange cat in a stylized garden, "
            "electric magenta + lemon yellow halftone background, static comic "
            "panel grid overlay in lower-right corner. Cel-shaded with bright "
            "flat colors."
        ),
        "timed_shot_list": [
            "[0-4s] medium shot of junior crouched in a stylized flat-shaded garden, orange cat licks her palm, twin tails spill over her shoulders, yellow-green eyes smile down at cat",
            "[4-10s] push in to medium close-up of junior's smiling face, plush star pendant bobs on orange ribbon, halftone dots pulse softly behind her in lemon yellow"
        ],
        "ending_frame_cue": "junior's bright smile, orange ribbons mid-frame",
    },
    {
        "index": 5,
        "title": "双人对比",
        "duration_sec": 10,
        "downbeat_start": 40.0,
        "char_focus": "both",
        "include_senior": True,
        "include_junior": True,
        "camera": {"type": "whip pan + static", "amplitude": "large", "speed": "fast"},
        "scene": (
            "senior and junior standing back-to-back in a flat-color pop-art "
            "frame, senior left in navy blazer, junior right in cream cardigan. "
            "Background divided in two halves: left half magenta radial lines, "
            "right half cyan checkerboard."
        ),
        "timed_shot_list": [
            "[0-4s] medium two-shot: senior left + junior right standing back-to-back, navy blazer vs cream cardigan, silver ring on senior's hand + orange ribbons on junior's tails visible at frame edges, neon CMYK background divided into magenta+cyan zones",
            "[4-10s] medium shot both facing camera, senior with composed faint smile + junior with curious bright smile, fluorescent green+lemon yellow ink splash sticker behind them, halftone dots pulse softly"
        ],
        "ending_frame_cue": "both faces symmetric, neon CMYK pop-art background dominant",
    },
    {
        "index": 6,
        "title": "收束",
        "duration_sec": 10,
        "downbeat_start": 50.0,
        "char_focus": "both",
        "include_senior": True,
        "include_junior": True,
        "camera": {"type": "pull back + tilt up", "amplitude": "medium", "speed": "slow"},
        "scene": (
            "senior and junior walking away side by side under a stylized "
            "cherry tree, magenta+cyan+lemon yellow halftone overlay across "
            "the whole frame, static comic panel border around frame as "
            "composition element. Cel-shaded flat color blocking."
        ),
        "timed_shot_list": [
            "[0-4s] wide back-view both walking away under stylized cherry tree, senior on left in navy blazer, junior on right in cream cardigan, neon CMYK cherry petals drift around them",
            "[4-10s] extreme wide back-view both figures get smaller, abstract flat-color campus silhouettes rise behind them, fluorescent green+lemon yellow gradient sky, static comic panel border frames the shot"
        ],
        "ending_frame_cue": "both silhouettes small in frame, neon CMYK gradient sky dominant",
    },
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--beats", default=str(BEATS_JSON))
    args = ap.parse_args(argv)

    beats_path = Path(args.beats)
    if not beats_path.exists():
        print(f"ERROR: beats.json 不存在 {beats_path}", file=sys.stderr)
        return 2
    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    beats = beats_data["beats"]
    downbeats = beats_data["downbeats"]

    for shot in SHOTS:
        ds = shot["downbeat_start"]
        if ds % 2.0 != 0:
            print(f"WARN: shot{shot['index']} 起点 {ds}s 不在 downbeat (2s 整数倍)", flush=True)
        if ds >= len(beats) * 0.5:
            print(f"WARN: shot{shot['index']} 起点 {ds}s 超出 BGM 时长 {len(beats)*0.5}s",
                  flush=True)

    # 注意：v35 不在 storyboard 里写 cut_points / transition_in / transition_out
    # 转场规划职责已剥离到 transition_planner_v35.py
    storyboard = {
        "title": "选学姐还是学妹？（MV 版 / v3.5 — 生成层去转场词）",
        "version": "v3.5",
        "pipeline": "ai-video-pipeline v3.5",
        "reference_video": "D:\\ai-video-pipeline\\input_h3_pv_ref.mp4",
        "reference_score": 68.5,
        "style_strategy": "plan_b_prompt_reinforcement + mai_yoneyama_cel",
        "lora_enabled": False,
        "target_resolution": "720x1280",
        "target_duration_sec": 60,
        "n_shots": N_SHOTS,
        "shots": SHOTS,
        "intro_card": {
            "duration_sec": 2.0,
            "start_sec": 0.0,
            "title_text": "COLOR RIOT",
            "subtitle_text": "选学姐还是学妹？",
            "engine": "pil_overlay",
        },
        "outro_card": {
            "duration_sec": 2.0,
            "start_sec": 58.0,
            "title_text": "THE CHOICE",
            "subtitle_text": "—— 你的答案 ——",
            "engine": "pil_overlay",
        },
        "bgm": {
            "source": "程序合成 (v3.5 复用 v3.2 — 6 层多轨 J-pop)",
            "bpm": 120.0,
            "duration_sec": 72.0,
            "file": "output/pipeline_v3/music/bgm_v32.wav",
            "beats_json": "output/pipeline_v3/music/beats.json",
        },
        "voiceover": "NONE — 全片零对白",
        "sound_effects": [
            "BGM-driven, no per-cut whoosh (whoosh removed in P1)",
            "soft chimes on intro card",
            "bass pulse under BGM",
        ],
        "transition_governance": {
            "policy": "transitions planned in editing layer by transition_planner_v35.py",
            "allowed_types": ["hard_cut", "dissolve", "fadeblack", "fade"],
            "fancy_budget_max": 2,
            "rationale": "shot scene + timed_shot_list intentionally free of transition words",
        },
        "vlm_dimensions": [
            "画面质量 1.0",
            "角色一致性 1.5",
            "镜头语言 1.5",
            "动作流畅度 1.0",
            "风格与氛围 1.0",
            "制作完成度 1.0",
            "档次定位 1.0",
        ],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"[sb-v35] 分镜表 -> {out_path}", flush=True)
    print(f"[sb-v35] 段数: {N_SHOTS}, 总时长: {sum(SEG_DURATIONS)}s", flush=True)
    print(f"[sb-v35] BGM 时长: {len(beats)*0.5:.1f}s, 拍点数: {len(beats)}, downbeats: {len(downbeats)}",
          flush=True)
    print(f"[sb-v35] 转场字段: 全部移除（由 transition_planner_v35.py 接管）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
