#!/usr/bin/env python3
"""v3.1 Prompt Pack：基于 v3 prompt-pack 加"段间引导"措辞。

增量：
  - shot01: 纯 T2V 措辞（"opening establishing shot"），不附 continuity
  - shot02~08: 每段 prompt 末尾追加"continuity from previous shot"块，明确告诉模型
    当前段是上一段的自然延续（保持光照/角色/场景氛围），减少段间硬切感。

与 v3 prompt-pack 字段约定一致：
  STYLE_BLOCK + SCENE_BLOCK + ACTION_BLOCK + CAMERA_BLOCK + NARRATION + CHARACTERS +
  ANTI_BLOCK + SOUNDSCAPE + MUSIC
+ (NEW v3.1) CONTINUITY_BLOCK（shot02+）

CLI:
  python prompt_pack_v31.py                  # 全量重生成 shot01-shot08
  python prompt_pack_v31.py --start 2 --end 8  # 只重生成 shot02-shot08
  python prompt_pack_v31.py --dry-run        # 只打印不写
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
SB_PATH = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard.json"
CHAR_PATH = ROOT / "output" / "pipeline_v3" / "clips" / "char_blocks.json"
STYLE_PATH = ROOT / "output" / "pipeline_v3" / "clips" / "style_block.txt"
CLIPS_DIR = ROOT / "output" / "pipeline_v3" / "clips"


# ---------------------------------------------------------------------------
# 段间引导措辞（关键：保持光照/角色/场景/动势连续）
# ---------------------------------------------------------------------------

CONTINUITY_BLOCK_TEMPLATE = (
    "Continuity from previous shot: this shot continues naturally from the immediately "
    "preceding shot with consistent visual continuity — same warm golden-hour lighting "
    "and time-of-day atmosphere, same hand-painted cel-shaded anime color palette and "
    "lighting direction, same character designs (CHAR_SENIOR silver-white twin braids / "
    "CHAR_JUNIOR chestnut twin tails with red ribbons), same campus spring season "
    "context with cherry blossom petals in air when applicable, and a smooth visual "
    "transition in subject blocking, gaze direction, and motion arc from where the "
    "previous shot ended. The first frame should feel like a direct continuation of "
    "the previous shot's final frame — preserving the same lighting setup, color "
    "temperature, focal length, depth of field, character pose baseline, and background "
    "composition density so the cut between shots is visually seamless."
)

OPENING_BLOCK = (
    "Opening establishing shot: this is the very first shot of the video and sets "
    "the visual baseline for all subsequent shots. Establish the warm golden-hour "
    "lighting setup, hand-painted cel-shaded anime color palette, campus spring "
    "atmosphere, and the silhouettes of both main characters (CHAR_SENIOR and "
    "CHAR_JUNIOR) at a distance so they can be visually anchored throughout the "
    "rest of the video."
)


def load_blocks() -> tuple[dict, dict, str]:
    sb = json.loads(SB_PATH.read_text(encoding="utf-8"))
    chars = json.loads(CHAR_PATH.read_text(encoding="utf-8"))
    style = STYLE_PATH.read_text(encoding="utf-8").strip()
    return sb, chars, style


def compose_prompt(shot: dict, chars: dict, style: str, with_continuity: bool) -> str:
    """组装一段完整 prompt（H3 官方三段式：desc + soundscape + music）。"""
    include_senior = shot.get("include_senior", False)
    include_junior = shot.get("include_junior", False)

    # 1. integrated_multimodal_description（H3 官方要求第一段）
    desc_parts = [f"In a {style}."]
    scene_text = shot.get("scene", "").strip()
    action_text = shot.get("action", "").strip()
    cam = shot.get("camera", {})
    cam_text = (
        f"The camera moves with a {cam.get('type','static')} "
        f"with {cam.get('amplitude','small')} amplitude at {cam.get('speed','slow')} speed, "
        f"preserving the gentle anime pacing "
        f"(type={cam.get('type','static')}, amplitude={cam.get('amplitude','small')}, "
        f"speed={cam.get('speed','slow')})."
    )
    desc_parts.append(scene_text)
    desc_parts.append(action_text)
    desc_parts.append(cam_text)
    desc_parts.append(f"Narration beat (for audio sync): {shot.get('narration','')}")

    # 2. CONTINUITY_BLOCK（关键增量）
    if with_continuity:
        desc_parts.append(CONTINUITY_BLOCK_TEMPLATE)
    else:
        desc_parts.append(OPENING_BLOCK)

    # 3. CHARACTERS（公共参数；每段只引用出场角色）
    if include_senior:
        desc_parts.append(f"CHAR_SENIOR: {chars['CHAR_SENIOR']}")
    if include_junior:
        desc_parts.append(f"CHAR_JUNIOR: {chars['CHAR_JUNIOR']}")

    # 4. ANTI_BLOCK（公共；逐段引用）
    desc_parts.append(f"CHAR_AUTHOR_INSTRUCTION: {chars['ANTI_BLOCK']}")

    # 5. SOUNDSCAPE_BLOCK
    desc_parts.append(f"overall_soundscape: {chars['SOUNDSCAPE_BLOCK']}")

    # 6. MUSIC_BLOCK（v3.1 关键：保持段间一致，避免音乐断裂）
    # 与 v3 不同的微调：明确说明此为全片统一主题，每段不该再独立生成 BGM
    desc_parts.append(
        "non_diegetic_music: " + chars['MUSIC_BLOCK'] + " This is a unified school-day "
        "loop theme shared across all segments of the video; do not introduce new "
        "melodic phrases, tempo shifts, or stylistic breaks within this segment, "
        "keep the same harmonic progression as the global theme so all segments stitch "
        "into a single continuous soundtrack."
    )

    return "\n\n".join(desc_parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1, help="起始段号（含）")
    ap.add_argument("--end", type=int, default=8, help="结束段号（含）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写")
    args = ap.parse_args(argv)

    sb, chars, style = load_blocks()
    shots = sb.get("shots", [])

    n_written = 0
    for shot in shots:
        idx = shot["index"]
        if idx < args.start or idx > args.end:
            continue
        # shot01 无前段 → 不带 continuity；其余段都带
        with_continuity = (idx > 1)
        prompt = compose_prompt(shot, chars, style, with_continuity=with_continuity)

        out_path = CLIPS_DIR / f"shot{idx:02d}_prompt_v31.txt"
        if args.dry_run:
            print(f"\n========== shot{idx:02d} (with_continuity={with_continuity}) ==========")
            print(prompt)
        else:
            out_path.write_text(prompt, encoding="utf-8")
            print(f"[pack] shot{idx:02d} -> {out_path} ({len(prompt)} chars, "
                  f"continuity={with_continuity})", flush=True)
            n_written += 1

    if not args.dry_run:
        print(f"\n[pack] done: wrote {n_written} prompt_v31.txt in {CLIPS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())