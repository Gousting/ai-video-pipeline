#!/usr/bin/env python3
"""v3.6.5 prompt-pack: 在 v364 基础上缝回生成层转场特效 (per oc_task_v365.txt §2)。

vs prompt_pack_v364.py 关键差异：

- **加回 TRANSITIONS_BLOCK 词库**（复用 v3.2 char_blocks_v32.json 的 7 个词）：
    1. color explosion / ink burst sweeping across frame
    2. fabric wipe where clothing sweeps past camera
    3. diagonal slash wipe
    4. comic panel split-screen
    5. whip pan with motion streaks
    6. hard cut on beat  (硬切, 不算花哨, 仅辅助)
    7. halftone dot overlay flash
- **新增 TRANSITION_ASSIGNMENT**：6 段 → 5 个不同转场特效, 每种全片只用 1 次。
    shot01 (slow_open): 不放花哨特效 (保留 v364 净开场)
    shot02 (build)    : halftone dot overlay flash (学妹登场)
    shot03 (fast)     : whip pan with motion streaks (快切段)
    shot04 (peak)     : color explosion / ink burst (情绪高潮, 最冲击)
    shot05 (fast)     : fabric wipe (布料扫镜过渡到对比)
    shot06 (tail)     : diagonal slash wipe (收束段斜切划像)
    (备用: comic panel split-screen 若想 6 段全放则给 shot01, 但 shot01 不放花哨,
     所以全片只用 5 个不同特效, 不重复, 任务书 §2.2 硬约束)
- 每段 prompt 加 `[TRANSITION_RHYTHM]` 块: 让 H3 在该段首 1-2s 渲染出该段的
  唯一转场特效, 段内只放 1 个, 不堆砌。
- **完全保留 v364 全部强制指令** (§3.1-§3.6 一条不丢)：
    - 竖屏 768x1344
    - 无白淡入 (no fade-in from white/black)
    - 开场直接上角色
    - 快慢呼吸节奏
    - 角色锚点 (学姐棕发双马尾+橙发带+星形挂件 vs 学妹黑长直+choker+深蓝制服)
    - 首帧 YAVG 检查

CLI:
    python prompt_pack_v365.py --out-dir output/pipeline_v36/shots_v365
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "shots_v365"

# ---- 角色定义（任务书 §3.2, 沿用 v364 跨段一致性锚点）----
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

# 竖屏 9:16 构图说明（沿用 v364）
VERTICAL_FRAME = (
    "vertical 9:16 portrait frame (768 wide by 1344 tall), character centered "
    "slightly above middle, generous environment above and below the subject, "
    "no extreme close-up that crops half the face, well-framed composition with "
    "full head and shoulders visible throughout"
)

# 强制指令（任务书 §3.3 / v365 §3, 每段 prompt 末尾显式声明, 沿用 v364 一字不改）
FORCED_INSTRUCTION = (
    "MANDATORY CONTENT RULES: the very first frame must show the character "
    "clearly and directly (no fade-in from white, no fade-in from black, no "
    "abstract neon backdrop, no hand or fabric or accessory close-up opening). "
    "No abstract distortion, no pixelation, no broken geometry, no rotating "
    "background objects, no repeated tile textures. Single clear subject per "
    "frame. Crisp anime cel-shaded rendering with sharp lineart from the very "
    "first frame to the very last frame. The opening shot must establish the "
    "character with face and eyes clearly visible."
)

# 风格基调（任务书 §3.1 / v365 §3.5, 沿用 v364）
STYLE_BLOCK_V365 = (
    "2D-animated, Mai Yoneyama anime cel-shading pop-art style with romantic "
    "soft-light aesthetic. Vibrant high-saturation CMYK pop-art color palette "
    "with translucent color blocks, hand-painted anime lineart, cel-shaded "
    "flat shading with subtle airbrushed gradient on skin only. Layered "
    "composition with distinct foreground character, midground props, and "
    "background atmosphere (cherry blossoms, neon city, or campus). No "
    "photorealism, no 3D render, no CGI. Crisp detail on character features: "
    "hair ornaments, pendants, ribbons, uniform trim."
)

# ---- v3.6.5 新增：生成层转场词库 (复用 v3.2 char_blocks_v32.json §TRANSITIONS_BLOCK) ----
TRANSITIONS_BLOCK = (
    "color explosion / ink burst sweeping across frame, "
    "fabric wipe where clothing sweeps past camera, "
    "diagonal slash wipe, "
    "comic panel split-screen, "
    "whip pan with motion streaks, "
    "hard cut on beat, "
    "halftone dot overlay flash"
)

# ---- v3.6.5 新增：转场特效分配 (6 段 → 5 个不同特效, 每种全片只用 1 次) ----
# 任务书 v365 §2.2 推荐分配, 全片去重
TRANSITION_ASSIGNMENT = {
    1: None,  # shot01 slow_open 不放花哨, 保留 v364 净开场定调
    2: "halftone dot overlay flash",         # shot02 build 学妹登场, 网点闪光切入
    3: "whip pan with motion streaks",        # shot03 fast 快切段甩镜
    4: "color explosion / ink burst sweeping across frame",  # shot04 peak 情绪高潮色彩爆炸
    5: "fabric wipe where clothing sweeps past camera",      # shot05 fast 布料扫镜过渡到对比
    6: "diagonal slash wipe",                 # shot06 tail 收束段斜切划像
}

# 段时长（沿用 v364 拍点网格 120 BPM = 0.5s/拍）
SEGMENT_DURATIONS_SEC = {1: 10.0, 2: 8.0, 3: 6.0, 4: 10.0, 5: 6.0, 6: 8.0}


def make_transition_rhythm_block(shot_idx: int, transition_effect: str | None) -> str:
    """构造该段的 [TRANSITION_RHYTHM] 块 (任务书 §2.3 写法)。

    - 若 transition_effect 为 None (shot01), 返回空字符串 (不放任何转场特效)。
    - 否则: 在段内 ~t=0.7-1.7s (raw H3 时轴) 渲染该唯一转场特效, 不堆砌。

    Why t=0.7-1.7s (not first 1-2s)？
      - 任务书 §3 硬约束: "生成后 trim 掉每段开头白淡入 (v364 已验证 0.5s trim 方案生效)"
      - 若转场在 raw 视频的首 0-0.5s 渲染, 会被 trim 头 0.5s 切掉
      - 把转场放在 raw t≈0.7-1.7s (trim 之后仍在 trimmed 0.2-1.2s),
        既避开 trim 头, 又让 VLM 能看到, 且仍属"段首 1-2s 之内"
        (trimmed 视频的段首 1-2s ≈ raw t=0.5-2.5s, 我们取中段 t=0.7-1.7s).
    """
    if not transition_effect:
        return ""
    return (
        f"[TRANSITION_RHYTHM] This shot contains exactly one in-prompt "
        f"transition effect: the {transition_effect} plays within the first "
        f"1-2 seconds of the shot (specifically at approximately t=0.7-1.7s "
        f"of the raw video, AFTER the brief blank opening fade has resolved), "
        f"rendered as an integrated visual transition (not a cut, not a fade, "
        f"not a post-process overlay). The effect should feel physically "
        f"motivated by the camera or the character's motion (e.g. ink burst "
        f"explodes outward, fabric wipe = clothing or hair sweeps past the "
        f"lens, halftone dots flash on as a graphic pop). Only this one "
        f"transition effect in this shot. No other transition effects in this "
        f"shot. After the first 1-2 seconds the shot settles into the main "
        f"scene without further transition-like artifacts. Do NOT place the "
        f"transition in the very first 0.5 seconds (the brief blank opening "
        f"is trimmed off in post; placing transition there would lose it)."
    )


def soundscape() -> str:
    """沿用 v364 声音床, 避免 H3 单音轨。"""
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
    """沿用 v364 BGM 骨架。"""
    return (
        "non-diegetic music: 120 BPM beat grid, minimal background beat, low "
        "volume, no melody. Audio bed is intentionally sparse during "
        "generation; the final layered BGM track is mixed in post by an "
        "external ffmpeg mixer to keep every beat aligned to the 120 BPM grid "
        "and to avoid the H3 monolithic generated tone."
    )


def _build_prompt(shot_idx: int, transition_effect: str | None,
                  scene: str, timeline: str, camera: str,
                  char_block: str, transition_visual_cue: str = "") -> tuple[str, dict]:
    """通用 prompt 拼装器。

    transition_visual_cue: 在 SCENE 段末尾追加对该转场特效的画面级描述,
                          让 H3 在段首 1-2s 真的渲染出对应效果。
    """
    tr_block = make_transition_rhythm_block(shot_idx, transition_effect)
    body_parts = [STYLE_BLOCK_V365, char_block]
    if transition_visual_cue:
        body_parts.append(transition_visual_cue)
    body_parts.append(scene)
    body_parts.append(timeline)
    if tr_block:
        body_parts.append(tr_block)
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
        "phase": {1: "slow_open", 2: "build", 3: "fast",
                  4: "peak", 5: "fast", 6: "tail"}[shot_idx],
        "duration_sec": dur,
        "downbeat_start": float(downbeat_start),
        "include_senior": False,
        "include_junior": False,
        "characters": (
            ["xuejie_brown_twintails"] if shot_idx == 1
            else ["xuemei_black_long"] if shot_idx == 2
            else ["xuejie_brown_twintails", "xuemei_black_long"]
        ),
        "transition_effect": transition_effect,
        "has_in_prompt_transition": transition_effect is not None,
        "prompt_chars": len(prompt),
        "bpm_target": 120.0,
        "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.5",
        "open_with_character": True,
        "no_fade_in": True,
    }
    return prompt, meta


# ============================================================
# Shot 1 — slow_open / 学姐开场 / 净开场 (不放花哨转场)
# ============================================================
def build_prompt_shot1() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[FRAME] {VERTICAL_FRAME}"
    )
    scene = (
        "[SCENE] A sunlit campus corridor in early spring afternoon. Soft "
        "golden-hour backlight from a large arched window on the left. A few "
        "out-of-focus cherry blossom petals drifting through warm air. The "
        "character stands at the center of the corridor facing the camera in "
        "a three-quarter pose, head slightly tilted, eyes meeting the lens "
        "with a calm confident expression. Cream sailor uniform catches the "
        "warm light, the orange ribbons and star pendant on her twin tails "
        "are clearly visible and in focus."
    )
    timeline = (
        "[SHOT_TIMELINE] Single primary motion chain executed in sequence: "
        "first the camera holds a medium shot with the character centered "
        "and clearly visible from frame one; then she slowly turns her head "
        "about 15 degrees to her left while keeping her gaze toward the "
        "camera, twin tails swaying gently; then a slow subtle smile forms "
        "as cherry petals drift across frame. The character remains the "
        "single clear focal point throughout. No second character. No "
        "abstract overlay. No rapid camera move."
    )
    camera = "[CAMERA] gentle dolly forward, amplitude small, speed slow."
    return _build_prompt(1, TRANSITION_ASSIGNMENT[1],
                         scene, timeline, camera, char_block)


# ============================================================
# Shot 2 — build / 学妹登场 / halftone dot overlay flash
# ============================================================
def build_prompt_shot2() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[FRAME] {VERTICAL_FRAME}"
    )
    transition_cue = (
        "[INCOMING_TRANSITION_CUE] At approximately t=0.7-1.7s of the raw "
        "video (after the brief blank opening has resolved), a vivid comic-"
        "book halftone dot pattern overlay flashes on the frame like a "
        "printed newspaper page coming into focus: thousands of small "
        "circular dots in warm orange and deep magenta tile the entire "
        "frame in a regular grid, covering the character and background, "
        "then dissolving away to reveal the clean shot. The halftone dot "
        "pattern must be clearly visible at the 1-second mark as a graphic "
        "comic-book overlay, and clear by approximately 1.7 seconds in. "
        "Do NOT place the halftone in the first 0.5 seconds (it would be "
        "trimmed off in post)."
    )
    scene = (
        "[SCENE] The same sunlit campus corridor, slightly closer to the "
        "arched window so the warm backlight is more pronounced. A new "
        "character appears at the right side of frame in medium shot, looking "
        "toward the camera with a quiet curious gaze, head slightly tilted. "
        "Her long black hair frames her face, the navy blazer uniform with "
        "white piping is crisp, the black choker and silver ring on her right "
        "index finger catch a glint of warm light. Background shows a few "
        "out-of-focus lockers and a bulletin board."
    )
    timeline = (
        "[SHOT_TIMELINE] [0.7s] HALFTONE DOT OVERLAY FLASH TRANSITION — "
        "a printed newspaper-page comic-book halftone dot pattern fills the "
        "entire frame, thousands of small circular dots in warm orange and "
        "deep magenta arranged in a regular grid; the dot pattern dissolves "
        "away over the next 0.5 seconds. [1.5s] the camera holds medium shot "
        "with xuemei clearly visible from frame one (after the halftone "
        "flash has cleared); she lifts her right hand to chest height and "
        "gently tucks a strand of black hair behind her right ear, the silver "
        "ring catching the warm light; then her gaze softens and she gives "
        "a small slow nod. The character remains the single clear focal "
        "point throughout. No other character in this shot. No further "
        "abstract overlay. No rapid motion."
    )
    camera = "[CAMERA] static with very subtle 5 percent push in, amplitude small, speed slow."
    return _build_prompt(2, TRANSITION_ASSIGNMENT[2],
                         scene, timeline, camera, char_block,
                         transition_visual_cue=transition_cue)


# ============================================================
# Shot 3 — fast / 双人同框 / whip pan with motion streaks
# ============================================================
def build_prompt_shot3() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] The brown-twin-tail character (xuejie) stands on the "
        f"left side of frame, the black-long-hair character (xuemei) on the "
        f"right side, both visible in the same shot. They face each other in "
        f"three-quarter view. Visual distinction is preserved: brown twin "
        f"tails with orange ribbons vs long straight black hair with choker.\n"
        f"[FRAME] {VERTICAL_FRAME}"
    )
    transition_cue = (
        "[INCOMING_TRANSITION_CUE] At approximately t=0.7-1.7s of the raw "
        "video (after the brief blank opening has resolved), a fast whip "
        "pan from left to right takes place, leaving horizontal motion "
        "streaks and speed-line blur trailing behind the camera motion. The "
        "stretches are bright white-yellow with a slight blue tint, comic-"
        "book style motion lines layered over the blurred background. The "
        "whip pan settles into a stable medium two-shot by approximately "
        "1.7 seconds in. Do NOT place the whip pan in the first 0.5 "
        "seconds (it would be trimmed off in post)."
    )
    scene = (
        "[SCENE] An outdoor campus bench area at golden hour. Two characters "
        "stand facing each other, both clearly visible from head to mid-torso. "
        "Warm sunlight from the upper left rims both characters' hair and "
        "shoulders. Background shows a soft-focus row of cherry blossom trees "
        "with petals drifting between them."
    )
    timeline = (
        "[SHOT_TIMELINE] [0.7s] WHIP PAN WITH MOTION STREAKS TRANSITION — "
        "fast camera whip pan from left to right with strong horizontal "
        "motion blur streaks and bright white-yellow comic-book speed lines "
        "trailing across the frame. The whole frame is motion-blurred with "
        "diagonal streaks during the whip. The whip pan settles into a "
        "stable medium two-shot over the next 0.5 seconds. [1.5s] the camera "
        "frames both characters clearly from frame one in a stable medium "
        "two-shot, brown twin tails on left and black long hair on right, "
        "both with eyes visible; then xuejie on the left reaches her right "
        "hand out toward xuemei as if to offer a small object (a single "
        "cherry petal resting on her palm, clearly shown); then xuemei on "
        "the right lifts her gaze from the petal to xuejie's eyes, a small "
        "surprised smile forming. Both characters remain in frame throughout. "
        "No further whip pan after the opening. No abstract overlay. No "
        "third character."
    )
    camera = "[CAMERA] static medium two-shot after opening whip pan, amplitude none, speed slow."
    return _build_prompt(3, TRANSITION_ASSIGNMENT[3],
                         scene, timeline, camera, char_block,
                         transition_visual_cue=transition_cue)


# ============================================================
# Shot 4 — peak / 情绪锚 / color explosion / ink burst
# ============================================================
def build_prompt_shot4() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] Both characters clearly visible in the same frame. "
        f"xuejie (brown twin tails, orange ribbons) on left, xuemei (black "
        f"long hair, navy blazer, choker) on right. They stand close enough "
        f"to suggest emotional closeness, both eyes clearly visible.\n"
        f"[FRAME] {VERTICAL_FRAME}"
    )
    transition_cue = (
        "[INCOMING_TRANSITION_CUE] At approximately t=0.7-1.7s of the raw "
        "video (after the brief blank opening has resolved), a vibrant color "
        "explosion / ink burst sweeps across the frame. Saturated CMYK ink "
        "(electric magenta, laser cyan, fluorescent green, lemon yellow) "
        "bursts outward from one side of the frame and flows across the "
        "screen like a splashed ink wash, momentarily overlaying the "
        "characters and the rooftop skyline before clearing to reveal the "
        "clean shot. The ink burst must be clearly visible around the 1-"
        "second mark and dissolve away by approximately 1.7 seconds in. The "
        "ink feels physical, like spilled paint, not a flat color wipe. Do "
        "NOT place the ink burst in the first 0.5 seconds (it would be "
        "trimmed off in post)."
    )
    scene = (
        "[SCENE] A dramatic evening rooftop at blue-hour dusk. The city "
        "skyline is visible in soft focus behind them with a few warm "
        "window lights beginning to glow. The two characters stand side by "
        "side, both clearly visible from head to mid-torso, the brown "
        "twin-tail character on the left and the black-long-hair character "
        "on the right. A gentle breeze lifts their hair and uniform hems. "
        "The sky is a layered gradient from deep indigo at top to soft "
        "rose at the horizon."
    )
    timeline = (
        "[SHOT_TIMELINE] [0.7s] COLOR EXPLOSION / INK BURST TRANSITION — "
        "saturated CMYK ink (electric magenta, laser cyan, fluorescent "
        "green, lemon yellow) bursts outward from one side of the frame and "
        "flows across the screen like a splashed ink wash, momentarily "
        "overlaying the characters and the rooftop skyline. The ink feels "
        "physical, like spilled paint, and dissolves away over the next 0.5 "
        "seconds. [1.5s] the camera frames both characters clearly from "
        "frame one in a stable medium two-shot (after the opening color "
        "explosion has cleared), both faces and uniforms in focus; then "
        "xuemei on the right slowly turns her head toward xuejie on the "
        "left, her gaze drifting up to meet xuejie's eyes; then xuejie on "
        "the left returns the gaze with a soft warm smile, the orange "
        "ribbons and star pendant clearly catching a glint of dusk light. "
        "Both characters remain the clear focal point throughout. No rapid "
        "camera move. No abstract overlay after the opening ink burst. No "
        "third character."
    )
    camera = "[CAMERA] slow cinematic push in, amplitude small, speed very slow."
    return _build_prompt(4, TRANSITION_ASSIGNMENT[4],
                         scene, timeline, camera, char_block,
                         transition_visual_cue=transition_cue)


# ============================================================
# Shot 5 — fast / 双人对比 / fabric wipe
# ============================================================
def build_prompt_shot5() -> tuple[str, dict]:
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] xuejie (brown twin tails, cream sailor uniform, "
        f"orange ribbons) on left of frame; xuemei (black long hair, navy "
        f"blazer, choker) on right of frame. Both clearly visible from "
        f"head to mid-torso, contrasting warm vs cool color palette.\n"
        f"[FRAME] {VERTICAL_FRAME}"
    )
    transition_cue = (
        "[INCOMING_TRANSITION_CUE] At approximately t=0.7-1.7s of the raw "
        "video (after the brief blank opening has resolved), a physical "
        "fabric wipe plays: a large piece of cream-colored sailor cardigan "
        "uniform fabric (the actual cloth, soft out-of-focus) sweeps across "
        "the camera lens from the left edge to the right edge, briefly "
        "filling the entire frame with soft blurry cream-colored cloth "
        "texture, then continuing past to reveal the clean shot underneath. "
        "The fabric is a real-looking piece of cream cloth, soft and "
        "out-of-focus, not a flat color block. Do NOT place the fabric wipe "
        "in the first 0.5 seconds (it would be trimmed off in post)."
    )
    scene = (
        "[SCENE] Split composition in a glass-roofed campus hallway at noon. "
        "Warm sunlight pours from above through the glass, casting soft "
        "light beams that divide the frame. The brown-twin-tail character "
        "(xuejie) stands in the warm left half, the black-long-hair "
        "character (xuemei) stands in the cool right half. Both are clearly "
        "visible from head to mid-torso in medium shot. Background shows a "
        "blurred hallway extending into depth."
    )
    timeline = (
        "[SHOT_TIMELINE] [0.7s] FABRIC WIPE TRANSITION — a large piece of "
        "physical cream-colored sailor cardigan uniform cloth (real cloth, "
        "soft out-of-focus) sweeps across the camera lens from the left "
        "edge to the right edge, briefly filling the entire frame with "
        "soft blurry cream-colored cloth texture, then continuing past to "
        "reveal the clean shot underneath. The fabric wipe completes over "
        "the next 0.5 seconds. [1.5s] the camera frames both characters "
        "clearly from frame one in the split-light medium two-shot (after "
        "the fabric wipe has passed); then xuejie on the left tilts her "
        "chin up with a confident small smile, twin tails swaying slightly; "
        "then xuemei on the right responds with a soft gentle smile, a hand "
        "rising slightly toward her choker. Both characters remain the "
        "clear focal point throughout. No rapid camera move. No abstract "
        "overlay after the opening fabric wipe. No third character."
    )
    camera = "[CAMERA] static with very subtle 5 percent dolly, amplitude small, speed slow."
    return _build_prompt(5, TRANSITION_ASSIGNMENT[5],
                         scene, timeline, camera, char_block,
                         transition_visual_cue=transition_cue)


# ============================================================
# Shot 6 — tail / 收束 / diagonal slash wipe
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
        f"[FRAME] {VERTICAL_FRAME}"
    )
    transition_cue = (
        "[INCOMING_TRANSITION_CUE] At approximately t=0.7-1.7s of the raw "
        "video (after the brief blank opening has resolved), a diagonal "
        "slash wipe crosses the frame from the upper-left corner to the "
        "lower-right corner. A bright lemon-yellow diagonal band sweeps "
        "across the frame like a sword slash, momentarily dividing the image, "
        "then clears to reveal the clean wide shot of the cherry blossom "
        "path. The slash must be clearly visible around the 1-second mark "
        "and complete its sweep by approximately 1.7 seconds in. Do NOT "
        "place the diagonal slash in the first 0.5 seconds (it would be "
        "trimmed off in post)."
    )
    scene = (
        "[SCENE] A wide shot at dusk of a tree-lined campus path with "
        "cherry blossom trees on both sides, petals falling gently through "
        "warm amber street lamp light. The two characters are seen in "
        "three-quarter back view walking side by side away from the "
        "camera, the brown twin tails of xuejie on the left and the long "
        "black hair of xuemei on the right clearly distinguishable. The "
        "path recedes into soft focus depth. Warm lamp light pools on the "
        "path around them."
    )
    timeline = (
        "[SHOT_TIMELINE] [0.7s] DIAGONAL SLASH WIPE TRANSITION — a bright "
        "lemon-yellow diagonal band sweeps across the frame from the upper-"
        "left corner to the lower-right corner like a sword slash, "
        "momentarily dividing the image, then clears to reveal the clean "
        "wide shot. The slash completes its sweep over the next 0.5 "
        "seconds. [1.5s] the camera frames both characters clearly from "
        "frame one in a stable wide shot, walking away side by side along "
        "the path (after the diagonal slash wipe has cleared); then their "
        "silhouettes pass under a warm amber street lamp, hair and uniform "
        "hems catching the light; then the camera holds as they continue "
        "to walk into the soft focus distance, the falling cherry petals "
        "drifting across frame in foreground. Both characters remain "
        "visible throughout. No rapid camera move. No abstract overlay "
        "after the opening diagonal slash. No third character."
    )
    camera = "[CAMERA] slow gentle pull back, amplitude small, speed slow."
    return _build_prompt(6, TRANSITION_ASSIGNMENT[6],
                         scene, timeline, camera, char_block,
                         transition_visual_cue=transition_cue)


BUILDERS = {
    1: build_prompt_shot1,
    2: build_prompt_shot2,
    3: build_prompt_shot3,
    4: build_prompt_shot4,
    5: build_prompt_shot5,
    6: build_prompt_shot6,
}


def verify_forced_instructions(out_dir: Path) -> dict:
    """验证每段 prompt 都满足 v365 §3 强制指令 + 转场去重。

    检查项：
      1) 必含禁止词声明（"no fade-in from white" 等），证明 prompt 在主动避开问题。
      2) 必含 MANDATORY CONTENT RULES 段。
      3) 必含 "Single clear subject" 主张。
      4) 不能有「开场即 fade in from white」的指令性写法。
      5) shot02-06 必含 [TRANSITION_RHYTHM] 段（v365 新增）。
      6) shot02-06 必含 [INCOMING_TRANSITION_CUE] 段（v365 新增）。
      7) TRANSITION_ASSIGNMENT 全片去重（v365 §2.2 硬约束）。
    """
    BANNED_INSTRUCTION_RE = re.compile(
        r"(?i)(?<!no )(?<!never )(?<!do not )(?<!don't )"
        r"(fade[- ]?in from white|fade[- ]?in from black|fade from white|fade from black|"
        r"opening with hand close|opening on hand|opening on fabric|opening on accessory)"
    )
    REQUIRED_PHRASES_V364 = (
        "MANDATORY CONTENT RULES",
        "no fade-in from white",
        "no fade-in from black",
        "Single clear subject",
    )
    REQUIRED_PHRASES_V365 = (
        "[TRANSITION_RHYTHM]",
        "[INCOMING_TRANSITION_CUE]",
    )
    report: dict = {
        "n_prompts": 0, "n_clean": 0, "errors": [],
        "transition_assignment": {str(k): v for k, v in TRANSITION_ASSIGNMENT.items()},
        "transitions_used_count": 0,
        "transitions_unique_ok": False,
    }
    seen_effects: list[str] = []
    for cp in sorted(out_dir.glob("shot*_prompt.txt")):
        report["n_prompts"] += 1
        text = cp.read_text(encoding="utf-8")
        banned_hits = [m.group(0) for m in BANNED_INSTRUCTION_RE.finditer(text)]
        missing_v364 = [p for p in REQUIRED_PHRASES_V364 if p not in text]
        # 抽 shot idx
        m = re.search(r"shot0?(\d+)_prompt\.txt", cp.name)
        shot_idx = int(m.group(1)) if m else None
        missing_v365: list[str] = []
        if shot_idx in TRANSITION_ASSIGNMENT and TRANSITION_ASSIGNMENT[shot_idx]:
            for p in REQUIRED_PHRASES_V365:
                if p not in text:
                    missing_v365.append(p)
        # 检查 TRANSITION_RHYTHM 的 effect 是否匹配分配
        tr_m = re.search(
            r"\[TRANSITION_RHYTHM\][^:]*:\s*the\s+(.+?)\s+plays\s+within",
            text,
        )
        effect_in_prompt = tr_m.group(1).strip() if tr_m else None
        effect_match_ok = True
        if shot_idx in TRANSITION_ASSIGNMENT:
            expected = TRANSITION_ASSIGNMENT[shot_idx]
            if expected is None:
                if "[TRANSITION_RHYTHM]" in text:
                    missing_v365.append("shot01 should NOT have TRANSITION_RHYTHM block")
            else:
                if effect_in_prompt != expected:
                    effect_match_ok = False
                if expected and expected not in seen_effects:
                    seen_effects.append(expected)

        if banned_hits or missing_v364 or missing_v365 or not effect_match_ok:
            report["errors"].append({
                "file": cp.name,
                "banned_hits": banned_hits,
                "missing_required_v364": missing_v364,
                "missing_required_v365": missing_v365,
                "effect_in_prompt": effect_in_prompt,
                "expected_effect": TRANSITION_ASSIGNMENT.get(shot_idx),
                "effect_match_ok": effect_match_ok,
            })
        else:
            report["n_clean"] += 1
    report["transitions_used_count"] = len(seen_effects)
    # 全片去重: 5 个不同特效 (shot01 不放)
    report["transitions_unique_ok"] = (
        len(seen_effects) == 5
        and len(set(seen_effects)) == 5
    )
    report["ok"] = (
        report["n_clean"] == report["n_prompts"]
        and report["transitions_unique_ok"]
    )
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    char_blocks_path = out_dir / "char_blocks_v365.json"
    char_blocks_path.write_text(json.dumps({
        "CHAR_XUEJIE_BROWN_TWINTAILS": CHAR_SENIOR_XUEJIE,
        "CHAR_XUEMEI_BLACK_LONG": CHAR_JUNIOR_XUEMEI,
        "STYLE_BLOCK_V365": STYLE_BLOCK_V365,
        "VERTICAL_FRAME": VERTICAL_FRAME,
        "FORCED_INSTRUCTION": FORCED_INSTRUCTION,
        "TRANSITIONS_BLOCK": TRANSITIONS_BLOCK,
        "TRANSITION_ASSIGNMENT": {str(k): v for k, v in TRANSITION_ASSIGNMENT.items()},
        "version": "v3.6.5",
        "task_book": "oc_task_v365.txt §2 §3",
        "notes": [
            "TRANSITIONS_BLOCK: 7 in-prompt transition effects vocabulary (复用 v3.2 char_blocks_v32.json).",
            "TRANSITION_ASSIGNMENT: 6 shots -> 5 unique transitions (shot01 净开场, 不放花哨转场).",
            "每个转场特效全片只出现 1 次, 多段不重复 (硬约束).",
            "shot02-06 prompt 加 [TRANSITION_RHYTHM] + [INCOMING_TRANSITION_CUE] 块让 H3 在段首 1-2s 渲染出对应效果.",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[v365-pack] char_blocks → {char_blocks_path}", flush=True)

    n = len(BUILDERS)
    total_dur = 0.0
    print(f"[v365-pack] {n} shots → {out_dir}", flush=True)
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
        effect = TRANSITION_ASSIGNMENT[shot_idx]
        effect_str = effect if effect else "(none)"
        print(f"[v365-pack] shot{shot_idx:02d} phase={meta['phase']:10s} "
              f"dur={seg_dur:.1f}s transition={effect_str:55s} "
              f"prompt_chars={len(prompt)} → {prompt_path.name}", flush=True)

    verify = verify_forced_instructions(out_dir)
    verify["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    verify_path = out_dir / "verify_forced_v365.json"
    verify_path.write_text(json.dumps(verify, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[v365-pack] verify → {verify_path} ok={verify['ok']} "
          f"n_clean={verify['n_clean']}/{verify['n_prompts']} "
          f"transitions_unique={verify['transitions_unique_ok']} "
          f"({verify['transitions_used_count']} unique effects)",
          flush=True)
    print(f"[v365-pack] total duration = {total_dur}s ({total_dur*2:.0f} beats @ 120 BPM)",
          flush=True)

    phase_map = {1: "slow_open", 2: "build", 3: "fast",
                 4: "peak", 5: "fast", 6: "tail"}
    rhythm_plan = {
        "pipeline_version": "v3.6.5",
        "bpm": 120.0,
        "beat_period_sec": 0.5,
        "n_shots": n,
        "total_duration_sec": total_dur,
        "shots": [
            {
                "index": i,
                "phase": phase_map[i],
                "duration_sec": SEGMENT_DURATIONS_SEC[i],
                "downbeat_start": sum(SEGMENT_DURATIONS_SEC[j] for j in range(1, i)),
                "downbeat_end": sum(SEGMENT_DURATIONS_SEC[j] for j in range(1, i+1)),
                "transition_effect": TRANSITION_ASSIGNMENT[i],
            }
            for i in range(1, n+1)
        ],
        "transition_assignment": {str(k): v for k, v in TRANSITION_ASSIGNMENT.items()},
    }
    rhythm_plan_path = out_dir / "rhythm_plan_v365.json"
    rhythm_plan_path.write_text(json.dumps(rhythm_plan, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    print(f"[v365-pack] rhythm_plan → {rhythm_plan_path}", flush=True)
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
