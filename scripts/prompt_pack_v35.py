#!/usr/bin/env python3
"""v3.5 prompt-pack: 用 v35 storyboard（无转场词）生成 H3 prompt。

vs v3.4 (prompt_pack_v34.py) 关键差异：

- 读 storyboard_v35.json（不含 transition_in/out 字段，无转场词）
- **不再从 storyboard 复制 scene / timed_shot_list 到 prompt**——v35 storyboard
  已经在生成层去转场词，但场景描述里仍有部分风格词（halftone / radial line /
  pop-art sticker）。这些词在 H3 看来是静态视觉锚定词，不是转场词——保留 OK。
- 沿用 v34 P1/P2/P3 工艺：音乐后期化、动作限速、well-framed composition
- 输出 shot{NN}_prompt.txt + shot{NN}_meta.json + style_block + char_blocks

CLI:
  python prompt_pack_v35.py --storyboard <sb.json> --out-dir <d>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")

DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v35" / "sb" / "storyboard_v35.json"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "clips"
DEFAULT_CHAR_BLOCKS = ROOT / "output" / "pipeline_v36" / "clips" / "char_blocks_v35.json"
DEFAULT_STYLE_BLOCK = ROOT / "output" / "pipeline_v36" / "clips" / "style_block_v35.txt"

CHAR_SENIOR = (
    "a 21-year-old East Asian female university student with cool mature confidence, "
    "long straight black hair with subtle cyan-blue highlight strands framing her "
    "face and a single silver barrette clip on the right side, sharp narrow amber "
    "eyes with rainbow iris flecks, porcelain fair skin with subtle blush, slim tall "
    "figure, wearing a tailored navy blazer with white piping over a crisp white "
    "collared blouse, a thin black ribbon choker, a silver ring on her right index "
    "finger, dark pleated midi skirt and black leather loafers"
)

CHAR_JUNIOR = (
    "an 18-year-old East Asian female high school student, long chestnut-brown hair "
    "in low twin tails with bright orange ribbons, yellow-green eyes with rainbow "
    "iris flecks, soft round cheeks with brighter blush, slim petite figure with a "
    "curious and energetic youthful aura, wearing a cream sailor cardigan with a "
    "white collar and red ribbon tie, a dark pleated skirt, white knee-high socks, "
    "brown loafers, a small plush star pendant hanging on the orange ribbon"
)

MUSIC_SKELETON_V35 = (
    "non-diegetic music: 120 BPM beat grid, minimal background beat, low volume, "
    "no melody. Audio bed is intentionally sparse during generation; the final "
    "layered BGM track is mixed in post by an external ffmpeg mixer to keep every "
    "beat aligned to the 120 BPM grid and to avoid the H3 monolithic generated tone."
)

SOUNDSCAPE_V35 = (
    "Soft spring-pop ambient bed — gentle spring breeze through cherry petals, "
    "distant soft chimes ringing faintly, a very faint heartbeat-style bass pulse "
    "under the BGM. No voices, no spoken dialogue, no narration, no edge-tts, no "
    "vocalization of any kind throughout the entire video. The ambient bed remains "
    "continuous across every shot cut. NOTE: do not synthesize whoosh or any "
    "inter-shot SFX during generation; inter-shot audio treatment is handled "
    "externally by an ffmpeg mixer."
)

ENDING_FRAME_CUE_V35 = (
    "the camera pulls back to frame the ENTIRE head and shoulders, both eyes "
    "clearly visible, well-framed composition, no half-face cropping, no extreme "
    "close-up that leaves only one eye on screen"
)

STYLE_BLOCK_V35 = (
    "2D-animated, Mai Yoneyama anime cel-shading pop-art style. Medium close-up "
    "horizontal landscape frame (16:9), vibrant neon CMYK pop-art aesthetic, "
    "comic-book graphic overlay language. WELL-FRAMED COMPOSITION throughout: "
    "complete facial framing, both eyes visible, full head-and-shoulders in "
    "every ending frame, no half-face cropping, no extreme close-up that "
    "truncates the subject. Cel-shaded flat color blocks, hand-painted anime "
    "lineart, no photorealism no 3D render no CGI no airbrushed skin texture."
)

ACTION_TIMING_SUFFIX_V35 = (
    "Action timing constraint: this segment contains exactly ONE primary motion "
    "chain executed in sequence with explicit then-clauses. Each motion is fast, "
    "snappy, and completes in approximately 1.5 seconds before the next begins. "
    "No lingering, no slow motion, no held poses between motions. The total "
    "motion density is high but the time-on-screen per motion is short."
)

_TIME_TAG_RE = re.compile(r"\[(\d+(?:\.\d+)?)s?\s*[-–]\s*(\d+(?:\.\d+)?)s?\]")


def parse_time_window(token: str) -> tuple[float, float] | None:
    m = _TIME_TAG_RE.search(token)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def extract_speed_windows(timed_shot_list: list[str],
                          duration_sec: float) -> list[dict]:
    windows: list[dict] = []
    for line in timed_shot_list:
        win = parse_time_window(line)
        if win is None:
            continue
        start, end = win
        kind = "transition" if ("TRANSITION" in line.upper() and (end - start) <= 1.0) \
            else "motion" if (end - start) >= 0.5 else "hold"
        windows.append({"start": round(start, 3), "end": round(end, 3), "kind": kind})
    return windows


def rewrite_shot_timeline(shot: dict) -> tuple[str, list[dict]]:
    timeline = shot.get("timed_shot_list", []) or []
    if not timeline:
        return "", []

    motion_lines: list[str] = []
    for line in timeline:
        cleaned = _TIME_TAG_RE.sub("", line).strip(" —-")
        if not cleaned:
            continue
        motion_lines.append(cleaned)

    chain = "; then ".join(motion_lines)
    rewritten = (
        f"Single motion chain executed in sequence: {chain}. "
        f"Each motion is fast, snappy, and completes in approximately 1.5 "
        f"seconds before the next begins. No lingering between motions."
    )
    speed_windows = extract_speed_windows(timeline, shot.get("duration_sec", 10.0))
    return rewritten, speed_windows


def build_prompt(shot: dict) -> tuple[str, dict]:
    STYLE = STYLE_BLOCK_V35
    char_lines: list[str] = []
    if shot.get("include_senior"):
        char_lines.append(f"[CHARACTER_SENIOR] {CHAR_SENIOR}")
    if shot.get("include_junior"):
        char_lines.append(f"[CHARACTER_JUNIOR] {CHAR_JUNIOR}")
    if shot.get("include_senior") and shot.get("include_junior"):
        char_lines.append(
            "[CHARACTER_INTERACTION] The senior (left) and the junior (right) "
            "appear together in the same frame; keep them visually distinct via "
            "silhouette, hair, and outfit."
        )
    CHAR_PARAGRAPH = "\n".join(char_lines)

    motion_chain_text, speed_windows = rewrite_shot_timeline(shot)

    cam = shot.get("camera", {})
    cam_text = (
        f"[CAMERA] {cam.get('type', 'static')}, amplitude "
        f"{cam.get('amplitude', 'medium')}, speed {cam.get('speed', 'fast')}."
    )

    ending_cue = ENDING_FRAME_CUE_V35

    integrated = (
        f"{STYLE}\n\n"
        f"{CHAR_PARAGRAPH}\n\n"
        f"[SCENE] {shot.get('scene', '')}\n\n"
        f"[SHOT_TIMELINE] {motion_chain_text}\n\n"
        f"{cam_text}\n\n"
        f"{ACTION_TIMING_SUFFIX_V35}\n\n"
        f"[FRAME_END_CUE] {ending_cue}"
    )

    prompt = (
        f"integrated_multimodal_description:\n{integrated}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V35}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V35}"
    )

    meta = {
        "shot": shot.get("index"),
        "title": shot.get("title", ""),
        "duration_sec": shot.get("duration_sec"),
        "downbeat_start": shot.get("downbeat_start", 0.0),
        "include_senior": shot.get("include_senior", False),
        "include_junior": shot.get("include_junior", False),
        "camera": cam,
        "prompt_chars": len(prompt),
        "style_block_chars": len(STYLE),
        "char_block_chars": sum(len(c) for c in char_lines),
        "pipeline_version": "v3.5-line0",
        "style_strategy": (
            "v3.5: storyboard_v35 (no transition words) + "
            "P1_minimal_music_skeleton + P2_single_action_chain + "
            "P3_well_framed_composition"
        ),
        "lora_enabled": False,
        "p1_music_skeleton": MUSIC_SKELETON_V35,
        "p2_action_chain_text": motion_chain_text,
        "p2_speed_windows": speed_windows,
        "p2_action_timing_suffix": ACTION_TIMING_SUFFIX_V35,
        "p3_ending_frame_cue": ending_cue,
        "p3_style_block_suffix": "well-framed composition, complete facial framing",
        "bpm_target": 120.0,
        "beat_period_sec": 0.5,
    }
    return prompt, meta


def verify_no_transition_words(out_dir: Path) -> dict:
    """v35 验证：生成出的 prompt 文件里也无转场词残留。"""
    BANNED_RE = re.compile(
        r"(?i)(explode|burst|wipe|slash|split|color\s+\w*\s*trans|transition)"
    )
    report = {"n_prompts": 0, "n_clean": 0, "errors": []}
    for cp in sorted(out_dir.glob("shot*_prompt.txt")):
        report["n_prompts"] += 1
        text = cp.read_text(encoding="utf-8")
        hits = list(BANNED_RE.finditer(text))
        if hits:
            report["errors"].append({
                "file": cp.name,
                "n_hits": len(hits),
                "samples": [m.group(0) for m in hits[:5]],
            })
        else:
            report["n_clean"] += 1
    report["ok"] = (report["n_clean"] == report["n_prompts"])
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--style-block", default=str(DEFAULT_STYLE_BLOCK))
    ap.add_argument("--char-blocks", default=str(DEFAULT_CHAR_BLOCKS))
    args = ap.parse_args(argv)

    sb_path = Path(args.storyboard)
    out_dir = Path(args.out_dir)
    style_path = Path(args.style_block)
    char_path = Path(args.char_blocks)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not sb_path.exists():
        print(f"ERROR: storyboard 不存在 {sb_path}", file=sys.stderr)
        return 2

    storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
    shots = storyboard.get("shots", [])

    style_path.write_text(STYLE_BLOCK_V35, encoding="utf-8")
    char_blocks = {
        "CHAR_SENIOR": CHAR_SENIOR,
        "CHAR_JUNIOR": CHAR_JUNIOR,
        "version": "v3.5",
        "diff_required_zero": True,
    }
    char_path.write_text(json.dumps(char_blocks, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    n = len(shots)
    print(f"[v35-pack] {n} shots → {out_dir}", flush=True)

    for shot in shots:
        prompt, meta = build_prompt(shot)
        idx = shot.get("index", 0)
        prompt_path = out_dir / f"shot{idx:02d}_prompt.txt"
        meta_path = out_dir / f"shot{idx:02d}_meta.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[v35-pack] shot{idx:02d} prompt={len(prompt)} chars "
              f"speed_windows={len(meta['p2_speed_windows'])} → {prompt_path.name}",
              flush=True)

    verify = verify_no_transition_words(out_dir)
    verify["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    verify_path = out_dir / "style_strategy_v35.json"
    verify_path.write_text(json.dumps(verify, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"[v35-pack] verify → {verify_path} ok={verify['ok']} "
          f"n_clean={verify['n_clean']}/{verify['n_prompts']}",
          flush=True)
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
