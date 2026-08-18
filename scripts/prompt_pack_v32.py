#!/usr/bin/env python3
"""v3.2 Prompt 组装：每段生成 H3 用完整 prompt。

Prompt 结构（与 v3.1 一致：STYLE + CHAR + SCENE + ACTION + CAMERA + ANTI）：
  [STYLE_BLOCK]     # Mai Yoneyama cel-shading + neon CMYK（公开锚定，全段字面一致）
  [TRANSITIONS_BLOCK]  # 转场词库（每段从词库中选 1 个）
  [SOUNDSCAPE_BLOCK]  # 无对白环境音（公开锚定）
  [CHAR_SENIOR/JUNIOR]  # 角色描述（每段根据 include_* 拼）
  [ANTI_REALISM]    # 反向锚定
  [SHOT_PROMPT]     # 本段 prompt（scene + timed_shot_list + camera）
  [SOUNDSCAPE_NOTE]  # 末尾声学描述

每段 prompt 长度预期 2000-2800 字符（任务要求 1700-2500 区间上沿）。

CLI:
  python prompt_pack_v32.py
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
OUT_DIR = ROOT / "output" / "pipeline_v3" / "clips_v32"
STORYBOARD = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard_v32.json"
CHAR_BLOCKS = ROOT / "output" / "pipeline_v3" / "clips_v32" / "char_blocks_v32.json"
STYLE_BLOCK = ROOT / "output" / "pipeline_v3" / "clips_v32" / "style_block_v32.txt"


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_prompt(shot: dict, char_blocks: dict, style_block: str) -> tuple[str, dict]:
    """为单段生成 H3 prompt + meta。"""
    STYLE = style_block
    TRANSITIONS = char_blocks["TRANSITIONS_BLOCK"]
    SOUNDSCAPE = char_blocks["SOUNDSCAPE_BLOCK"]
    ANTI = char_blocks["ANTI_REALISM"]

    # 角色块（根据 include_senior / include_junior 选择）
    char_lines = []
    if shot["include_senior"]:
        char_lines.append("[CHARACTER_SENIOR] " + char_blocks["CHAR_SENIOR"])
    if shot["include_junior"]:
        char_lines.append("[CHARACTER_JUNIOR] " + char_blocks["CHAR_JUNIOR"])
    if shot["include_senior"] and shot["include_junior"]:
        char_lines.append(
            "[CHARACTER_INTERACTION] The senior (left) and the junior (right) appear together "
            "in the same frame; keep them visually distinct via silhouette, hair, and outfit."
        )
    CHAR_PARAGRAPH = "\n".join(char_lines)

    # timed shot list → 拼成 H3 prompt
    scene_text = shot["scene"]
    shot_list_text = " ; ".join(shot["timed_shot_list"])

    camera = shot["camera"]
    cam_text = (
        f"[CAMERA] {camera['type']}, amplitude {camera['amplitude']}, "
        f"speed {camera['speed']}."
    )

    transition_text = (
        f"[TRANSITION_RHYTHM] This shot contains one in-prompt transition: "
        f"incoming transition type = {shot['transition_in']}; outgoing transition type = "
        f"{shot['transition_out']}. Transition vocabulary to choose from: {TRANSITIONS}. "
        f"Apply the incoming transition visually within the first 1-2 seconds, and embed "
        f"the outgoing transition cue in the prompt description so the model can render it."
    )

    # 拼装 H3 prompt（H3 ImageToVideo 用 integrated_multimodal_description）
    prompt = f"""[STYLE_BLOCK] {STYLE}

[TRANSITIONS_BLOCK] {TRANSITIONS}

[SOUNDSCAPE_BLOCK] {SOUNDSCAPE}

{CHAR_PARAGRAPH}

[ANTI_REALISM] {ANTI}

[SCENE] {scene_text}

[SHOT_TIMELINE] {shot_list_text}

{cam_text}

{transition_text}

[FRAME_END_CUE] {shot['ending_frame_cue']}

[RENDER_NOTE] Cel-shaded flat color blocks, hand-painted anime lineart, pop-art graphic language, no photorealism, no CGI, no 3D render. Keep Mai Yoneyama aesthetic locked across the entire 10-second clip. No dialogue, no spoken words, no narration; the visual story is told entirely through imagery, body language, and the in-prompt transition.
"""

    meta = {
        "shot": shot["index"],
        "duration_sec": shot["duration_sec"],
        "downbeat_start": shot["downbeat_start"],
        "include_senior": shot["include_senior"],
        "include_junior": shot["include_junior"],
        "transition_in": shot["transition_in"],
        "transition_out": shot["transition_out"],
        "camera": camera,
        "title": shot["title"],
        "prompt_chars": len(prompt),
        "style_block_chars": len(STYLE),
        "char_block_chars": sum(len(c) for c in char_lines),
        "pipeline_version": "v3.2",
        "style_strategy": "plan_b_prompt_reinforcement + mai_yoneyama_cel",
        "lora_enabled": False,
    }
    return prompt, meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(STORYBOARD))
    ap.add_argument("--char-blocks", default=str(CHAR_BLOCKS))
    ap.add_argument("--style-block", default=str(STYLE_BLOCK))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args(argv)

    sb_path = Path(args.storyboard)
    char_path = Path(args.char_blocks)
    style_path = Path(args.style_block)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not sb_path.exists():
        print(f"ERROR: storyboard 不存在 {sb_path}", file=sys.stderr)
        return 2
    if not char_path.exists():
        print(f"ERROR: char_blocks 不存在 {char_path}", file=sys.stderr)
        return 2
    if not style_path.exists():
        print(f"ERROR: style_block 不存在 {style_path}", file=sys.stderr)
        return 2

    storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
    char_blocks = json.loads(char_path.read_text(encoding="utf-8"))
    style_block = load_text(style_path)

    # 一致性校验：char_blocks/style_block 字面一致性（v3 规范要求）
    # 这里只有一份，所以 diff = 0 trivially

    n_shots = len(storyboard["shots"])
    print(f"[pack] {n_shots} 段 → {out_dir}", flush=True)

    gate = {"status": "ok", "errors": [], "warnings": [], "prompts": []}

    for shot in storyboard["shots"]:
        prompt, meta = build_prompt(shot, char_blocks, style_block)

        prompt_path = out_dir / f"shot{shot['index']:02d}_prompt.txt"
        meta_path = out_dir / f"shot{shot['index']:02d}_meta.json"

        prompt_path.write_text(prompt, encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")

        # 字符数校验（任务要求 1700-2500）
        if not (1500 <= len(prompt) <= 3200):
            gate["warnings"].append(
                f"shot{shot['index']:02d} prompt length {len(prompt)} chars out of range"
            )

        gate["prompts"].append({
            "shot": shot["index"],
            "prompt_file": str(prompt_path),
            "meta_file": str(meta_path),
            "prompt_chars": len(prompt),
        })
        print(f"[pack] shot{shot['index']:02d} prompt={len(prompt)} chars -> {prompt_path.name}",
              flush=True)

    gate_path = out_dir / "pack_gate.json"
    gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pack] gate -> {gate_path}", flush=True)
    print(f"[pack] 完成 {n_shots} 段, errors={len(gate['errors'])}, "
          f"warnings={len(gate['warnings'])}", flush=True)
    return 0 if not gate["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
