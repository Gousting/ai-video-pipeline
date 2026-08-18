#!/usr/bin/env python3
"""v3.2 分镜表：6 段 × 10s MV 风格快切，节拍对齐。

设计要点：
- 6 段 × 10s = 60s 内容（落在任务"60-80s"区间下沿）
- 每段 240 帧 @ 24fps（H3 支持的非默认长度，H3 上限 ~15s）
- 段间在 downbeat 切换（0、12、22、32、42、52、62s 附近 = 拍点整数倍）
- 每段 prompt 含 1 个 in-prompt 转场（mid-segment 4-5s 时刻）
- 5 个段间过渡在 downbeat 用 0.2-0.4s xfade
- 内容：学姐 vs 学妹 双角色对比，套用 Mai Yoneyama MV 工艺
- 无独白，无字幕（仅 intro/outro 标题卡 + 角色标签）

CLI:
  python storyboard_v32.py
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
OUT_PATH = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard_v32.json"
BEATS_JSON = ROOT / "output" / "pipeline_v3" / "music" / "beats.json"

# 段时长（秒）—— 拍点整数倍（每拍 0.5s = 2 拍 = 1s 短语；每 4 拍 = 2s 小节）
# 6 × 10s = 60s 内容
# 段间在 downbeat (每 2s) 切换
# 段 1 起点 0s, 段 2 起点 10s, 段 3 起点 20s, 段 4 起点 30s, 段 5 起点 40s, 段 6 起点 50s
# 注意：5s = 10 beats = 5 bars，10s = 20 beats = 10 bars
SEG_DURATIONS = [10, 10, 10, 10, 10, 10]
N_SHOTS = len(SEG_DURATIONS)

# 6 段 MV 风格分镜（学姐 vs 学妹 双角色对比）
# 每段包含：
#   - 风格化的中英 prompt 描述（用于 H3）
#   - timed_shot_list: in-prompt 时间标注的镜头切换 + 转场词
#   - transition_type: 段间转场类型（color explosion / fabric wipe / etc.）
#   - include_senior / include_junior
#   - camera type / amplitude / speed
#   - downbeat_at: 本段起始的 downbeat 时间戳（拍点整数倍）

SHOTS = [
    {
        "index": 1,
        "title": "COLOR RIOT / 学姐开场",
        "duration_sec": 10,
        "downbeat_start": 0.0,
        "char_focus": "senior",
        "include_senior": True,
        "include_junior": False,
        "transition_in": "color_explosion",
        "transition_out": "ink_burst",
        "camera": {"type": "whip pan + push in", "amplitude": "medium", "speed": "fast"},
        "scene": "extreme close-up of senior's right hand holding a cherry blossom petal, silver ring catches neon magenta light, neon CMYK background explodes in radial sunburst lines and halftone dots. Pop-art sticker overlay of a star burst in upper-left corner.",
        "timed_shot_list": [
            "[0-2s] extreme close-up of senior's hand reaching toward camera from off-screen left, neon radial background bursts, comic panel grid overlay",
            "[2-4s] camera pushes in to extreme close-up of silver ring on index finger catching magenta+cyan light, hand slowly rotates to reveal cherry petal",
            "[4s] COLOR EXPLOSION TRANSITION — full-frame magenta/cyan/lime ink burst sweeps across from right edge",
            "[4-10s] medium close-up side profile of senior's face, rainbow-gradient iris visible, long black hair with cyan-blue highlight strands catches fluorescent green rim light, abstract radial line background pulses behind her"
        ],
        "ending_frame_cue": "senior's amber eye at frame edge, cyan-blue hair highlight dominant",
    },
    {
        "index": 2,
        "title": "FABRIC WIPE / 学妹登场",
        "duration_sec": 10,
        "downbeat_start": 10.0,
        "char_focus": "junior",
        "include_senior": False,
        "include_junior": True,
        "transition_in": "fabric_wipe",
        "transition_out": "diagonal_slash",
        "camera": {"type": "whip pan + dolly out", "amplitude": "medium", "speed": "fast"},
        "scene": "extreme close-up of junior's twin tails with bright orange ribbons fluttering, yellow-green eye with rainbow iris flecks visible between hair strands, cream sailor cardigan sleeve wipes past camera. Background: halftone dot pattern + ink splash sticker.",
        "timed_shot_list": [
            "[0-3s] extreme close-up of junior's twin tails with bright orange ribbons fluttering, fabric whip across foreground, halftone dots dominant",
            "[3-5s] camera dollies back to reveal yellow-green eye with rainbow iris flecks between hair strands, cheek with brighter blush, eye looks directly at camera",
            "[5s] FABRIC WIPE TRANSITION — cream sailor cardigan sleeve sweeps past camera from left",
            "[5-10s] medium close-up side profile of junior's face, chestnut twin tails bouncing, abstract checkerboard tiles rotate behind her, plush star pendant visible on orange ribbon at neck"
        ],
        "ending_frame_cue": "junior's yellow-green eye dominant, orange ribbon mid-frame",
    },
    {
        "index": 3,
        "title": "DIAGONAL SLASH / 学姐氛围",
        "duration_sec": 10,
        "downbeat_start": 20.0,
        "char_focus": "senior",
        "include_senior": True,
        "include_junior": False,
        "transition_in": "diagonal_slash",
        "transition_out": "comic_panel_split",
        "camera": {"type": "pan left + tilt down", "amplitude": "medium", "speed": "medium"},
        "scene": "senior leaning against a pop-art column reading a book, cobalt blue + lemon yellow background with diagonal slash lines, ink splash sticker in upper-right corner. Cel-shaded flat color blocks, no 3D render.",
        "timed_shot_list": [
            "[0-4s] medium shot of senior leaning against an abstract pop-art column, navy blazer with white piping sharp against cobalt blue flat background, silver ring on index finger holds the book edge, eyes reading",
            "[4-5s] DIAGONAL SLASH TRANSITION — lemon yellow slash wipes diagonally across frame from upper-left to lower-right",
            "[5-10s] medium close-up of senior turning head toward camera, cyan-blue hair highlight catches fluorescent green rim light, rainbow-gradient iris appears, faint smile on lips"
        ],
        "ending_frame_cue": "senior's face 3/4 view, cyan-blue hair highlight prominent",
    },
    {
        "index": 4,
        "title": "COMIC PANEL / 学妹活力",
        "duration_sec": 10,
        "downbeat_start": 30.0,
        "char_focus": "junior",
        "include_senior": False,
        "include_junior": True,
        "transition_in": "comic_panel_split",
        "transition_out": "whip_pan",
        "camera": {"type": "push in + tilt up", "amplitude": "small", "speed": "medium"},
        "scene": "junior crouched feeding a small orange cat in a stylized garden, electric magenta + lemon yellow halftone background, comic panel grid overlay in lower-right corner. Cel-shaded with bright flat colors.",
        "timed_shot_list": [
            "[0-4s] medium shot of junior crouched in a stylized flat-shaded garden, orange cat licks her palm, twin tails spill over her shoulders, yellow-green eyes smile down at cat",
            "[4-5s] COMIC PANEL SPLIT-SCREEN TRANSITION — frame splits into 3 vertical comic panels (left/center/right) showing junior from different angles, then collapses back to single",
            "[5-10s] push in to medium close-up of junior's smiling face, plush star pendant bobs on orange ribbon, halftone dots pulse behind her in lemon yellow"
        ],
        "ending_frame_cue": "junior's bright smile, orange ribbons mid-frame",
    },
    {
        "index": 5,
        "title": "WHIP PAN / 双人对比",
        "duration_sec": 10,
        "downbeat_start": 40.0,
        "char_focus": "both",
        "include_senior": True,
        "include_junior": True,
        "transition_in": "whip_pan",
        "transition_out": "halftone_flash",
        "camera": {"type": "whip pan + static", "amplitude": "large", "speed": "fast"},
        "scene": "senior and junior standing back-to-back in a flat-color pop-art frame, senior left in navy blazer, junior right in cream cardigan. Background split: left half magenta radial lines, right half cyan checkerboard.",
        "timed_shot_list": [
            "[0-4s] medium two-shot: senior left + junior right standing back-to-back, navy blazer vs cream cardigan, silver ring on senior's hand + orange ribbons on junior's tails visible at frame edges, neon CMYK background split into magenta+cyan zones",
            "[4-5s] WHIP PAN WITH MOTION STREAKS — camera whips left-to-right with horizontal cyan motion streak overlay",
            "[5-10s] medium shot both facing camera, senior with composed faint smile + junior with curious bright smile, fluorescent green+lemon yellow ink splash sticker behind them, halftone dots pulse"
        ],
        "ending_frame_cue": "both faces symmetric, neon CMYK pop-art background dominant",
    },
    {
        "index": 6,
        "title": "HALFTONE FLASH / 收束",
        "duration_sec": 10,
        "downbeat_start": 50.0,
        "char_focus": "both",
        "include_senior": True,
        "include_junior": True,
        "transition_in": "halftone_flash",
        "transition_out": "hard_cut",
        "camera": {"type": "pull back + tilt up", "amplitude": "medium", "speed": "slow"},
        "scene": "senior and junior walking away side by side under cherry tree, magenta+cyan+lemon yellow halftone overlay across whole frame, comic panel border around frame. Cel-shaded flat color blocking.",
        "timed_shot_list": [
            "[0-4s] wide back-view both walking away under stylized cherry tree, senior on left in navy blazer, junior on right in cream cardigan, neon CMYK cherry petals fall around them",
            "[4-5s] HALFTONE DOT OVERLAY FLASH — full-frame magenta halftone dots flash 0.5s",
            "[5-10s] extreme wide back-view both figures get smaller, abstract flat-color campus silhouettes rise behind them, fluorescent green+lemon yellow gradient sky, comic panel border frames the shot"
        ],
        "ending_frame_cue": "both silhouettes small in frame, neon CMYK gradient sky dominant",
    },
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--beats", default=str(BEATS_JSON))
    args = ap.parse_args(argv)

    # 加载 beats.json 验证对齐
    beats_path = Path(args.beats)
    if not beats_path.exists():
        print(f"ERROR: beats.json 不存在 {beats_path}", file=sys.stderr)
        return 2
    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    beats = beats_data["beats"]
    downbeats = beats_data["downbeats"]

    # 验证每段起点都在拍点上（0.5s 倍数）
    for shot in SHOTS:
        ds = shot["downbeat_start"]
        # 必须是 2 beats (1s) 整数倍且是 downbeat (2s 整数倍)
        if ds % 2.0 != 0:
            print(f"WARN: shot{shot['index']} 起点 {ds}s 不在 downbeat (2s 整数倍)", flush=True)
        if ds >= len(beats) * 0.5:
            print(f"WARN: shot{shot['index']} 起点 {ds}s 超出 BGM 时长 {len(beats)*0.5}s",
                  flush=True)

    # 计算段间过渡点
    cut_points = []
    for i in range(len(SHOTS) - 1):
        cut = SHOTS[i]["downbeat_start"] + SHOTS[i]["duration_sec"]
        cut_points.append({"from_shot": i + 1, "to_shot": i + 2,
                           "cut_at_sec": cut, "xfade_sec": 0.3,
                           "in_transition": SHOTS[i]["transition_out"],
                           "out_transition": SHOTS[i + 1]["transition_in"]})

    storyboard = {
        "title": "选学姐还是学妹？（MV 版 / v3.2）",
        "version": "v3.2",
        "pipeline": "ai-video-pipeline v3.2",
        "reference_video": "D:\\ai-video-pipeline\\input_h3_pv_ref.mp4",
        "reference_score": 68.5,
        "style_strategy": "plan_b_prompt_reinforcement + mai_yoneyama_cel",
        "lora_enabled": False,
        "target_resolution": "720x1280",
        "target_duration_sec": 60,
        "n_shots": N_SHOTS,
        "shots": SHOTS,
        "cut_points": cut_points,
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
            "source": "程序合成 (v3.2 改进版 — 6 层多轨 J-pop)",
            "bpm": 120.0,
            "duration_sec": 72.0,
            "file": "output/pipeline_v3/music/bgm_v32.wav",
            "beats_json": "output/pipeline_v3/music/beats.json",
        },
        "voiceover": "NONE — 全片零对白",
        "sound_effects": [
            "whoosh at each segment boundary (x6 + intro/outro)",
            "soft chimes on intro card",
            "bass pulse under BGM",
        ],
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
    print(f"[sb] 分镜表 -> {out_path}", flush=True)
    print(f"[sb] 段数: {N_SHOTS}, 总时长: {sum(SEG_DURATIONS)}s, 转场点: {len(cut_points)}",
          flush=True)
    print(f"[sb] BGM 时长: {len(beats)*0.5:.1f}s, 拍点数: {len(beats)}, downbeats: {len(downbeats)}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
