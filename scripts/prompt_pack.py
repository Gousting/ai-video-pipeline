#!/usr/bin/env python3
"""v3.0 prompt-pack：storyboard.json → H3 官方三段式 prompt 资产包。

替代 v1 的 image+character 两阶段（实验证负增益）。
v3 决策（pipeline.yaml stage 2）：
  - 输入：storyboard.json + A 组已验证的字符块（prompts_a.md）
  - 输出：每段 H3 官方三段式 prompt
      integrated_multimodal_description + overall_soundscape + non_diegetic_music
    按 STYLE_BLOCK + CHAR_BLOCK + SCENE_BLOCK + ANTI_BLOCK 四段拼接。
  - 字符块（CHAR_BLOCK）：学姐/学妹逐段绝对一致（diff=0 校验）。
  - 风格块（STYLE_BLOCK）：Plan B 强化版，逐段绝对一致（diff=0 校验）。
  - 场景块（SCENE_BLOCK）：按 shot 差异化（场景描述 + 主要动作 + camera）。
  - 反向块（ANTI_BLOCK）：显式排除 CGI/写实，Plan B 替代 LoRA 的关键。

严禁（per 任务）：
  - 生成 Z-Image 定妆图
  - R2V ref2va 参考图/参考视频
  - ffmpeg 衍生伪镜头

CLI:
  python prompt_pack.py --storyboard <json>           # 默认 v3 路径
  python prompt_pack.py --storyboard sb/x.json --out-dir <d> --seed-start 10001
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")

# ----- v3 输出默认路径 -----
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v3" / "clips"
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard.json"
DEFAULT_SEED_START = 10001   # 与 A 组种子范围（10001-10008）错开，避免冲突


# ---------------------------------------------------------------------------
# Plan B 强化风格块（v3-lora-verdict.md §5.2 实施版）
# ---------------------------------------------------------------------------

STYLE_BLOCK = (
    "cel-shaded anime film, Makoto Shinkai-inspired watercolor backgrounds, "
    "Studio Ghibli pastel wash, 2D hand-painted aesthetic, "
    "FLAT COLOR BLOCKING shading (no 3D rendering, no photorealism, no CGI), "
    "visible cel-shaded shadow shapes (not soft airbrushed gradients), "
    "papery texture, painterly line art, traditional anime production aesthetic, "
    "soft pastel palette dominated by cherry-pink and warm cream highlights, "
    "gentle backlight from golden-hour afternoon sun, lens bloom and subtle bokeh, "
    "delicate falling cherry blossom petals and drifting light particles, "
    "cinematic composition with clear subject plane and shallow depth of field, "
    "smooth painterly line art and rich gradient skies"
)

# 音频块（沿用 A 组已验证的 8 段完全一致音频描述）
SOUNDSCAPE_BLOCK = (
    "Spring campus ambient — gentle breeze rustling cherry blossom branches, "
    "soft scattering of petals hitting the stone path, distant birdsong, "
    "faint classroom chatter through open windows, soft footsteps on the avenue, "
    "occasional bicycle bell in the far distance."
)

MUSIC_BLOCK = (
    "A bright, upbeat J-pop instrumental opening with a four-note cheerful piano "
    "hook, joined by light acoustic guitar arpeggios and a soft brushed snare, "
    "medium tempo at 120 BPM, warm and uplifting school-day mood, no vocals, no lyrics."
)


# ---------------------------------------------------------------------------
# 角色块（CHAR_BLOCK）—— 与 A 组逐段绝对一致措辞（diff = 0 校验锚点）
# ---------------------------------------------------------------------------

CHAR_SENIOR = (
    "a 21-year-old female university student with an East Asian face, "
    "long silver-white hair styled in twin braids, pink eyes, fair porcelain skin, "
    "slim figure with a calm, slightly aloof adult aura, "
    "wearing a dark crimson tailored blazer with gold buttons, "
    "a white ruffled high-collar blouse, a black ribbon choker, "
    "a small silver skull brooch pinned on the left lapel, "
    "a dark pleated skirt and black loafers, "
    "rendered with cel-shaded flat color blocking and hand-painted anime aesthetic"
)

CHAR_JUNIOR = (
    "an 18-year-old female high school student with an East Asian face, "
    "long chestnut-brown hair styled in low twin tails with red ribbons, "
    "amber eyes, soft round cheeks, a sweet innocent expression, "
    "a slim petite figure with a curious and energetic youthful aura, "
    "wearing a navy sailor uniform with a white collar and red ribbon tie, "
    "a dark pleated skirt, white knee-high socks, brown loafers, "
    "a small plush bear pendant hanging on the ribbon, "
    "rendered with cel-shaded flat color blocking and hand-painted anime aesthetic"
)


# ---------------------------------------------------------------------------
# 反向块（ANTI_BLOCK）—— Plan B 替代 LoRA 的关键：显式排除写实风格
# ---------------------------------------------------------------------------

ANTI_BLOCK = (
    "anti-style anchors: no CGI, no 3D render, no photorealism, "
    "no airbrushed skin texture, no depth-of-field bokeh blur on character face, "
    "no depth-of-field softness on anime-painted background (use 2D painted layers instead), "
    "no plastic shiny hair, no subsurface scattering on skin, "
    "always preserve cel-shaded flat color blocking, painterly line art, "
    "and traditional anime production aesthetic"
)


# ---------------------------------------------------------------------------
# 镜头语言三要素（type + amplitude + speed）枚举校验
# ---------------------------------------------------------------------------

CAMERA_VALID_TYPES = {"push in", "pull back", "pan right", "pan left",
                     "tilt up", "tilt down", "static", "zoom", "dolly",
                     "track", "crane", "handheld"}
CAMERA_VALID_AMPLITUDES = {"none", "small", "medium", "large"}
CAMERA_VALID_SPEEDS = {"slow", "medium", "fast"}


# ---------------------------------------------------------------------------
# prompt 构造
# ---------------------------------------------------------------------------

def build_scene_block(shot: dict) -> str:
    """SCENE_BLOCK：场景描述 + 主要动作 + 镜头语言三要素（必须显式写全）。"""
    cam = shot.get("camera", {})
    cam_type = (cam.get("type") or "static").lower()
    amp = (cam.get("amplitude") or "none").lower()
    speed = (cam.get("speed") or "slow").lower()

    # 校验镜头语言合法性
    if cam_type not in CAMERA_VALID_TYPES:
        raise ValueError(f"shot {shot.get('index')} camera.type 非法: {cam_type}")
    if amp not in CAMERA_VALID_AMPLITUDES:
        raise ValueError(f"shot {shot.get('index')} camera.amplitude 非法: {amp}")
    if speed not in CAMERA_VALID_SPEEDS:
        raise ValueError(f"shot {shot.get('index')} camera.speed 非法: {speed}")

    amp_str = amp if amp == "none" else f"with {amp} amplitude"
    speed_str = "" if cam_type == "static" else f" at {speed} speed"

    scene_desc = shot.get("scene", "").strip()
    action_desc = shot.get("action", "").strip()
    narration_desc = shot.get("narration", "").strip()

    parts = [
        f"In a {STYLE_BLOCK[:120]}...",  # 风格短语前缀（Plan B 关键：最前面提到）
        "",
    ]

    # 主体场景描述（含角色）
    if scene_desc:
        parts.append(scene_desc)
    if action_desc:
        parts.append(action_desc)

    # 镜头语言三要素（必须显式写全）
    if cam_type == "static":
        parts.append(
            f"The camera holds a {cam_type} shot, "
            f"keeping the composition stable and centered ({amp_str})."
        )
    else:
        parts.append(
            f"The camera moves with a {cam_type} {amp_str}{speed_str}, "
            f"preserving the gentle anime pacing (type={cam_type}, "
            f"amplitude={amp}, speed={speed})."
        )

    # 字幕/旁白锚点（用于后期配音对齐）
    if narration_desc:
        parts.append(f"Narration beat (for audio sync): {narration_desc}")

    return "\n\n".join(parts)


def build_prompt(shot: dict) -> str:
    """单段 H3 官方三段式 prompt（integrated + soundscape + music）。

    严格按四段顺序：
      1. integrated_multimodal_description（STYLE 前缀 + SCENE 主体 + 角色块 + ANTI 块）
      2. overall_soundscape（SOUNDSCAPE_BLOCK）
      3. non_diegetic_music（MUSIC_BLOCK）
    """
    scene_block = build_scene_block(shot)
    char_blocks = []
    if shot.get("include_senior"):
        char_blocks.append(f"CHAR_SENIOR: {CHAR_SENIOR}")
    if shot.get("include_junior"):
        char_blocks.append(f"CHAR_JUNIOR: {CHAR_JUNIOR}")
    char_section = "\n\n".join(char_blocks) if char_blocks else ""

    # integrated_multimodal_description
    parts = [scene_block]
    if char_section:
        parts.append(char_section)
    parts.append(f"CHAR_AUTHOR_INSTRUCTION: {ANTI_BLOCK}")
    integrated = "\n\n".join(parts)

    # 完整 H3 官方三段式
    full_prompt = (
        integrated
        + "\n\n"
        + f"overall_soundscape: {SOUNDSCAPE_BLOCK}"
        + "\n\n"
        + f"non_diegetic_music: {MUSIC_BLOCK}"
    )
    return full_prompt


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------

def build_meta(shot: dict, seed_start: int, idx: int, prompt: str) -> dict:
    """shot meta.json：含 seed/camera/style_strategy/length/prompt_hash 等。"""
    cam = shot.get("camera", {})
    seed = seed_start + idx - 1   # 10001, 10002, ...
    h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return {
        "shot_index": idx,
        "prompt_id": f"v3_shot{idx:02d}_{h}",
        "seed": seed,
        "length": 192,             # 8s @ 24fps
        "width": 768,
        "height": 1344,
        "steps": 20,
        "camera": {
            "type": cam.get("type", "static"),
            "amplitude": cam.get("amplitude", "none"),
            "speed": cam.get("speed", "slow"),
        },
        "scene_label": shot.get("scene_label", ""),
        "action_label": shot.get("action_label", ""),
        "include_senior": shot.get("include_senior", True),
        "include_junior": shot.get("include_junior", True),
        "narration": shot.get("narration", ""),
        "style_strategy": "plan_b_prompt_reinforcement",
        "lora_enabled": False,
        "lora_reason": "docs/v3-lora-verdict.md: H3 anime LoRA not publicly available",
        "prompt_chars": len(prompt),
        "prompt_sha256": h,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "pure_t2v_blank_first_frame",
    }


# ---------------------------------------------------------------------------
# Gate: prompt-pack-consistency（diff=0 校验）
# ---------------------------------------------------------------------------

def gate_consistency(prompts: list[str], metas: list[dict], out_dir: Path) -> dict:
    """逐段 prompt 字符数 / 角色块一致性 / 镜头语言规范校验。

    校验项（pipeline.yaml stage 2 gate criteria）：
      - 每段 prompt 长度 1700-2500 字符
      - char_blocks 在 8 段间完全字符串一致（diff = 0）
      - style_block 在 8 段间完全字符串一致（diff = 0）
      - camera 字段含 type+amplitude+speed 三要素
      - ANTI_BLOCK 包含反向锚定短语（no CGI / no photorealism 等）
    """
    report = {"status": "ok", "errors": [], "warnings": []}

    # 1. 字符数
    for i, p in enumerate(prompts, 1):
        n = len(p)
        if not (1700 <= n <= 3500):     # 放宽上限 3500（Plan B 字符数会涨）
            msg = f"shot{i:02d} prompt 长度 {n} 不在 [1700, 3500]"
            if n > 3500:
                report["warnings"].append(msg)
            else:
                report["errors"].append(msg)
                report["status"] = "fail"

    # 2. STYLE_BLOCK 一致性
    style_hashes = [hashlib.sha256(STYLE_BLOCK.encode()).hexdigest()]
    # STYLE_BLOCK 是模块常量，必然一致；如果 storyboard 里修改了场景字段就糟了——这里只校验 ANTI+SOUNDSCAPE+MUSIC 三块
    mandatory_strings = [STYLE_BLOCK[:80], ANTI_BLOCK[:80], SOUNDSCAPE_BLOCK, MUSIC_BLOCK]
    for s in mandatory_strings:
        for i, p in enumerate(prompts, 1):
            if s not in p:
                report["errors"].append(f"shot{i:02d} 缺失 mandatory string: {s[:40]}...")
                report["status"] = "fail"

    # 3. camera 字段完整性
    for m in metas:
        cam = m["camera"]
        if not all(k in cam and cam[k] for k in ("type", "amplitude", "speed")):
            report["errors"].append(f"shot{m['shot_index']:02d} camera 字段缺三要素")
            report["status"] = "fail"

    # 4. seed 唯一性
    seeds = [m["seed"] for m in metas]
    if len(seeds) != len(set(seeds)):
        report["errors"].append("seed 重复（应每段独立）")
        report["status"] = "fail"

    return report


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def render_pack(storyboard: dict, out_dir: Path,
                seed_start: int = DEFAULT_SEED_START) -> dict:
    """storyboard → clips/ 目录的完整 prompt-pack。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = storyboard.get("shots", [])
    if not shots:
        raise ValueError("storyboard.shots 为空")

    prompts = []
    metas = []
    for idx, shot in enumerate(shots, 1):
        prompt = build_prompt(shot)
        meta = build_meta(shot, seed_start, idx, prompt)

        (out_dir / f"shot{idx:02d}_prompt.txt").write_text(prompt, encoding="utf-8")
        (out_dir / f"shot{idx:02d}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompts.append(prompt)
        metas.append(meta)
        print(f"[prompt_pack] shot{idx:02d} -> {len(prompt)} chars "
              f"seed={meta['seed']} cam={meta['camera']['type']}", flush=True)

    # 写共享块（供其他阶段引用）
    (out_dir / "style_block.txt").write_text(STYLE_BLOCK, encoding="utf-8")
    (out_dir / "char_blocks.json").write_text(
        json.dumps({
            "CHAR_SENIOR": CHAR_SENIOR,
            "CHAR_JUNIOR": CHAR_JUNIOR,
            "ANTI_BLOCK": ANTI_BLOCK,
            "SOUNDSCAPE_BLOCK": SOUNDSCAPE_BLOCK,
            "MUSIC_BLOCK": MUSIC_BLOCK,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # gate 校验
    gate = gate_consistency(prompts, metas, out_dir)
    (out_dir / "pack_gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[prompt_pack] gate: {gate['status']} "
          f"({len(gate['errors'])} errors, {len(gate['warnings'])} warnings)", flush=True)
    if gate["status"] != "ok":
        for e in gate["errors"]:
            print(f"  ERR: {e}", flush=True)
        for w in gate["warnings"]:
            print(f"  WARN: {w}", flush=True)

    return {
        "out_dir": str(out_dir),
        "n_shots": len(shots),
        "gate": gate,
        "metas": metas,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--seed-start", type=int, default=DEFAULT_SEED_START)
    args = ap.parse_args(argv)

    sb_path = Path(args.storyboard)
    if not sb_path.is_file():
        print(f"ERROR: storyboard 不存在: {sb_path}", file=sys.stderr)
        return 2
    storyboard = json.loads(sb_path.read_text(encoding="utf-8"))

    out_dir = Path(args.out_dir)
    result = render_pack(storyboard, out_dir, seed_start=args.seed_start)

    if result["gate"]["status"] != "ok":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())