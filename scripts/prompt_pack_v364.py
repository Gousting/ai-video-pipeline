#!/usr/bin/env python3
"""v3.6.4 prompt-pack: 全流程重构后的 6 段新 prompt (per oc_task_v364.txt §3)。

核心差异 vs prompt_pack_v35.py:

- 角色重新定义（任务书 §3.2）：
  - 学姐（棕发）：棕发双马尾 + 橙色发带 + 星星挂件 + 制服
  - 学妹（黑发）：黑长直 + 黄瞳 + choker + 深蓝制服
  （v363 中 senior/junior 定义互换 → 本任务按任务书重新对齐参考视频）
- 强制指令（任务书 §3.3，每段 prompt 显式声明）：
  - 首帧必须直接上角色，禁止抽象/霓虹光斑/手部/布料特写开场
  - 禁止 fade in from white / from black，首帧即有清晰内容
  - 禁止抽象畸变/像素化/错乱几何/旋转参照物/重复贴图
  - 每段一个主要动作/镜头，不要堆砌过多元素
- 节奏按参考视频重构（任务书 §2.2）：
  - slow_open(20拍/10s) → build(16拍/8s) → fast(12拍/6s) → peak(20拍/10s)
    → fast(12拍/6s) → tail(16拍/8s) = 48s
  - BGM 120 BPM 网格（0.5s/拍），切点落拍
- 竖屏构图：768x1344（宽×高），主体居中偏上
- 风格基调（任务书 §3.1）：米山舞式二次元唯美风，高饱和 pop/anime 视觉

CLI:
  python prompt_pack_v364.py --out-dir output/pipeline_v36/shots_v364
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "shots_v364"

# ---- 角色定义（任务书 §3.2，跨段一致性锚点）----
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

# 竖屏 9:16 构图说明（任务书 §3.1）
VERTICAL_FRAME = (
    "vertical 9:16 portrait frame (768 wide by 1344 tall), character centered "
    "slightly above middle, generous environment above and below the subject, "
    "no extreme close-up that crops half the face, well-framed composition with "
    "full head and shoulders visible throughout"
)

# 强制指令（任务书 §3.3，每段 prompt 末尾显式声明）
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

# 风格基调（任务书 §3.1，米山舞式二次元唯美风）
STYLE_BLOCK_V364 = (
    "2D-animated, Mai Yoneyama anime cel-shading pop-art style with romantic "
    "soft-light aesthetic. Vibrant high-saturation CMYK pop-art color palette "
    "with translucent color blocks, hand-painted anime lineart, cel-shaded "
    "flat shading with subtle airbrushed gradient on skin only. Layered "
    "composition with distinct foreground character, midground props, and "
    "background atmosphere (cherry blossoms, neon city, or campus). No "
    "photorealism, no 3D render, no CGI. Crisp detail on character features: "
    "hair ornaments, pendants, ribbons, uniform trim."
)

# 声音床（沿用 v35，避免 H3 生成单音轨）
SOUNDSCAPE_V364 = (
    "Soft spring-pop ambient bed — gentle spring breeze through cherry petals, "
    "distant soft chimes ringing faintly, a very faint heartbeat-style bass pulse "
    "under the BGM. No voices, no spoken dialogue, no narration, no edge-tts, "
    "no vocalization of any kind throughout the entire video. The ambient bed "
    "remains continuous across every shot cut. NOTE: do not synthesize whoosh "
    "or any inter-shot SFX during generation; inter-shot audio treatment is "
    "handled externally by an ffmpeg mixer."
)

MUSIC_SKELETON_V364 = (
    "non-diegetic music: 120 BPM beat grid, minimal background beat, low volume, "
    "no melody. Audio bed is intentionally sparse during generation; the final "
    "layered BGM track is mixed in post by an external ffmpeg mixer to keep every "
    "beat aligned to the 120 BPM grid and to avoid the H3 monolithic generated tone."
)


def build_prompt_shot1() -> tuple[str, dict]:
    """段1 学姐开场（slow_open, ~10s, 20拍）。
    开门见山直接上角色，正面/侧身回眸，定调。
    """
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
    body = (
        f"{STYLE_BLOCK_V364}\n\n"
        f"{char_block}\n\n"
        f"{scene}\n\n"
        f"{timeline}\n\n"
        f"{camera}\n\n"
        f"{FORCED_INSTRUCTION}"
    )
    prompt = (
        f"integrated_multimodal_description:\n{body}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V364}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V364}"
    )
    meta = {
        "shot": 1, "phase": "slow_open", "duration_sec": 10.0,
        "downbeat_start": 0.0, "include_senior": False, "include_junior": False,
        "characters": ["xuejie_brown_twintails"],
        "prompt_chars": len(prompt),
        "bpm_target": 120.0, "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.4",
        "open_with_character": True, "no_fade_in": True,
    }
    return prompt, meta


def build_prompt_shot2() -> tuple[str, dict]:
    """段2 学妹登场（build, ~8s, 16拍）。
    学妹出现，好奇神态，登场动作。
    """
    char_block = (
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[FRAME] {VERTICAL_FRAME}"
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
        "[SHOT_TIMELINE] Single primary motion chain executed in sequence: "
        "first the camera holds medium shot with the new character clearly "
        "visible from frame one; then she lifts her right hand to chest "
        "height and gently tucks a strand of black hair behind her right ear, "
        "the silver ring catching the warm light; then her gaze softens and "
        "she gives a small slow nod. The character remains the single clear "
        "focal point throughout. No other character in this shot. No abstract "
        "overlay. No rapid motion."
    )
    camera = "[CAMERA] static with very subtle 5 percent push in, amplitude small, speed slow."
    body = (
        f"{STYLE_BLOCK_V364}\n\n"
        f"{char_block}\n\n"
        f"{scene}\n\n"
        f"{timeline}\n\n"
        f"{camera}\n\n"
        f"{FORCED_INSTRUCTION}"
    )
    prompt = (
        f"integrated_multimodal_description:\n{body}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V364}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V364}"
    )
    meta = {
        "shot": 2, "phase": "build", "duration_sec": 8.0,
        "downbeat_start": 10.0, "include_senior": False, "include_junior": False,
        "characters": ["xuemei_black_long"],
        "prompt_chars": len(prompt),
        "bpm_target": 120.0, "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.4",
        "open_with_character": True, "no_fade_in": True,
    }
    return prompt, meta


def build_prompt_shot3() -> tuple[str, dict]:
    """段3 双人同框（fast, ~6s, 12拍）。
    双人互动，节奏加快但仍清晰可辨。
    """
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
    scene = (
        "[SCENE] An outdoor campus bench area at golden hour. Two characters "
        "stand facing each other, both clearly visible from head to mid-torso. "
        "Warm sunlight from the upper left rims both characters' hair and "
        "shoulders. Background shows a soft-focus row of cherry blossom trees "
        "with petals drifting between them."
    )
    timeline = (
        "[SHOT_TIMELINE] Single primary motion chain executed in sequence: "
        "first the camera frames both characters clearly from frame one, "
        "brown twin tails on left and black long hair on right, both with "
        "eyes visible; then xuejie on the left reaches her right hand out "
        "toward xuemei as if to offer a small object (a single cherry petal "
        "resting on her palm, clearly shown); then xuemei on the right "
        "lifts her gaze from the petal to xuejie's eyes, a small surprised "
        "smile forming. No rapid whip pan. Both characters remain in frame "
        "throughout. No abstract overlay. No third character."
    )
    camera = "[CAMERA] static medium two-shot, amplitude none, speed slow."
    body = (
        f"{STYLE_BLOCK_V364}\n\n"
        f"{char_block}\n\n"
        f"{scene}\n\n"
        f"{timeline}\n\n"
        f"{camera}\n\n"
        f"{FORCED_INSTRUCTION}"
    )
    prompt = (
        f"integrated_multimodal_description:\n{body}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V364}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V364}"
    )
    meta = {
        "shot": 3, "phase": "fast", "duration_sec": 6.0,
        "downbeat_start": 18.0, "include_senior": False, "include_junior": False,
        "characters": ["xuejie_brown_twintails", "xuemei_black_long"],
        "prompt_chars": len(prompt),
        "bpm_target": 120.0, "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.4",
        "open_with_character": True, "no_fade_in": True,
    }
    return prompt, meta


def build_prompt_shot4() -> tuple[str, dict]:
    """段4 情绪锚（peak, ~10s, 20拍）。
    最具画面冲击的镜头，全片高潮段，稳住。
    """
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] Both characters clearly visible in the same frame. "
        f"xuejie (brown twin tails, orange ribbons) on left, xuemei (black "
        f"long hair, navy blazer, choker) on right. They stand close enough "
        f"to suggest emotional closeness, both eyes clearly visible.\n"
        f"[FRAME] {VERTICAL_FRAME}"
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
        "[SHOT_TIMELINE] Single primary motion chain executed in sequence: "
        "first the camera frames both characters clearly from frame one in "
        "a stable medium two-shot, both faces and uniforms in focus; then "
        "xuemei on the right slowly turns her head toward xuejie on the "
        "left, her gaze drifting up to meet xuejie's eyes; then xuejie on "
        "the left returns the gaze with a soft warm smile, the orange "
        "ribbons and star pendant clearly catching a glint of dusk light. "
        "Both characters remain the clear focal point throughout. No "
        "rapid camera move. No abstract overlay. No third character."
    )
    camera = "[CAMERA] slow cinematic push in, amplitude small, speed very slow."
    body = (
        f"{STYLE_BLOCK_V364}\n\n"
        f"{char_block}\n\n"
        f"{scene}\n\n"
        f"{timeline}\n\n"
        f"{camera}\n\n"
        f"{FORCED_INSTRUCTION}"
    )
    prompt = (
        f"integrated_multimodal_description:\n{body}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V364}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V364}"
    )
    meta = {
        "shot": 4, "phase": "peak", "duration_sec": 10.0,
        "downbeat_start": 24.0, "include_senior": False, "include_junior": False,
        "characters": ["xuejie_brown_twintails", "xuemei_black_long"],
        "prompt_chars": len(prompt),
        "bpm_target": 120.0, "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.4",
        "open_with_character": True, "no_fade_in": True,
    }
    return prompt, meta


def build_prompt_shot5() -> tuple[str, dict]:
    """段5 双人对比（fast, ~6s, 12拍）。
    双人风格/神态对比快切。
    """
    char_block = (
        f"[CHARACTER_XUEJIE] {CHAR_SENIOR_XUEJIE}\n"
        f"[CHARACTER_XUEMEI] {CHAR_JUNIOR_XUEMEI}\n"
        f"[INTERACTION] xuejie (brown twin tails, cream sailor uniform, "
        f"orange ribbons) on left of frame; xuemei (black long hair, navy "
        f"blazer, choker) on right of frame. Both clearly visible from "
        f"head to mid-torso, contrasting warm vs cool color palette.\n"
        f"[FRAME] {VERTICAL_FRAME}"
    )
    scene = (
        "[SCENE] Split composition in a glass-roofed campus hallway at noon. "
        "Warm sunlight pours from above through the glass, casting soft "
        "light beams that divide the frame. The brown-twin-tail character "
        "(xuejie) stands in the warm left half, the black-long-hair "
        "character (xuemei) stands in the cool right half. Both are clearly "
        "visible from head to mid-torso in medium shot. Background shows "
        "a blurred hallway extending into depth."
    )
    timeline = (
        "[SHOT_TIMELINE] Single primary motion chain executed in sequence: "
        "first the camera frames both characters clearly from frame one in "
        "the split-light medium two-shot; then xuejie on the left tilts "
        "her chin up with a confident small smile, twin tails swaying "
        "slightly; then xuemei on the right responds with a soft gentle "
        "smile, a hand rising slightly toward her choker. Both characters "
        "remain the clear focal point throughout. No rapid camera move. "
        "No abstract overlay. No third character."
    )
    camera = "[CAMERA] static with very subtle 5 percent dolly, amplitude small, speed slow."
    body = (
        f"{STYLE_BLOCK_V364}\n\n"
        f"{char_block}\n\n"
        f"{scene}\n\n"
        f"{timeline}\n\n"
        f"{camera}\n\n"
        f"{FORCED_INSTRUCTION}"
    )
    prompt = (
        f"integrated_multimodal_description:\n{body}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V364}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V364}"
    )
    meta = {
        "shot": 5, "phase": "fast", "duration_sec": 6.0,
        "downbeat_start": 34.0, "include_senior": False, "include_junior": False,
        "characters": ["xuejie_brown_twintails", "xuemei_black_long"],
        "prompt_chars": len(prompt),
        "bpm_target": 120.0, "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.4",
        "open_with_character": True, "no_fade_in": True,
    }
    return prompt, meta


def build_prompt_shot6() -> tuple[str, dict]:
    """段6 收束（tail, ~8s, 16拍）。
    双人远景/背影，氛围定格收尾。
    """
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
        "[SHOT_TIMELINE] Single primary motion chain executed in sequence: "
        "first the camera frames both characters clearly from frame one in "
        "a stable wide shot, walking away side by side along the path; "
        "then their silhouettes pass under a warm amber street lamp, "
        "hair and uniform hems catching the light; then the camera holds "
        "as they continue to walk into the soft focus distance, the "
        "falling cherry petals drifting across frame in foreground. Both "
        "characters remain visible throughout. No rapid camera move. No "
        "abstract overlay. No third character."
    )
    camera = "[CAMERA] slow gentle pull back, amplitude small, speed slow."
    body = (
        f"{STYLE_BLOCK_V364}\n\n"
        f"{char_block}\n\n"
        f"{scene}\n\n"
        f"{timeline}\n\n"
        f"{camera}\n\n"
        f"{FORCED_INSTRUCTION}"
    )
    prompt = (
        f"integrated_multimodal_description:\n{body}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V364}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V364}"
    )
    meta = {
        "shot": 6, "phase": "tail", "duration_sec": 8.0,
        "downbeat_start": 40.0, "include_senior": False, "include_junior": False,
        "characters": ["xuejie_brown_twintails", "xuemei_black_long"],
        "prompt_chars": len(prompt),
        "bpm_target": 120.0, "beat_period_sec": 0.5,
        "pipeline_version": "v3.6.4",
        "open_with_character": True, "no_fade_in": True,
    }
    return prompt, meta


BUILDERS = {
    1: build_prompt_shot1,
    2: build_prompt_shot2,
    3: build_prompt_shot3,
    4: build_prompt_shot4,
    5: build_prompt_shot5,
    6: build_prompt_shot6,
}

# 段时长（拍点网格 120 BPM = 0.5s/拍）
SEGMENT_DURATIONS_SEC = {1: 10.0, 2: 8.0, 3: 6.0, 4: 10.0, 5: 6.0, 6: 8.0}


def verify_forced_instructions(out_dir: Path) -> dict:
    """验证每段 prompt 都满足 §3.3 强制指令（无 fade in、无 abstract open）。

    检查项：
      1) 必含禁止词声明（"no fade-in from white" 等），证明 prompt 在主动避开问题。
      2) 必含 MANDATORY CONTENT RULES 段。
      3) 必含 "Single clear subject" 主张。
      4) 不能有「开场即 fade in from white」的指令性写法（即前缀不能是
         "fade in from white" 而非 "no fade in from white"）。
    """
    BANNED_INSTRUCTION_RE = re.compile(
        r"(?i)(?<!no )(?<!never )(?<!do not )(?<!don't )"
        r"(fade[- ]?in from white|fade[- ]?in from black|fade from white|fade from black|"
        r"opening with hand close|opening on hand|opening on fabric|opening on accessory)"
    )
    REQUIRED_PHRASES = (
        "MANDATORY CONTENT RULES",
        "no fade-in from white",
        "no fade-in from black",
        "Single clear subject",
    )
    report: dict = {"n_prompts": 0, "n_clean": 0, "errors": []}
    for cp in sorted(out_dir.glob("shot*_prompt.txt")):
        report["n_prompts"] += 1
        text = cp.read_text(encoding="utf-8")
        banned_hits = [m.group(0) for m in BANNED_INSTRUCTION_RE.finditer(text)]
        missing = [p for p in REQUIRED_PHRASES if p not in text]
        if banned_hits or missing:
            report["errors"].append({
                "file": cp.name,
                "banned_hits": banned_hits,
                "missing_required_phrases": missing,
            })
        else:
            report["n_clean"] += 1
    report["ok"] = (report["n_clean"] == report["n_prompts"])
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    char_blocks_path = out_dir / "char_blocks_v364.json"
    char_blocks_path.write_text(json.dumps({
        "CHAR_XUEJIE_BROWN_TWINTAILS": CHAR_SENIOR_XUEJIE,
        "CHAR_XUEMEI_BLACK_LONG": CHAR_JUNIOR_XUEMEI,
        "STYLE_BLOCK_V364": STYLE_BLOCK_V364,
        "VERTICAL_FRAME": VERTICAL_FRAME,
        "FORCED_INSTRUCTION": FORCED_INSTRUCTION,
        "version": "v3.6.4",
        "task_book": "oc_task_v364.txt §3.2 §3.3",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(BUILDERS)
    total_dur = 0.0
    print(f"[v364-pack] {n} shots → {out_dir}", flush=True)
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
        print(f"[v364-pack] shot{shot_idx:02d} phase={meta['phase']} "
              f"dur={seg_dur}s prompt_chars={len(prompt)} → {prompt_path.name}",
              flush=True)

    verify = verify_forced_instructions(out_dir)
    verify["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    verify_path = out_dir / "verify_forced_v364.json"
    verify_path.write_text(json.dumps(verify, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[v364-pack] verify → {verify_path} ok={verify['ok']} "
          f"n_clean={verify['n_clean']}/{verify['n_prompts']}", flush=True)
    print(f"[v364-pack] total duration = {total_dur}s ({total_dur*2:.0f} beats @ 120 BPM)",
          flush=True)

    phase_map = {1: "slow_open", 2: "build", 3: "fast",
                 4: "peak", 5: "fast", 6: "tail"}
    rhythm_plan = {
        "pipeline_version": "v3.6.4",
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
            }
            for i in range(1, n+1)
        ],
    }
    rhythm_plan_path = out_dir / "rhythm_plan_v364.json"
    rhythm_plan_path.write_text(json.dumps(rhythm_plan, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(f"[v364-pack] rhythm_plan → {rhythm_plan_path}", flush=True)
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
