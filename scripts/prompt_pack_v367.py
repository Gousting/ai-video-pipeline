#!/usr/bin/env python3
"""v3.6.7 prompt-pack: 6 段参考直出 Reference-to-Video 提示词包。

任务书 oc_task_v367.txt §3:
- 基于视觉风格档案 v367_style_profile.md (Step 0 VLM 产出) 严格生成
- 6 段节奏: slow_open(8s) / build(6s) / fast(5s) / peak(8s) / fast(5s) / tail(8s)
- 总计 40s, 与 v366 节奏一致
- **关键**: 每段 prompt 必须引用档案中的 character/style/color/lighting block
- 不另起炉灶, 一切围绕参考视频的"Color Riot Girl"单角色 fashion editorial MV
- 用 MiniMaxH3ReferenceToVideo (ref_images 贯穿每个采样步), 不是首帧续写

vs prompt_pack_v366.py 关键差异:
- 单一角色 "Color Riot Girl" (不是 v366 虚构的"学姐+学妹"双角色)
- 完全使用 VLM 输出的 4 个 prompt block (character/style/color/lighting)
- 场景/动作/构图跟随参考视频 (CMYK 平面色场 + 特写 + 横分屏 + 标题卡)
- 强制指令: 禁止 v366 那套 v365-failure 抽象转场词; 禁止自然外景/水彩/3D 等档案 banned 项
- 每段都标注 reference_to_video + ref_images manifest

CLI:
    python prompt_pack_v367.py
    python prompt_pack_v367.py --out-dir output/pipeline_v36/shots_v367
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
DEFAULT_PROFILE = ROOT / "ref_analysis_v367" / "v367_style_profile.md"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "shots_v367"

# 任务书 §3 段时长 (沿用 v366 节奏, 6 段 40s)
SEGMENT_DURATIONS_SEC = {1: 8.0, 2: 6.0, 3: 5.0, 4: 8.0, 5: 5.0, 6: 8.0}
SEGMENT_PHASE = {1: "slow_open", 2: "build", 3: "fast",
                 4: "peak", 5: "fast", 6: "tail"}

# ============================================================
# VLM 档案 4 个 prompt block (来自 v367_style_profile.md §7)
# 直接引用, 不重写. 后续所有 shot 都共享这 4 个 block, 保证一致.
# ============================================================
CHAR_BLOCK = (
    "A young anime woman in her early twenties with long straight black "
    "hair, blunt bangs and flowing strands showing a teal/cyan underlayer "
    "and rainbow prismatic streaks. Skin is pale and fair with a soft "
    "peach blush. Eyes are large and prismatic — shifting between full "
    "rainbow spectrum and emerald-green-yellow — framed by sharp black "
    "lashes and a tiny rainbow tear mark beneath. She wears an acid-lime "
    "green cropped jacket and matching shorts with a visible zipper, a "
    "black thigh strap with a cyan stripe, and rainbow multi-pierced ear "
    "cuffs including a geometric triangle charm. Her long coffin/stiletto "
    "nails alternate cobalt-blue and hot-magenta polish. Expression is "
    "cool, detached, fashion-editorial with a subtle smirk. Body is "
    "slender with stylized anime proportions and long legs."
)

STYLE_BLOCK = (
    "Anime cel-shading with hard 2-tone shading, glossy wet specular "
    "highlights on skin and nails, sharp black linework, mixed with flat "
    "2D graphic color-field plates and painterly macro close-ups. Risograph "
    "halftone dots, CMYK registration mis-print feel, neon rave aesthetic, "
    "motion-graphics anime direction."
)

COLOR_BLOCK = (
    "Hyper-saturated CMYK palette: vivid red, cobalt blue, canary yellow "
    "as primaries, with electric cyan, hot magenta and acid lime green as "
    "accents. No muted or desaturated tones anywhere."
)

LIGHTING_BLOCK = (
    "Stylized artificial studio lighting with a strong frontal key, hard "
    "rim light producing iridescent rainbow edges on hair and skin, glossy "
    "wet highlights on eyes and nails. Studio void or flat color-field "
    "backgrounds. No naturalistic shadows."
)

# 横屏构图 (任务书 §1: 1344x576 跟随参考视频)
HORIZONTAL_FRAME = (
    "horizontal 2.36:1 cinematic wide frame (1344 wide by 576 tall), "
    "characters framed at center with generous color-field bands on both "
    "sides, deep depth of field showing flat 2D graphic plates as "
    "background, well-framed composition maintaining extreme macro to "
    "wide shot variety throughout, no cropping of face or character "
    "silhouette"
)

# 强制指令 (任务书 §6 硬性约束 + VLM §7.5 banned_in_prompt)
FORCED_INSTRUCTION = (
    "MANDATORY CONTENT RULES (v3.6.7 Reference-to-Video): "
    "Maintain the exact visual style, character identity, color palette, "
    "and lighting of the reference images (ref_images) throughout every "
    "frame. The opening frame must continue naturally from the reference "
    "identity — no fade-in from white, no fade-in from black, no abstract "
    "neon-only opening, no hand-only or fabric-only opening. "
    "BANNED ELEMENTS (must NOT appear): realistic photography, muted or "
    "pastel palette, 3D Pixar render, soft watercolor shading, natural "
    "outdoor lighting, historical or period costume, low saturation, "
    "photorealistic skin texture, halftone dot overlay flash, whip pan "
    "with motion streaks, fabric wipe, diagonal slash wipe, split-screen "
    "comic panels, [TRANSITION_RHYTHM], [INCOMING_TRANSITION_CUE]. "
    "Single clear subject per frame. Crisp anime cel-shaded rendering with "
    "sharp lineart from the very first frame to the very last frame. "
    "Reference tokens ride through every sampling step (this is "
    "MiniMaxH3ReferenceToVideo, not first-frame continuation)."
)

# v366 banned + v367 banned (VLM §7.5)
BANNED_PHRASES = (
    "halftone dot overlay flash", "halftone",
    "whip pan with motion streaks", "whip pan",
    "color explosion", "ink burst",
    "fabric wipe", "diagonal slash wipe", "diagonal slash",
    "comic panel split-screen", "split-screen",
    "hard cut on beat",
    "[TRANSITION_RHYTHM]", "[INCOMING_TRANSITION_CUE]",
    "realistic photography", "muted palette", "pastel palette",
    "3D Pixar render", "soft watercolor shading",
    "natural outdoor lighting", "photorealistic skin texture",
    "low saturation",
)


def soundscape() -> str:
    """参考视频是 MV, 留 BGM 后期统一铺底, prompt 不放音乐描述避免 H3 误解。"""
    return (
        "Soft ambient audio bed — distant digital chimes, faint heartbeat "
        "pulse, no voices, no spoken dialogue, no narration throughout "
        "the entire video. The audio bed is mixed in post by an external "
        "ffmpeg mixer to keep all beats aligned. NOTE: do not synthesize "
        "whoosh, inter-shot SFX, or any music during generation; the "
        "final layered BGM track is mixed externally."
    )


def music_skeleton() -> str:
    return (
        "non-diegetic music: 120 BPM beat grid, minimal background beat, "
        "low volume, no melody. The audio bed is intentionally sparse "
        "during generation; the final layered BGM is mixed in post by an "
        "external ffmpeg mixer to keep every beat aligned to the 120 BPM "
        "grid and to avoid H3 monolithic generated tone."
    )


def _build_prompt(shot_idx: int, scene: str, timeline: str,
                  camera: str, shot_description: str,
                  continuity_hint: str = "") -> tuple[str, dict]:
    """通用 prompt 拼装器。

    shot_description: 短标题, 用于 meta 和日志。
    continuity_hint: shot02-06 必填, 描述与参考 ref_images 的一致性。
    """
    body_parts = [STYLE_BLOCK, COLOR_BLOCK, LIGHTING_BLOCK,
                  f"[CHARACTER] {CHAR_BLOCK}",
                  f"[FRAME] {HORIZONTAL_FRAME}"]
    if continuity_hint:
        body_parts.append(continuity_hint)
    body_parts.append(f"[SHOT_DESC] {shot_description}")
    body_parts.append(f"[SCENE] {scene}")
    body_parts.append(f"[SHOT_TIMELINE] {timeline}")
    body_parts.append(f"[CAMERA] {camera}")
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
        "characters": ["color_riot_girl"],
        "method": "reference_to_video",
        "ref_images_source": "input_h3_pv_ref.mp4",
        "ref_images_indices": [1, 4, 7, 11],
        "ref_image_size": "max",
        "node_class": "MiniMaxH3ReferenceToVideo",
        "prompt_chars": len(prompt),
        "bpm_target": 120.0,
        "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.7",
        "task_book_section": "oc_task_v367.txt §3",
        "vlm_profile_ref": "ref_analysis_v367/v367_style_profile.md",
        "open_with_character": True,
        "no_fade_in": True,
        "no_abstract_transition_words": True,
        "no_banned_vlm_elements": True,
        "horizontal_resolution": "1344x576",
        "aspect_ratio": "2.36:1",
        "character_block_chars": len(CHAR_BLOCK),
        "style_block_chars": len(STYLE_BLOCK),
        "color_block_chars": len(COLOR_BLOCK),
        "lighting_block_chars": len(LIGHTING_BLOCK),
    }
    return prompt, meta


# ============================================================
# Shot 1 — slow_open (8s) / 标题卡风格开场, 角色正脸
# ============================================================
def build_prompt_shot1() -> tuple[str, dict]:
    """参考视频 t=0.20s 的"COLOR RIOT"标题 + 角色正脸 + 彩虹泪滴。"""
    continuity = (
        "[CONTINUITY_TO_REFS] This shot's character identity, hair, eye "
        "color, outfit, and color palette must remain pixel-faithful to "
        "the 4 reference images (ref_images: t=0.20s / t=8.00s / t=14.03s "
        "/ t=27.27s). The character must look like the same person across "
        "all reference frames: black hair with cyan underlayer and rainbow "
        "streaks, prismatic rainbow eyes with tear mark, acid-lime green "
        "cropped outfit, rainbow multi-pierced ear cuffs, stiletto nails."
    )
    scene = (
        "An abstract neon studio void with bold CMYK graphic color-field "
        "backdrop: a vivid red panel on the left, a cobalt blue panel on "
        "the right, with a canary yellow horizontal band cutting across "
        "the middle. Risograph halftone dots scattered across the color "
        "field. The character stands at dead center of the frame in a "
        "three-quarter power pose, head slightly tilted, looking directly "
        "into the camera with a cool detached fashion-editorial smirk. "
        "Her acid-lime green cropped jacket and matching shorts are crisp, "
        "the black thigh strap with cyan stripe clearly visible, the "
        "rainbow ear cuffs and geometric triangle charm catching the "
        "studio rim light."
    )
    timeline = (
        "Hold a centered medium wide shot from frame one with the character "
        "clearly visible and in focus, no zoom, no dissolve; then the "
        "character slowly tilts her chin down by 10 degrees while keeping "
        "eye contact with the camera, twin strands of hair falling forward; "
        "then she lifts her right hand (with cobalt-blue and hot-magenta "
        "stiletto nails) and frames her face with two fingers, a deliberate "
        "fashion-editorial gesture. The character remains the single clear "
        "focal point throughout. No second character. No abstract overlay "
        "swallowing the face."
    )
    camera = "[CAMERA] static wide with very subtle 3 percent push in, amplitude small, speed very slow."
    return _build_prompt(
        1, scene, timeline, camera,
        "Slow-open title-card intro: Color Riot Girl at center frame, "
        "CMYK color-field backdrop, fashion-editorial smirk.",
        continuity_hint=continuity)


# ============================================================
# Shot 2 — build (6s) / 极特写眼睛, 跟随 shot1 结尾
# ============================================================
def build_prompt_shot2() -> tuple[str, dict]:
    """参考视频 t=1.37s 的极特写眼睛 + 彩虹反光。"""
    continuity = (
        "[CONTINUITY_TO_REFS] Continue the exact character identity, "
        "prismatic rainbow eyes, black hair with cyan underlayer, acid-"
        "lime green outfit, rainbow multi-pierced ear cuff, and CMYK "
        "color palette from the 4 reference images. The close-up must "
        "show facial features that exactly match the reference: rainbow "
        "tear mark under the eye, sharp black lashes, pale fair skin "
        "with peach blush."
    )
    scene = (
        "An extreme macro close-up of the character's left eye filling "
        "the right two-thirds of the horizontal 2.36:1 frame, the left "
        "third showing a vivid cobalt blue graphic color-field panel "
        "with electric cyan paint ribbons floating past. The eye iris is "
        "fully prismatic — a rainbow spectrum of red, yellow, green, blue "
        "spinning inside the pupil — with a glossy wet specular highlight "
        "from the studio key light. Long sharp black lashes frame the "
        "eye, and the tiny rainbow tear mark beneath the lower lash line "
        "is clearly visible. Skin is pale fair with peach blush, no "
        "realistic pores, cel-shaded flat."
    )
    timeline = (
        "Hold the extreme macro close-up from frame one, the eye is "
        "clearly visible and in focus throughout; then the character "
        "slowly blinks once, the prismatic iris shifting hue from "
        "rainbow to emerald-green-yellow as the eyelid closes; then the "
        "eye opens again with the rainbow restored and the gaze drifts "
        "slightly to camera left. The eye remains the single clear focal "
        "point throughout. No second character. No dissolve."
    )
    camera = "[CAMERA] static macro, amplitude none, speed none."
    return _build_prompt(
        2, scene, timeline, camera,
        "Extreme macro eye close-up with prismatic rainbow iris and "
        "fashion-editorial gaze.",
        continuity_hint=continuity)


# ============================================================
# Shot 3 — fast (5s) / 美甲特写, 跟随 shot2 结尾
# ============================================================
def build_prompt_shot3() -> tuple[str, dict]:
    """参考视频 t=15.67s 的美甲特写 + 蓝/品红渐变 + 黄背景。"""
    continuity = (
        "[CONTINUITY_TO_REFS] Maintain the exact character identity: black "
        "hair with cyan underlayer and rainbow streaks, pale fair skin, "
        "acid-lime green outfit. The hand shown in close-up belongs to "
        "the same Color Riot Girl character from the 4 reference images, "
        "her signature cobalt-blue and hot-magenta stiletto nail art "
        "matching reference frame_08 (t=15.67s)."
    )
    scene = (
        "An extreme macro close-up of the character's left hand resting "
        "against a flat canary yellow graphic color-field panel that fills "
        "the lower two-thirds of the horizontal 2.36:1 frame, the upper "
        "third showing a cobalt blue band with electric cyan paint "
        "ribbons. The hand is slender with stylized anime proportions, "
        "pale fair skin with peach blush, long coffin-shaped stiletto "
        "nails alternating cobalt-blue polish on the index and pinky, "
        "hot-magenta polish on the middle and ring, clear gloss on the "
        "thumb. The hand is positioned at center frame, palm up, fingers "
        "slightly spread. Risograph halftone dots scattered in the "
        "background."
    )
    timeline = (
        "Hold the macro close-up from frame one, the hand is clearly "
        "visible and in focus throughout; then the character slowly curls "
        "the index and middle fingers inward by 20 degrees, the cobalt-"
        "blue and hot-magenta nail polish catching the studio key light "
        "with glossy wet highlights; then she extends them again to a "
        "relaxed pose. The hand remains the single clear focal point "
        "throughout. No second character. No dissolve."
    )
    camera = "[CAMERA] static macro, amplitude none, speed none."
    return _build_prompt(
        3, scene, timeline, camera,
        "Macro hand close-up with alternating cobalt-blue and hot-"
        "magenta stiletto nails on canary yellow backdrop.",
        continuity_hint=continuity)


# ============================================================
# Shot 4 — peak (8s) / 全身动态 fashion editorial pose
# ============================================================
def build_prompt_shot4() -> tuple[str, dict]:
    """参考视频 t=14.03s 的全身 + 绿衣 + 抽象色带。"""
    continuity = (
        "[CONTINUITY_TO_REFS] Continue the exact character identity, "
        "outfit (acid-lime green cropped jacket and matching shorts with "
        "zipper, black thigh strap with cyan stripe), rainbow multi-"
        "pierced ear cuffs, black hair with cyan underlayer and rainbow "
        "streaks, prismatic rainbow eyes, CMYK color palette — all "
        "matching the 4 reference images pixel-faithful."
    )
    scene = (
        "A bold full-body fashion editorial shot of the character standing "
        "at center frame against a layered CMYK graphic color-field "
        "backdrop: cobalt blue panel on the upper half, electric cyan "
        "paint ribbon swooping diagonally from upper-right to lower-left, "
        "a vivid red horizontal band cutting across the middle, canary "
        "yellow at the bottom edge. Risograph halftone dots scattered "
        "across the color field. The character stands in a confident "
        "slight contrapposto pose, weight on the left leg, right hand "
        "resting on the hip, left arm relaxed at her side. Acid-lime "
        "green cropped jacket with visible zipper, matching shorts, "
        "black thigh strap with cyan stripe clearly visible, rainbow "
        "multi-pierced ear cuffs with geometric triangle charm catching "
        "the rim light, stiletto nails alternating cobalt-blue and hot-"
        "magenta visible on the resting hand."
    )
    timeline = (
        "Hold the centered full-body shot from frame one, the character "
        "is clearly visible head to toe in focus throughout; then she "
        "shifts her weight and tilts her chin up by 5 degrees with a "
        "subtle smirk, twin strands of hair falling forward over her "
        "face; then she lifts her right hand from her hip and gestures "
        "an open palm toward the camera, the cobalt-blue and hot-magenta "
        "stiletto nails catching the key light. The character remains "
        "the single clear focal point throughout. No second character. "
        "No dissolve. No rapid camera move."
    )
    camera = "[CAMERA] static full-body with very subtle 4 percent push in, amplitude small, speed very slow."
    return _build_prompt(
        4, scene, timeline, camera,
        "Full-body fashion editorial power pose with layered CMYK color-"
        "field backdrop and cobalt-blue paint ribbon.",
        continuity_hint=continuity)


# ============================================================
# Shot 5 — fast (5s) / 横分屏, 跟随参考视频 t=8.0s 的分屏风格
# ============================================================
def build_prompt_shot5() -> tuple[str, dict]:
    """参考视频 t=8.0s 的双面板角色分屏 (vivid red + cobalt blue 双色)。"""
    continuity = (
        "[CONTINUITY_TO_REFS] Continue the exact character identity from "
        "the 4 reference images: black hair with cyan underlayer and "
        "rainbow streaks, prismatic rainbow eyes with rainbow tear mark, "
        "acid-lime green outfit, rainbow multi-pierced ear cuffs, "
        "stiletto nails alternating cobalt-blue and hot-magenta, pale "
        "fair skin with peach blush. Both halves of the split-screen "
        "show the SAME character, not two different people."
    )
    scene = (
        "A horizontal split-screen composition: the upper half shows a "
        "vivid red graphic color-field panel with the character's face "
        "from the shoulders up, head turned three-quarter to camera left, "
        "prismatic rainbow eyes looking toward camera, rainbow tear mark "
        "visible, rainbow multi-pierced ear cuff clearly catching the "
        "rim light; the lower half shows a cobalt blue graphic color-field "
        "panel with the SAME character's face from the shoulders up in a "
        "different angle, head turned three-quarter to camera right, the "
        "acid-lime green jacket collar visible, the geometric triangle "
        "earring prominent. Risograph halftone dots scattered in both "
        "halves. A thin canary yellow horizontal band cuts between the "
        "two halves."
    )
    timeline = (
        "Hold the split-screen composition from frame one, both halves "
        "of the same character are clearly visible and in focus throughout; "
        "then the upper-half character slowly blinks once, the prismatic "
        "iris shifting from rainbow to emerald-green-yellow as the "
        "eyelid closes; then both halves return to the original pose, "
        "eyes locked with the camera. The split-screen with two copies "
        "of the SAME character remains the focal point throughout. No "
        "second distinct character. No dissolve."
    )
    camera = "[CAMERA] static split-screen, amplitude none, speed none."
    return _build_prompt(
        5, scene, timeline, camera,
        "Horizontal split-screen dual panels of the same Color Riot "
        "Girl character against vivid red and cobalt blue backdrops.",
        continuity_hint=continuity)


# ============================================================
# Shot 6 — tail (8s) / 收束, 角色最终姿势 + 标题卡风格
# ============================================================
def build_prompt_shot6() -> tuple[str, dict]:
    """参考视频 t=30.50s 的下半身 + 绿短裙 + 黑腿带 + 蓝美甲 + 蓝粉黄背景。"""
    continuity = (
        "[CONTINUITY_TO_REFS] Continue the exact character identity, "
        "outfit (acid-lime green cropped jacket and matching shorts, "
        "black thigh strap with cyan stripe), black hair with cyan "
        "underlayer and rainbow streaks, stiletto nails alternating "
        "cobalt-blue and hot-magenta, CMYK hyper-saturated palette — "
        "all matching the 4 reference images pixel-faithful."
    )
    scene = (
        "A bold mid-shot from the waist down of the character standing "
        "at center frame against a hyper-saturated CMYK color-field "
        "backdrop: cobalt blue panel on the left, electric cyan paint "
        "ribbons in the middle, hot magenta panel on the right, canary "
        "yellow accent at the top edge. The character's acid-lime green "
        "shorts with visible zipper are crisp, the black thigh strap "
        "with cyan stripe is clearly visible, the cobalt-blue and hot-"
        "magenta stiletto nails are visible on her resting right hand. "
        "Risograph halftone dots scattered in the background."
    )
    timeline = (
        "Hold the waist-down mid-shot from frame one, the character's "
        "outfit and stiletto nails are clearly visible and in focus "
        "throughout; then the character slowly shifts her weight from "
        "left leg to right leg, the black thigh strap sliding slightly "
        "as she moves; then she lifts her right hand (cobalt-blue and "
        "hot-magenta stiletto nails catching the studio key light) and "
        "gestures a small wave toward the camera, a final fashion-"
        "editorial flourish. The character remains the single clear "
        "focal point throughout. No second character. No dissolve."
    )
    camera = "[CAMERA] static mid-shot with very subtle 3 percent pull back, amplitude small, speed very slow."
    return _build_prompt(
        6, scene, timeline, camera,
        "Mid-shot waist-down outro with acid-lime green shorts, black "
        "thigh strap, and CMYK color-field backdrop.",
        continuity_hint=continuity)


BUILDERS = {
    1: build_prompt_shot1,
    2: build_prompt_shot2,
    3: build_prompt_shot3,
    4: build_prompt_shot4,
    5: build_prompt_shot5,
    6: build_prompt_shot6,
}


def _visual_sections(text: str) -> str:
    """从完整 prompt 抽出视觉描述段 (排除 FORCED_INSTRUCTION / soundscape / music
    这几块, 因为 banned 列表就写在 FORCED_INSTRUCTION 里).
    """
    out = []
    cur_label = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur_label = s
            continue
        if s.startswith("integrated_multimodal_description:") \
                or s.startswith("overall_soundscape:") \
                or s.startswith("non_diegetic_music:") \
                or s.startswith("MANDATORY CONTENT RULES"):
            cur_label = None  # exclude
            continue
        if cur_label:
            out.append(line)
    return "\n".join(out)


def verify_no_banned_phrases(out_dir: Path) -> dict:
    """v367 验证: 视觉描述段不含 v366 banned + v367 VLM banned。"""
    report = {
        "n_prompts": 0, "n_clean": 0, "errors": [],
        "banned_phrases_list": list(BANNED_PHRASES),
        "vlm_profile_ref_check_ok": False,
    }
    for cp in sorted(out_dir.glob("shot*_prompt.txt")):
        report["n_prompts"] += 1
        text = cp.read_text(encoding="utf-8")
        visual_text = _visual_sections(text)
        visual_l = visual_text.lower()
        banned_hits = [p for p in BANNED_PHRASES
                       if p.lower() in visual_l]
        m = re.search(r"shot0?(\d+)_prompt\.txt", cp.name)
        shot_idx = int(m.group(1)) if m else None

        missing = []
        for needle in (
            "MANDATORY CONTENT RULES (v3.6.7 Reference-to-Video)",
            "no fade-in from white",
            "[CHARACTER]",
            "[FRAME]",
        ):
            if needle not in text:
                missing.append(needle)
        if "2.36:1" not in text:
            missing.append("2.36:1")

        # 强制: 4 个 VLM block 都必须出现 (在视觉描述段中)
        for block_label in (
            "long straight black hair",
            "Anime cel-shading",
            "Hyper-saturated CMYK palette",
            "Stylized artificial studio lighting",
        ):
            if block_label not in text:
                missing.append(f"VLM block missing: {block_label}")

        # v367 r2v: 所有 6 段都必须有 [CONTINUITY_TO_REFS]
        # (与参考 ref_images 一致性是 r2v 核心, 不存在"无前段可接"的 shot01 例外)
        if shot_idx is not None and "[CONTINUITY_TO_REFS]" not in text:
            missing.append(
                f"shot{shot_idx:02d} missing [CONTINUITY_TO_REFS] "
                "(r2v style requires explicit reference consistency)")

        if banned_hits or missing:
            report["errors"].append({
                "file": cp.name,
                "banned_hits": banned_hits,
                "missing": missing,
            })
        else:
            report["n_clean"] += 1

    report["vlm_profile_ref_check_ok"] = all(
        not any("VLM block missing" in x for x in e["missing"])
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
    ap.add_argument("--profile-md", default=str(DEFAULT_PROFILE),
                    help="VLM 视觉档案 (用于 sanity check 存在)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # sanity: VLM 档案必须存在 (任务书 §6 硬约束: 先看档案再写 prompt)
    profile_path = Path(args.profile_md)
    if not profile_path.exists():
        print(f"ERROR: VLM profile not found: {profile_path}", file=sys.stderr)
        print("请先运行: python scripts/vlm_analyze_ref_v367.py",
              file=sys.stderr)
        return 2
    profile_size = profile_path.stat().st_size
    if profile_size < 1000:
        print(f"ERROR: VLM profile too small ({profile_size} bytes), "
              f"可能 VLM 调用失败: {profile_path}", file=sys.stderr)
        return 2
    print(f"[v367-pack] VLM profile OK: {profile_path} ({profile_size} B)")

    # 写 char_blocks_v367.json (VLM 4 blocks + meta)
    char_blocks_path = out_dir / "char_blocks_v367.json"
    char_blocks_path.write_text(json.dumps({
        "CHAR_COLOR_RIOT_GIRL": CHAR_BLOCK,
        "STYLE_BLOCK_V367": STYLE_BLOCK,
        "COLOR_BLOCK_V367": COLOR_BLOCK,
        "LIGHTING_BLOCK_V367": LIGHTING_BLOCK,
        "HORIZONTAL_FRAME": HORIZONTAL_FRAME,
        "FORCED_INSTRUCTION": FORCED_INSTRUCTION,
        "BANNED_PHRASES": list(BANNED_PHRASES),
        "version": "v3.6.7",
        "task_book": "oc_task_v367.txt §3",
        "method": "reference_to_video",
        "node_class": "MiniMaxH3ReferenceToVideo",
        "vlm_profile_ref": str(profile_path),
        "ref_images_manifest": str(
            ROOT / "output" / "pipeline_v36" / "ref_images_v367" / "manifest.json"),
        "notes": [
            "单一主角色 Color Riot Girl (来自 VLM 视觉档案 §1)",
            "横屏 1344x576 (2.36:1) 跟随参考视频画幅",
            "用 MiniMaxH3ReferenceToVideo (ref_images 4 张, ref_image_size=max)",
            "character/style/color/lighting 4 个 block 完全引用 VLM 输出",
            "禁止 v366 banned + v367 VLM §7.5 banned_in_prompt",
            "禁止链式续写 (v366 失败点)",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[v367-pack] char_blocks -> {char_blocks_path}")

    n = len(BUILDERS)
    total_dur = 0.0
    print(f"[v367-pack] {n} shots -> {out_dir}")
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
        print(f"[v367-pack] shot{shot_idx:02d} phase={meta['phase']:10s} "
              f"dur={seg_dur:.1f}s chars={len(prompt):4d} "
              f"-> {prompt_path.name}",
              flush=True)

    verify = verify_no_banned_phrases(out_dir)
    verify["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    verify_path = out_dir / "verify_forced_v367.json"
    verify_path.write_text(json.dumps(verify, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[v367-pack] verify -> {verify_path} ok={verify['ok']} "
          f"n_clean={verify['n_clean']}/{verify['n_prompts']}")
    if verify["errors"]:
        for e in verify["errors"]:
            print(f"[v367-pack] ERROR {e['file']}: "
                  f"banned={e['banned_hits']} missing={e['missing']}",
                  flush=True)
    print(f"[v367-pack] total duration = {total_dur}s "
          f"({total_dur*2:.0f} beats @ 120 BPM)")

    # rhythm_plan_v367.json (供 compose 读取)
    rhythm_plan = {
        "pipeline_version": "v3.6.7",
        "bpm": 120.0,
        "beat_period_sec": 0.5,
        "n_shots": n,
        "total_duration_sec": total_dur,
        "method": "reference_to_video",
        "node_class": "MiniMaxH3ReferenceToVideo",
        "ref_images_count": 4,
        "ref_images_indices": [1, 4, 7, 11],
        "shots": [
            {
                "index": i,
                "phase": SEGMENT_PHASE[i],
                "duration_sec": SEGMENT_DURATIONS_SEC[i],
                "downbeat_start": sum(SEGMENT_DURATIONS_SEC[j]
                                      for j in range(1, i)),
                "downbeat_end": sum(SEGMENT_DURATIONS_SEC[j]
                                    for j in range(1, i+1)),
            }
            for i in range(1, n+1)
        ],
    }
    rhythm_plan_path = out_dir / "rhythm_plan_v367.json"
    rhythm_plan_path.write_text(json.dumps(rhythm_plan, ensure_ascii=False,
                                          indent=2), encoding="utf-8")
    print(f"[v367-pack] rhythm_plan -> {rhythm_plan_path}")
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
