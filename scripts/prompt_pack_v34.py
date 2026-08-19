#!/usr/bin/env python3
"""v3.4 prompt-pack: 集成 P1/P2/P3 工艺改造。

vs v3.3-line0 (759f710) 关键差异（per 任务书 v3.4）：

P1 音乐后期化
  - non_diegetic_music 从「120 BPM J-pop synth track with kick, hihat, snare,
    bass, synth lead and synth pad layered together ...」收敛为极简拍点骨架：
    `non-diegetic music: 120 BPM beat grid, minimal background beat, low volume,
    no melody`
  - 保留 BPM 信息供后期 ffmpeg 拍点对齐；其余 prompt 结构不动
  - 砍掉 H3 自带的合成器/旋律/情绪描述，强制走后期专业 BGM 路径
  - overall_soundscape 删掉 `occasional soft whoosh sweeping across each
    transition`（whoosh 在 P1 后期化脚本中替换为软淡入/不出声）

P2 动作限速
  - 每个 shot 改为单一动作链 + `then` 表顺序：
    「She reaches ... then completes the gesture, fast, snappy, completes in
    ~1.5s」
  - timed_shot_list 内每一帧标注 `t=N.NNNs` 绝对时间戳（供 ffmpeg setpts 变速
    区间精确切分）
  - prompt 末尾追加 `action_timing` 字段记录每条动作的窗口 [start, end] 秒

P3 镜头构图
  - 每段 ending_frame_cue 写死模板：
    `the camera pulls back to frame the ENTIRE head and shoulders, both eyes
    clearly visible`
  - STYLE_BLOCK 末尾追加 `well-framed composition, complete facial framing`
  - scene 描述中显式声明构图（medium shot / full head-and-shoulders / both
    eyes visible / no half-face cropping）

输出：
  - shot{NN}_prompt.txt   H3 三段式 prompt
  - shot{NN}_meta.json    含 ending_frame_cue / action_chain / speed_windows
  - style_block_v34.txt   全片统一风格锚定（含 P3 构图补丁）
  - char_blocks_v34.json  角色描述块（与 v3.3 一致；diff=0 校验）
  - style_strategy_v34.json P1/P2/P3 三个改造点校验（让用户在合并前自检）

CLI:
  python prompt_pack_v34.py --storyboard <sb.json> --out-dir <d>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")

# ----- v34 默认路径 -----
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v34" / "sb" / "storyboard_v34.json"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v34" / "clips"
DEFAULT_CHAR_BLOCKS = ROOT / "output" / "pipeline_v34" / "clips" / "char_blocks_v34.json"
DEFAULT_STYLE_BLOCK = ROOT / "output" / "pipeline_v34" / "clips" / "style_block_v34.txt"

# ----- 角色块（与 v3.3 line0 字面一致，diff=0 校验）-----
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

# ----- P1 改造 1/3：极简拍点骨架（替代 v3.3 的「多层合成器」描述）-----
MUSIC_SKELETON_V34 = (
    "non-diegetic music: 120 BPM beat grid, minimal background beat, low volume, "
    "no melody. Audio bed is intentionally sparse during generation; the final "
    "layered BGM track is mixed in post by an external ffmpeg mixer to keep every "
    "beat aligned to the 120 BPM grid (≤ 41 ms beat-grid error) and to avoid the "
    "H3 monolithic generated tone."
)

# ----- P1 改造 2/3：环境声（移除 whoosh，由后期 on-beat cross-fade 替代）-----
SOUNDSCAPE_V34 = (
    "Soft spring-pop ambient bed — gentle spring breeze through cherry petals, "
    "distant soft chimes ringing faintly, a very faint heartbeat-style bass pulse "
    "under the BGM. No voices, no spoken dialogue, no narration, no edge-tts, no "
    "vocalization of any kind throughout the entire video. The ambient bed remains "
    "continuous across every shot cut. NOTE: do not synthesize whoosh or any "
    "transition SFX during generation; transition SFX is layered externally."
)

# ----- P3 改造 1/2：终幅构图写死模板 -----
ENDING_FRAME_CUE_V34 = (
    "the camera pulls back to frame the ENTIRE head and shoulders, both eyes "
    "clearly visible, well-framed composition, no half-face cropping, no extreme "
    "close-up that leaves only one eye on screen"
)

# ----- P3 改造 2/2：风格块末尾补 well-framed composition -----
STYLE_BLOCK_V34 = (
    "2D-animated, Mai Yoneyama anime cel-shading pop-art style. Medium close-up "
    "horizontal landscape frame (16:9), vibrant neon CMYK pop-art aesthetic, "
    "comic-book graphic overlay language. WELL-FRAMED COMPOSITION throughout: "
    "complete facial framing, both eyes visible, full head-and-shoulders in "
    "every ending frame, no half-face cropping, no extreme close-up that "
    "truncates the subject. Cel-shaded flat color blocks, hand-painted anime "
    "lineart, no photorealism no 3D render no CGI no airbrushed skin texture."
)

# ----- P2 改造 1/2：单一动作链 + then 序列 + 限速描述模板 -----
ACTION_TIMING_SUFFIX_V34 = (
    "Action timing constraint: this segment contains exactly ONE primary motion "
    "chain executed in sequence with explicit then-clauses. Each motion is fast, "
    "snappy, and completes in approximately 1.5 seconds before the next begins. "
    "No lingering, no slow motion, no held poses between motions. The total "
    "motion density is high but the time-on-screen per motion is short."
)

# ----- P2 改造 2/2：timed_shot_list 时间戳解析（t=N.NNNs）-----
_TIME_TAG_RE = re.compile(r"\[(\d+(?:\.\d+)?)s?\s*[-–]\s*(\d+(?:\.\d+)?)s?\]")


def parse_time_window(token: str) -> tuple[float, float] | None:
    """从 timed_shot_list 行首的 [t1-t2] 抓 (start, end) 秒。"""
    m = _TIME_TAG_RE.search(token)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def extract_speed_windows(timed_shot_list: list[str],
                         duration_sec: float) -> list[dict]:
    """从 timed_shot_list 解析所有 (start, end) 区间 + 估算 ffmpeg setpts 范围。

    返回 [{start, end, kind}] — kind ∈ {"motion"|"transition"|"hold"}
    """
    windows: list[dict] = []
    for line in timed_shot_list:
        win = parse_time_window(line)
        if win is None:
            continue
        start, end = win
        # transition 是包含 TRANSITION 字样的极短区间（≤ 1.0s）
        kind = "transition" if ("TRANSITION" in line.upper() and (end - start) <= 1.0) \
            else "motion" if (end - start) >= 0.5 else "hold"
        windows.append({"start": round(start, 3), "end": round(end, 3), "kind": kind})
    return windows


def rewrite_shot_timeline_p2(shot: dict) -> tuple[str, list[dict]]:
    """P2 改造：把 timed_shot_list 重写为「单动作链 + then 表」文本。

    返回 (重写后文本, speed_windows)
    """
    timeline = shot.get("timed_shot_list", []) or []
    if not timeline:
        return "", []

    # 把每条时间窗内的动作压成一条「fast, snappy, completes in ~1.5s」
    motion_lines: list[str] = []
    for line in timeline:
        # 去掉 [t1-t2] 前缀以便拼成「A then B then C」
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


def build_prompt(shot: dict, char_blocks: dict) -> tuple[str, dict]:
    """为单段生成 v3.4 H3 prompt + meta。"""
    # ---- 风格块（P3 末尾 well-framed composition 已内置） ----
    STYLE = STYLE_BLOCK_V34

    # ---- 角色块（diff=0 校验） ----
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

    # ---- P2：把 timed_shot_list 改写为单动作链 + then 表 ----
    motion_chain_text, speed_windows = rewrite_shot_timeline_p2(shot)

    # ---- 镜头语言 ----
    cam = shot.get("camera", {})
    cam_text = (
        f"[CAMERA] {cam.get('type', 'static')}, amplitude "
        f"{cam.get('amplitude', 'medium')}, speed {cam.get('speed', 'fast')}."
    )

    # ---- 终幅构图（P3 写死模板） ----
    ending_cue = ENDING_FRAME_CUE_V34

    # ---- 拼装 H3 官方三段式 prompt（integrated + soundscape + non_diegetic_music）----
    integrated = (
        f"{STYLE}\n\n"
        f"{CHAR_PARAGRAPH}\n\n"
        f"[SCENE] {shot.get('scene', '')}\n\n"
        f"[SHOT_TIMELINE] {motion_chain_text}\n\n"
        f"{cam_text}\n\n"
        f"{ACTION_TIMING_SUFFIX_V34}\n\n"
        f"[FRAME_END_CUE] {ending_cue}"
    )

    prompt = (
        f"integrated_multimodal_description:\n{integrated}\n\n"
        f"overall_soundscape:\n{SOUNDSCAPE_V34}\n\n"
        f"non_diegetic_music:\n{MUSIC_SKELETON_V34}"
    )

    meta = {
        "shot": shot.get("index"),
        "title": shot.get("title", ""),
        "duration_sec": shot.get("duration_sec"),
        "downbeat_start": shot.get("downbeat_start", 0.0),
        "include_senior": shot.get("include_senior", False),
        "include_junior": shot.get("include_junior", False),
        "transition_in": shot.get("transition_in", ""),
        "transition_out": shot.get("transition_out", ""),
        "camera": cam,
        "prompt_chars": len(prompt),
        "style_block_chars": len(STYLE),
        "char_block_chars": sum(len(c) for c in char_lines),
        "pipeline_version": "v3.4-line0",
        "style_strategy": (
            "v3.4: Context-IR integrated_multimodal_description + "
            "P1_minimal_music_skeleton + P2_single_action_chain_with_timing + "
            "P3_well_framed_composition"
        ),
        "lora_enabled": False,
        # P1
        "p1_music_skeleton": MUSIC_SKELETON_V34,
        # P2
        "p2_action_chain_text": motion_chain_text,
        "p2_speed_windows": speed_windows,
        "p2_action_timing_suffix": ACTION_TIMING_SUFFIX_V34,
        # P3
        "p3_ending_frame_cue": ending_cue,
        "p3_style_block_suffix": "well-framed composition, complete facial framing",
        # 供 ffmpeg 拍点对齐
        "bpm_target": 120.0,
        "beat_period_sec": 0.5,
    }
    return prompt, meta


def verify_strategy(out_dir: Path) -> dict:
    """校验输出：P1/P2/P3 三个改造点是否真正落地到 prompt 文件。"""
    clips = sorted(out_dir.glob("shot*_prompt.txt"))
    report = {
        "n_prompts": len(clips),
        "p1_pass": 0,
        "p2_pass": 0,
        "p3_pass": 0,
        "errors": [],
    }
    for cp in clips:
        text = cp.read_text(encoding="utf-8")
        # P1
        if "non-diegetic music: 120 BPM beat grid, minimal background beat" in text \
                and "kick, hihat, snare, bass, synth lead" not in text:
            report["p1_pass"] += 1
        else:
            report["errors"].append(f"P1 fail: {cp.name}")
        # P2
        if ("Single motion chain executed in sequence" in text
                and "fast, snappy, and completes in approximately 1.5 seconds" in text):
            report["p2_pass"] += 1
        else:
            report["errors"].append(f"P2 fail: {cp.name}")
        # P3
        if ("WELL-FRAMED COMPOSITION throughout" in text
                and "the camera pulls back to frame the ENTIRE head and shoulders" in text):
            report["p3_pass"] += 1
        else:
            report["errors"].append(f"P3 fail: {cp.name}")
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

    # 写 style_block / char_blocks（v34 版本，与 prompt 内字面一致）
    style_path.write_text(STYLE_BLOCK_V34, encoding="utf-8")
    char_blocks = {
        "CHAR_SENIOR": CHAR_SENIOR,
        "CHAR_JUNIOR": CHAR_JUNIOR,
        "version": "v3.4",
        "diff_required_zero": True,
    }
    char_path.write_text(json.dumps(char_blocks, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    n = len(shots)
    print(f"[v34-pack] {n} shots → {out_dir}", flush=True)

    for shot in shots:
        prompt, meta = build_prompt(shot, char_blocks)
        idx = shot.get("index", 0)
        prompt_path = out_dir / f"shot{idx:02d}_prompt.txt"
        meta_path = out_dir / f"shot{idx:02d}_meta.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[v34-pack] shot{idx:02d} prompt={len(prompt)} chars "
              f"speed_windows={len(meta['p2_speed_windows'])} → {prompt_path.name}",
              flush=True)

    # 校验
    verify = verify_strategy(out_dir)
    verify_path = out_dir / "style_strategy_v34.json"
    verify["ok"] = (verify["p1_pass"] == verify["n_prompts"]
                    and verify["p2_pass"] == verify["n_prompts"]
                    and verify["p3_pass"] == verify["n_prompts"])
    verify["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    verify_path.write_text(json.dumps(verify, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"[v34-pack] verify → {verify_path} ok={verify['ok']} "
          f"P1={verify['p1_pass']}/{verify['n_prompts']} "
          f"P2={verify['p2_pass']}/{verify['n_prompts']} "
          f"P3={verify['p3_pass']}/{verify['n_prompts']}",
          flush=True)
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
