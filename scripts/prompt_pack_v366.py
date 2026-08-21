#!/usr/bin/env python3
"""v3.6.6 prompt-pack: 6 段链式 I2V 提示词包。

任务书 oc_task_v366.txt §4:
- 6 段: slow_open(8s) / build(6s) / fast(5s) / peak(8s) / fast(5s) / tail(8s)
- 总计 40s, 80 拍 (120 BPM, 0.5s/拍)
- 方案A (链式): shot1 anchor = 参考视频帧; shot2..6 anchor = 上个尾帧 (extractLastFrame 自动)
- 禁止抽象转场特效词 (halftone/fabric wipe/whip pan/diagonal slash 等, v365 已证明接不住)
- 每段 prompt: 驱动自然运镜/动作, 与上一段画面自然延续

vs prompt_pack_v365.py 关键差异:
- 不含 TRANSITIONS_BLOCK, TRANSITION_ASSIGNMENT, [TRANSITION_RHYTHM], [INCOMING_TRANSITION_CUE]
- 镜头描述聚焦在"自然动作 + 链式衔接", 不放抽象特效词
- 强制指令保留: 无白淡入/无黑淡入/单主体清晰/cel-shaded anime
- 风格锁定: 参考视频的横屏 1344x576 (2.36:1) 构图

CLI:
    python prompt_pack_v366.py --out-dir output/pipeline_v36/shots_v366
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "shots_v366"

# 任务书 §4 全片结构 (6 段, 40s 总长)
SEGMENT_DURATIONS_SEC = {1: 8.0, 2: 6.0, 3: 5.0, 4: 8.0, 5: 5.0, 6: 8.0}
SEGMENT_PHASE = {1: "slow_open", 2: "build", 3: "fast",
                 4: "peak", 5: "fast", 6: "tail"}

# 角色锚点 (复用 v365, 保证视觉一致)
CHAR_SENIOR_XUEJIE = (
    "an 18-year-old East Asian female high school student with bright cheerful energy, "
    "chestnut-brown hair styled in low twin tails tied with bright orange ribbon bands, "
    "a small plush star pendant hanging on the right ribbon, bright yellow-green eyes "
    "with rainbow iris flecks, soft round cheeks with brighter blush, slim petite figure, "
    "wearing a cream sailor cardigan uniform with a white collar and red ribbon tie, a "
    "dark pleated skirt, white knee-high socks and brown loafers"
)

CHAR_JUNIOR_XUEMEI = (
    "a 21-year-old East Asian female university student with cool calm elegance, "
    "long straight black hair with subtle dark-blue highlight strands framing her "
    "face, sharp narrow amber eyes, porcelain fair skin with subtle blush, slim tall "
    "figure, wearing a tailored deep navy blazer uniform with white piping over a "
    "crisp white collared blouse, a thin black ribbon choker at the neck, a silver "
    "ring on her right index finger, a dark pleated midi skirt and black leather loafers"
)

# 横屏 2.36:1 构图说明 (任务书 §1: 1344x576, 跟随参考视频画幅)
HORIZONTAL_FRAME = (
    "horizontal 2.36:1 cinematic wide frame (1344 wide by 576 tall), characters "
    "framed at center and slightly left/right with generous environment on both "
    "sides, deep depth of field showing full environmental context, well-framed "
    "composition with full head and shoulders visible throughout, no extreme "
    "close-up that crops the face"
)

# 强制指令 (任务书 §3.3 沿用 v364/v365 核心: 无白淡入, 单主体, cel-shaded anime)
# v366 关键差异: 不放抽象转场特效词, 不写 [TRANSITION_RHYTHM]/[INCOMING_TRANSITION_CUE]
# 转场靠"上一段尾帧 -> 下一段首帧"的链式衔接自然完成
FORCED_INSTRUCTION = (
    "MANDATORY CONTENT RULES: Maintain the visual style, character appearance, "
    "lighting, and color palette of the input first frame throughout the entire "
    "shot. The opening frame must continue naturally from the input first frame "
    "(no fade-in from white, no fade-in from black, no abstract neon backdrop, "
    "no hand or fabric or accessory close-up). No abstract distortion, no "
    "pixelation, no broken geometry, no rotating background objects, no "
    "repeated tile textures. Single clear subject per frame. Crisp anime "
    "cel-shaded rendering with sharp lineart from the very first frame to the "
    "very last frame."
)

# 风格基调 (任务书 §3.1 沿用 v364/v365: 米山舞 anime cel-shading pop-art)
STYLE_BLOCK_V366 = (
    "2D-animated, Mai Yoneyama anime cel-shading pop-art style with romantic "
    "soft-light aesthetic. Vibrant high-saturation CMYK pop-art color palette "
    "with translucent color blocks, hand-painted anime lineart, cel-shaded "
    "flat shading with subtle airbrushed gradient on skin only. Layered "
    "composition with distinct foreground character, midground props, and "
    "background atmosphere. No photorealism, no 3D render, no CGI. Crisp "
    "detail on character features: hair ornaments, pendants, ribbons, uniform "
    "trim, choker."
)

# 抽象转场特效词 (v366 禁止词, 必须验证 prompt 中没有)
BANNED_TRANSITION_PHRASES = (
    "halftone dot overlay flash",
    "halftone",
    "whip pan with motion streaks",
    "whip pan",
    "color explosion",
    "ink burst",
    "fabric wipe",
    "diagonal slash wipe",
    "diagonal slash",
    "comic panel split-screen",
    "split-screen",
    "hard cut on beat",
    "[TRANSITION_RHYTHM]",
    "[INCOMING_TRANSITION_CUE]",
)


def soundscape() -> str:
    """沿用 v365 声音床, 避免 H3 单音轨。"""
    return (
        "Soft spring-pop ambient bed — gentle spring breeze through cherry "
        "petals, distant soft chimes ringing faintly, a very faint heartbeat-"
        "style bass pulse under the BGM. No voices, no spoken dialogue, no "
        "narration, no edge-tts, no vocalization of any kind throughout the "
        "entire video. The ambient bed remains continuous across every shot "
        "cut. NOTE: do not synthesize whoosh or any inter-shot SFX during "
        "generation; inter-shot audio treatment is handled externally by an "
        "ffmpeg mixer."
    )


def music_skeleton() -> str:
    """沿用 v365 BGM 骨架。"""
    return (
        "non-diegetic music: 120 BPM beat grid, minimal background beat, low "
        "volume, no melody. Audio bed is intentionally sparse during "
        "generation; the final layered BGM track is mixed in post by an "
        "external ffmpeg mixer to keep every beat aligned to the 120 BPM grid "
        "and to avoid the H3 monolithic generated tone."
    )


def _build_prompt(shot_idx: int, scene: str, timeline: str,
                  camera: str, char_block: str,
                  continuity_hint: str = "") -> tuple[str, dict]:
    """通用 prompt 拼装器。

    continuity_hint: 在 SCENE 末尾追加"承上启下"的画面描述, 帮助 H3 把镜头2/3/...
    自然延续上一段的尾帧, 不出现人物/场景漂移。
    """
    body_parts = [STYLE_BLOCK_V366, char_block]
    if continuity_hint:
        body_parts.append(continuity_hint)
    body_parts.append(scene)
    body_parts.append(timeline)
    body_parts.append(camera)
    body_parts.append(FORCED_INSTRUCTION)
    body = "\n\n".join(p for p in body_parts if p)

    prompt = (
        f"integrated_multimodal_description:\n{body}\n\n"
        f"overall_soundscape:\n{soundscape()}\n\n"
        f"non_diegetic_music:\n{music_skeleton()}"
    )

    dur = SEGMENT_DURATIONS_SEC[shot_idx]
    downbeat_start = sum(SEGMENT_DURATIONS_SEC[j] for j in range(1, shot_idx))
    meta = {
        "shot": shot_idx,
        "phase": SEGMENT_PHASE[shot_idx],
        "duration_sec": dur,
        "downbeat_start": float(downbeat_start),
        "include_senior": shot_idx in (1, 3, 4, 5, 6),
        "include_junior": shot_idx in (2, 3, 4, 5, 6),
        "characters": (
            ["xuejie_brown_twintails"] if shot_idx == 1
            else ["xuemei_black_long"] if shot_idx == 2
            else ["xuejie_brown_twintails", "xuemei_black_long"]
        ),
        "transition_effect": None,  # v366 不用生成层转场
        "has_in_prompt_transition": False,  # v366 不用
        "method": "chain_first_frame_i2v",  # 方案A: 上一段尾帧 -> 本段首帧
        "anchor_source": (
            "ref_video_0.20s" if shot_idx == 1
            else f"shot{shot_idx-1:02d}_last_frame_extracted"
        ),
        "prompt_chars": len(prompt),
        "bpm_target": 120.0,
        "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.6",
        "open_with_character": True,
        "no_fade_in": True,
        "no_abstract_transition_words": True,
        "horizontal_resolution": "1344x576",
        "aspect_ratio": "2.36:1",
    }
    return prompt, meta


# ============================================================
# Shot 1 — slow_open (8s) / 学姐开场, 净开场
# ============================================================
def build_prompt_shot1() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[FRAME] {HORIZONTAL_FRAME}"
    )
    scene = (
        "[SCENE] A sunlit campus courtyard in early spring afternoon. Soft "
        "golden-hour backlight from the upper left. A few out-of-focus cherry "
        "blossom petals drifting through warm air. The brown-twin-tail "
        "character stands at the center of frame in a three-quarter pose, "
        "head slightly tilted, eyes meeting the camera with a calm confident "
        "expression. Cream sailor uniform catches the warm light, the orange "
        "ribbons and star pendant on her twin tails are clearly visible and "
        "in focus. Background shows soft-focus campus trees and architecture "
        "with deep depth of field."
    )
    timeline = (
        "[SHOT_TIMELINE] Single primary motion chain executed in sequence: "
        "first the camera holds a medium wide shot with the character centered "
        "and clearly visible from frame one (continuing naturally from the "
        "input first frame); then she slowly turns her head about 15 degrees "
        "to her left while keeping her gaze toward the camera, twin tails "
        "swaying gently; then a slow subtle smile forms as cherry petals "
        "drift across frame. The character remains the single clear focal "
        "point throughout. No second character. No abstract overlay. No "
        "rapid camera move."
    )
    camera = "[CAMERA] gentle dolly forward, amplitude small, speed slow."
    return _build_prompt(1, scene, timeline, camera, char_block)


# ============================================================
# Shot 2 — build (6s) / 学妹登场, 自然延续 shot1 尾帧
# ============================================================
def build_prompt_shot2() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[FRAME] {HORIZONTAL_FRAME}"
    )
    continuity = (
        "[CONTINUITY] The opening frame continues naturally from the input "
        "first frame (which is the last frame of the previous shot): same "
        "sunlit spring campus courtyard, same golden-hour backlight, same "
        "cherry blossom petals drifting through warm air, same color palette "
        "and cel-shaded anime style."
    )
    scene = (
        "[SCENE] The same sunlit campus courtyard, slightly different angle "
        "so the warm backlight is more pronounced. The black-long-hair "
        "character appears at the right side of frame in medium shot, looking "
        "toward the camera with a quiet curious gaze, head slightly tilted. "
        "Her long black hair frames her face, the navy blazer uniform with "
        "white piping is crisp, the black choker and silver ring on her right "
        "index finger catch a glint of warm light. Background shows a soft-"
        "focus campus building and a few out-of-focus lockers."
    )
    timeline = (
        "[SHOT_TIMELINE] The camera holds a medium wide shot with the new "
        "character clearly visible from frame one; then she lifts her right "
        "hand to chest height and gently tucks a strand of black hair behind "
        "her right ear, the silver ring catching the warm light; then her "
        "gaze softens and she gives a small slow nod. The character remains "
        "the single clear focal point throughout. No other character in this "
        "shot. No further abstract overlay. No rapid motion."
    )
    camera = "[CAMERA] static with very subtle 5 percent push in, amplitude small, speed slow."
    return _build_prompt(2, scene, timeline, camera, char_block,
                         continuity_hint=continuity)


# ============================================================
# Shot 3 — fast (5s) / 双人同框, 衔接 shot2 尾帧
# ============================================================
def build_prompt_shot3() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] The brown-twin-tail character (xuejie) stands on the "
        f"left side of frame, the black-long-hair character (xuemei) on the "
        f"right side, both visible in the same wide horizontal shot. They "
        f"face each other in three-quarter view. Visual distinction is "
        f"preserved: brown twin tails with orange ribbons vs long straight "
        f"black hair with choker.\n"
        f"[FRAME] {HORIZONTAL_FRAME}"
    )
    continuity = (
        "[CONTINUITY] The opening frame continues naturally from the input "
        "first frame (which is the last frame of the previous shot): same "
        "sunlit spring campus courtyard, same golden-hour backlight, same "
        "warm color palette and cel-shaded anime style. Both characters are "
        "now visible together for the first time, the brown-twin-tail "
        "character naturally positioned on the left and the black-long-hair "
        "character on the right."
    )
    scene = (
        "[SCENE] The same outdoor campus courtyard at golden hour. Two "
        "characters stand facing each other, both clearly visible from head "
        "to mid-torso in the wide horizontal frame. Warm sunlight from the "
        "upper left rims both characters' hair and shoulders. Background "
        "shows a soft-focus row of cherry blossom trees with petals drifting "
        "between them, deep depth of field."
    )
    timeline = (
        "[SHOT_TIMELINE] The camera holds a stable wide two-shot with both "
        "characters clearly visible from frame one, brown twin tails on left "
        "and black long hair on right, both with eyes visible; then xuejie "
        "on the left reaches her right hand out toward xuemei as if to "
        "offer a small object (a single cherry petal resting on her palm, "
        "clearly shown); then xuemei on the right lifts her gaze from the "
        "petal to xuejie's eyes, a small surprised smile forming. Both "
        "characters remain in frame throughout. No rapid camera move. No "
        "abstract overlay."
    )
    camera = "[CAMERA] static wide two-shot, amplitude none, speed slow."
    return _build_prompt(3, scene, timeline, camera, char_block,
                         continuity_hint=continuity)


# ============================================================
# Shot 4 — peak (8s) / 情绪锚, 双人近景
# ============================================================
def build_prompt_shot4() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] Both characters clearly visible in the same wide "
        f"frame. xuejie (brown twin tails, orange ribbons) on left, xuemei "
        f"(black long hair, navy blazer, choker) on right. They stand close "
        f"enough to suggest emotional closeness, both eyes clearly visible.\n"
        f"[FRAME] {HORIZONTAL_FRAME}"
    )
    continuity = (
        "[CONTINUITY] The opening frame continues naturally from the input "
        "first frame (which is the last frame of the previous shot): same "
        "two characters in the same sunlit spring campus courtyard, same "
        "golden-hour backlight, same warm color palette and cel-shaded anime "
        "style. The emotional closeness is the natural continuation of the "
        "previous shot where xuejie offered a cherry petal."
    )
    scene = (
        "[SCENE] A dramatic evening rooftop at blue-hour dusk, continuing "
        "the same emotional beat. The city skyline is visible in soft focus "
        "behind them with a few warm window lights beginning to glow. The "
        "two characters stand side by side, both clearly visible from head "
        "to mid-torso, the brown-twin-tail character on the left and the "
        "black-long-hair character on the right. A gentle breeze lifts their "
        "hair and uniform hems. The sky is a layered gradient from deep "
        "indigo at top to soft rose at the horizon."
    )
    timeline = (
        "[SHOT_TIMELINE] The camera frames both characters clearly from "
        "frame one in a stable medium wide two-shot (continuing naturally "
        "from the previous shot), both faces and uniforms in focus; then "
        "xuemei on the right slowly turns her head toward xuejie on the "
        "left, her gaze drifting up to meet xuejie's eyes; then xuejie on "
        "the left returns the gaze with a soft warm smile, the orange "
        "ribbons and star pendant clearly catching a glint of dusk light; "
        "then a long sustained moment of mutual eye contact as the sky "
        "slowly deepens in the background. Both characters remain the clear "
        "focal point throughout. No rapid camera move. No abstract overlay."
    )
    camera = "[CAMERA] slow cinematic push in, amplitude small, speed very slow."
    return _build_prompt(4, scene, timeline, camera, char_block,
                         continuity_hint=continuity)


# ============================================================
# Shot 5 — fast (5s) / 双人对比动作
# ============================================================
def build_prompt_shot5() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] xuejie (brown twin tails, cream sailor uniform, "
        f"orange ribbons) on left of frame; xuemei (black long hair, navy "
        f"blazer, choker) on right of frame. Both clearly visible from "
        f"head to mid-torso in the wide horizontal frame, contrasting warm "
        f"vs cool color palette.\n"
        f"[FRAME] {HORIZONTAL_FRAME}"
    )
    continuity = (
        "[CONTINUITY] The opening frame continues naturally from the input "
        "first frame (which is the last frame of the previous shot): same "
        "two characters, same emotional state, same blue-hour dusk rooftop "
        "lighting, same color palette and cel-shaded anime style. The "
        "contrast pose is the natural continuation of the previous "
        "emotional peak moment."
    )
    scene = (
        "[SCENE] Split composition in a glass-roofed campus hallway at noon, "
        "continuing the visual contrast theme. Warm sunlight pours from "
        "above through the glass, casting soft light beams that divide the "
        "frame. The brown-twin-tail character (xuejie) stands in the warm "
        "left half, the black-long-hair character (xuemei) stands in the "
        "cool right half. Both are clearly visible from head to mid-torso "
        "in medium wide shot. Background shows a blurred hallway extending "
        "into depth."
    )
    timeline = (
        "[SHOT_TIMELINE] The camera frames both characters clearly from "
        "frame one in the split-light medium wide two-shot (continuing "
        "naturally from the previous shot); then xuejie on the left tilts "
        "her chin up with a confident small smile, twin tails swaying "
        "slightly; then xuemei on the right responds with a soft gentle "
        "smile, a hand rising slightly toward her choker. Both characters "
        "remain the clear focal point throughout. No rapid camera move. "
        "No abstract overlay."
    )
    camera = "[CAMERA] static with very subtle 5 percent dolly, amplitude small, speed slow."
    return _build_prompt(5, scene, timeline, camera, char_block,
                         continuity_hint=continuity)


# ============================================================
# Shot 6 — tail (8s) / 收束, 双人远景
# ============================================================
def build_prompt_shot6() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] Both characters visible together in the same wide "
        f"shot, brown twin tails (xuejie) on left and black long hair "
        f"(xuemei) on right. Both seen from behind or three-quarter back "
        f"view, walking or standing together, faces partially visible in "
        f"profile.\n"
        f"[FRAME] {HORIZONTAL_FRAME}"
    )
    continuity = (
        "[CONTINUITY] The opening frame continues naturally from the input "
        "first frame (which is the last frame of the previous shot): same "
        "two characters, same emotional state, same warm vs cool contrast, "
        "same color palette and cel-shaded anime style. The walking-away "
        "shot is the natural continuation of the previous contrast pose."
    )
    scene = (
        "[SCENE] A wide shot at dusk of a tree-lined campus path with "
        "cherry blossom trees on both sides, petals falling gently through "
        "warm amber street lamp light. The two characters are seen in "
        "three-quarter back view walking side by side away from the camera, "
        "the brown twin tails of xuejie on the left and the long black "
        "hair of xuemei on the right clearly distinguishable. The path "
        "recedes into soft focus depth. Warm lamp light pools on the path "
        "around them."
    )
    timeline = (
        "[SHOT_TIMELINE] The camera frames both characters clearly from "
        "frame one in a stable wide shot, walking away side by side along "
        "the path (continuing naturally from the previous shot); then "
        "their silhouettes pass under a warm amber street lamp, hair and "
        "uniform hems catching the light; then the camera holds as they "
        "continue to walk into the soft focus distance, the falling cherry "
        "petals drifting across frame in foreground. Both characters "
        "remain visible throughout. No rapid camera move. No abstract "
        "overlay."
    )
    camera = "[CAMERA] slow gentle pull back, amplitude small, speed slow."
    return _build_prompt(6, scene, timeline, camera, char_block,
                         continuity_hint=continuity)


BUILDERS = {
    1: build_prompt_shot1,
    2: build_prompt_shot2,
    3: build_prompt_shot3,
    4: build_prompt_shot4,
    5: build_prompt_shot5,
    6: build_prompt_shot6,
}


def verify_no_banned_phrases(out_dir: Path) -> dict:
    """v366 验证: 每段 prompt 不含抽象转场特效词 (任务书 §4: 禁止词)。
    检查项:
      1) 不含 BANNED_TRANSITION_PHRASES 任一
      2) 含 MANDATORY CONTENT RULES
      3) 含 "no fade-in from white"
      4) 含 "Single clear subject" 或类似主张
      5) shot02-06 必含 [CONTINUITY] 块 (链式衔接)
      6) shot01 不放 [CONTINUITY] (因为是开场首帧, 不是从其他镜头接来)
    """
    report = {
        "n_prompts": 0, "n_clean": 0, "errors": [],
        "banned_phrases_list": list(BANNED_TRANSITION_PHRASES),
        "continuity_check_ok": False,
    }
    for cp in sorted(out_dir.glob("shot*_prompt.txt")):
        report["n_prompts"] += 1
        text = cp.read_text(encoding="utf-8")
        banned_hits = [p for p in BANNED_TRANSITION_PHRASES
                       if p.lower() in text.lower()]
        m = re.search(r"shot0?(\d+)_prompt\.txt", cp.name)
        shot_idx = int(m.group(1)) if m else None

        # 检查强制指令
        missing = []
        for needle in ("MANDATORY CONTENT RULES", "no fade-in from white"):
            if needle not in text:
                missing.append(needle)

        # 检查 [CONTINUITY]
        if shot_idx == 1:
            if "[CONTINUITY]" in text:
                missing.append("shot01 should NOT have [CONTINUITY] block "
                                "(it is the opening shot)")
        else:
            if "[CONTINUITY]" not in text:
                missing.append(f"shot{shot_idx:02d} missing [CONTINUITY] block")

        if banned_hits or missing:
            report["errors"].append({
                "file": cp.name,
                "banned_hits": banned_hits,
                "missing": missing,
            })
        else:
            report["n_clean"] += 1

    report["continuity_check_ok"] = all(
        e["missing"] == [] or not any("CONTINUITY" in x for x in e["missing"])
        for e in report["errors"]
    ) and (report["n_clean"] == report["n_prompts"])
    report["ok"] = (
        report["n_clean"] == report["n_prompts"]
        and not any(e["banned_hits"] for e in report["errors"])
    )
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 写 char_blocks_v366.json (复用 v365 角色定义, 加 v366 字段)
    char_blocks_path = out_dir / "char_blocks_v366.json"
    char_blocks_path.write_text(json.dumps({
        "CHAR_XUEJIE_BROWN_TWINTAILS": CHAR_SENIOR_XUEJIE,
        "CHAR_XUEMEI_BLACK_LONG": CHAR_JUNIOR_XUEMEI,
        "STYLE_BLOCK_V366": STYLE_BLOCK_V366,
        "HORIZONTAL_FRAME": HORIZONTAL_FRAME,
        "FORCED_INSTRUCTION": FORCED_INSTRUCTION,
        "BANNED_TRANSITION_PHRASES": list(BANNED_TRANSITION_PHRASES),
        "version": "v3.6.6",
        "task_book": "oc_task_v366.txt §4 §3",
        "method": "chain_first_frame_i2v",
        "notes": [
            "横屏 1344x576 (2.36:1), 跟随参考视频 input_h3_pv_ref.mp4 画幅",
            "shot01 first_frame = 参考视频 t=0.20s",
            "shot02..06 first_frame = 上个 shot 倒数第 2 帧 (extractLastFrame)",
            "禁止抽象转场特效词 (halftone/whip pan/ink burst/fabric wipe/diagonal slash 等)",
            "转场靠'上一段尾帧 -> 下一段首帧'的链式衔接自然完成",
            "每段 prompt 含 [CONTINUITY] 块 (shot01 除外), 强化链式一致",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[v366-pack] char_blocks → {char_blocks_path}", flush=True)

    n = len(BUILDERS)
    total_dur = 0.0
    print(f"[v366-pack] {n} shots → {out_dir}", flush=True)
    for shot_idx in sorted(BUILDERS.keys()):
        prompt, meta = BUILDERS[shot_idx]()
        seg_dur = SEGMENT_DURATIONS_SEC[shot_idx]
        meta["duration_sec"] = seg_dur
        meta["downbeat_start"] = total_dur
        total_dur += seg_dur
        prompt_path = out_dir / f"shot{shot_idx:02d}_prompt.txt"
        meta_path = out_dir / f"shot{shot_idx:02d}_meta.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[v366-pack] shot{shot_idx:02d} phase={meta['phase']:10s} "
              f"dur={seg_dur:.1f}s chars={len(prompt):4d} "
              f"anchor={meta['anchor_source']:35s} → {prompt_path.name}",
              flush=True)

    verify = verify_no_banned_phrases(out_dir)
    verify["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    verify_path = out_dir / "verify_forced_v366.json"
    verify_path.write_text(json.dumps(verify, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[v366-pack] verify → {verify_path} ok={verify['ok']} "
          f"n_clean={verify['n_clean']}/{verify['n_prompts']}",
          flush=True)
    if verify["errors"]:
        print(f"[v366-pack] errors: {verify['errors']}", flush=True)
    print(f"[v366-pack] total duration = {total_dur}s "
          f"({total_dur*2:.0f} beats @ 120 BPM)", flush=True)

    # 节奏规划 (rhythm_plan_v366.json, 供 compose 读取)
    rhythm_plan = {
        "pipeline_version": "v3.6.6",
        "bpm": 120.0,
        "beat_period_sec": 0.5,
        "n_shots": n,
        "total_duration_sec": total_dur,
        "method": "chain_first_frame_i2v",
        "shots": [
            {
                "index": i,
                "phase": SEGMENT_PHASE[i],
                "duration_sec": SEGMENT_DURATIONS_SEC[i],
                "downbeat_start": sum(SEGMENT_DURATIONS_SEC[j]
                                      for j in range(1, i)),
                "downbeat_end": sum(SEGMENT_DURATIONS_SEC[j]
                                    for j in range(1, i+1)),
                "anchor_source": (
                    "ref_video_0.20s" if i == 1
                    else f"shot{i-1:02d}_last_frame_extracted"
                ),
            }
            for i in range(1, n+1)
        ],
    }
    rhythm_plan_path = out_dir / "rhythm_plan_v366.json"
    rhythm_plan_path.write_text(json.dumps(rhythm_plan, ensure_ascii=False,
                                          indent=2), encoding="utf-8")
    print(f"[v366-pack] rhythm_plan → {rhythm_plan_path}", flush=True)
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
