#!/usr/bin/env python3
"""P5-v4 提示词定义：新海诚动画风（换风重跑，保留三修复）。

- 关键帧 prompt：Z-Image 中文（Qwen-Image 原生中文），末尾追加英文风格锚定短语。
- 视频 prompt：H3 英文 FL2VA 三段式，内嵌英文风格锚定短语 + 三修复约束。
- 本脚本是 prompt 的唯一事实源（写入 git 可追溯），运行后把 prompt 落盘到
  output/tmp/（供 p5_v2_frames.py / p5_video_gen.py 以 --prompt-file 消费）。

三修复（换风不丢）：
  1. 招牌无文字：纯色发光霓虹灯牌，不写任何字符，避免文字漂移。
  2. 道具统一：手中找零/放回柜台 = 一张【绿色】五元纸币 + 几枚硬币，首尾一致，
     不出现红色纸币、不出现硬币变纸币。
  3. 钢琴收束：shot3 结尾钢琴渐弱收束（音频层，见 p5_v4_audio.py）。

角色人设对齐 frames_v2 现有关键帧（东方面孔、黑短发、细框眼镜），
非 P4 的西方面孔定妆图。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
TMP = ROOT / "output" / "tmp"

# 风格锚定（全片统一）
STYLE_CN = (
    "新海诚动画电影风格，唯美细腻光影，清新通透色调，"
    "雨夜霓虹灯在积水地面倒映，层次丰富的天空，细腻雨丝和光斑（bokeh），"
    "电影感构图，柔和渐变的天空色（蓝紫到暖橙）"
)
STYLE_EN = (
    "Makoto Shinkai anime film style, beautiful detailed lighting, "
    "fresh transparent color palette, neon reflections on wet rain-soaked ground, "
    "layered gradient sky, delicate rain streaks and bokeh, cinematic composition"
)

# 角色（对齐 frames_v2：东方面孔 / 黑短发 / 细框眼镜）
CHAR_CN = (
    "二十七八岁的年轻男人，东方面孔，身形清瘦，黑色短发微乱带着雨珠，"
    "戴细框眼镜，深灰色羊毛大衣半湿，深色帆布斜挎包"
)
CHAR_EN = (
    "a lean young man in his late twenties with an East Asian face, "
    "slightly messy black short hair wet with raindrops, thin-rimmed glasses, "
    "a half-wet dark grey wool overcoat and a dark canvas messenger bag"
)

# 三修复（中文，用于关键帧 prompt）
FIX_SIGN_CN = "便利店招牌是纯色发光霓虹灯牌，无任何文字字符"
FIX_PROP_CN = "几枚硬币和一张绿色五元纸币"

# 三修复（英文，用于视频 prompt）
FIX_SIGN_EN = "the signboard is a glowing neon light panel with no text, stable and undistorted"
FIX_PROP_EN = "a green five-yuan banknote and several coins"


def _frame(prompt_cn: str) -> str:
    return f"{prompt_cn}。{STYLE_EN}"


FRAME_PROMPTS: dict[str, dict[str, str]] = {
    "shot1": {
        "first": _frame(
            f"{STYLE_CN}。雨夜街道，{CHAR_CN}撑着黑伞走向便利店玻璃门，"
            f"伞沿滴着水珠，积水路面倒映着便利店暖色霓虹灯牌（{FIX_SIGN_CN}），"
            f"层次丰富的天空（蓝紫到暖橙柔和渐变），细腻雨丝和光斑（bokeh），"
            f"电影感构图，主体居左，右侧便利店暖色灯光形成引导线"
        ),
        "last": _frame(
            f"{STYLE_CN}。雨夜便利店门口，{CHAR_CN}站在玻璃门前，"
            f"一手收拢黑伞，一手伸向门把手准备推门，"
            f"暖白灯光透过玻璃门洒在他半湿的大衣上，"
            f"积水倒映着霓虹发光灯牌（{FIX_SIGN_CN}），"
            f"层次天空（蓝紫到暖橙渐变），雨丝光斑，电影感构图，主体近门居左"
        ),
    },
    "shot2": {
        "first": _frame(
            f"{STYLE_CN}。便利店内关东煮柜台前，{CHAR_CN}低头数零钱，"
            f"掌心是{FIX_PROP_CN}，关东煮锅升腾白色热气，暖白灯光，"
            f"玻璃柜反射，店内招牌均为纯色发光灯牌无文字，"
            f"电影感构图，主体居中偏左"
        ),
        "last": _frame(
            f"{STYLE_CN}。便利店内关东煮柜台前，{CHAR_CN}抬头微顿，"
            f"指尖捏着{FIX_PROP_CN}，关东煮锅热气升腾，暖白灯光，"
            f"玻璃柜反射，店内招牌均为纯色发光灯牌无文字，电影感构图"
        ),
    },
    "shot3": {
        "first": _frame(
            f"{STYLE_CN}。便利店柜台边，{CHAR_CN}的手把一张绿色五元纸币"
            f"轻轻放回柜台台面，暖白灯光，柜台和收银机可见，"
            f"店内招牌均为纯色发光灯牌无文字，电影感构图，主体居中"
        ),
        "last": _frame(
            f"{STYLE_CN}。便利店内近门口，{CHAR_CN}刚把绿色五元纸币放回柜台后转身，"
            f"手持黑伞走向玻璃门，门外雨幕与街灯白光涌入，"
            f"层次天空（蓝紫到暖橙渐变），积水倒映霓虹发光灯牌（{FIX_SIGN_CN}），"
            f"雨丝光斑，电影感构图，门框形成框式构图"
        ),
    },
}


def _video_align(shot: int) -> str:
    return (
        f"How the reference pictures align with the target video — "
        f"Picture 1 (from Shot {shot}) aligns with the 0.00-second mark; "
        f"Picture 2 (from Shot {shot}) aligns with the 5.00-second mark."
    )


VIDEO_PROMPTS: dict[str, str] = {
    "shot1": (
        f"{_video_align(1)}\n\n"
        f"integrated_multimodal_description: In {STYLE_EN}, {CHAR_EN} walks steadily "
        f"toward the convenience store glass door while holding a black umbrella, "
        f"the camera slowly tracking forward with a gentle push-in "
        f"(type=dolly-in, amplitude=small, speed=slow); in the final moment he stops "
        f"at the door, lowers the umbrella and reaches out to push the door open. "
        f"Neon reflections shimmer on the wet rain-soaked ground, a layered gradient "
        f"sky (blue-purple to warm orange) fills the background, delicate rain streaks "
        f"and bokeh throughout, fresh transparent color palette, beautiful detailed "
        f"lighting, cinematic composition. {FIX_SIGN_EN}. The framing gradually "
        f"tightens from a wide street view to a mid shot at the doorway.\n\n"
        f"overall_soundscape: Steady rain pattering on the pavement and umbrella, "
        f"faint distant city hum, the convenience store doorbell chime as the door "
        f"begins to open.\n\n"
        f"non_diegetic_music: N/A"
    ),
    "shot2": (
        f"{_video_align(2)}\n\n"
        f"integrated_multimodal_description: In {STYLE_EN}, {CHAR_EN} stands at the "
        f"oden counter with his head bowed, counting coins in his palm; his fingers "
        f"hold {FIX_PROP_EN}, then he slowly raises his head and stills, fingertips "
        f"pinching the green five-yuan banknote. The banknote stays green paper and "
        f"the coins stay metal and round — no color change and no coin-to-banknote "
        f"morphing across the shot. The camera stays at a fixed medium shot with only "
        f"a very slight slow drift-in (type=static, amplitude=minimal, speed=none). "
        f"Steam from the oden pot keeps rising in the foreground, warm white light, "
        f"fresh transparent color palette, beautiful detailed lighting, cinematic "
        f"composition. {FIX_SIGN_EN}.\n\n"
        f"overall_soundscape: Soft bubbling of the oden pot, faint clink of coins, "
        f"muted rain through the glass door, quiet interior hum.\n\n"
        f"non_diegetic_music: N/A"
    ),
    "shot3": (
        f"{_video_align(3)}\n\n"
        f"integrated_multimodal_description: In {STYLE_EN}, {CHAR_EN}'s hand gently "
        f"places a green five-yuan banknote back on the counter, then he turns and "
        f"pushes open the glass door, opening a black umbrella and stepping out into "
        f"the rain; the camera follows him toward the door and holds on his back "
        f"through the glass as he walks away into the rain (type=follow, amplitude=small, "
        f"speed=slow). The banknote remains a green five-yuan paper note — it never "
        f"turns red and never becomes coins. {FIX_SIGN_EN}. Neon reflections on the "
        f"wet rain-soaked ground, layered gradient sky, delicate rain streaks and "
        f"bokeh, fresh transparent color palette, beautiful detailed lighting, "
        f"cinematic composition. The framing moves from an interior mid shot to a "
        f"doorway frame-within-frame as he departs.\n\n"
        f"overall_soundscape: The convenience store doorbell chime, rain growing "
        f"louder as the door opens, receding footsteps on the wet pavement.\n\n"
        f"non_diegetic_music: A soft piano phrase fading out gently as the shot ends."
    ),
}


def write_prompts() -> dict[str, Path]:
    """把关键帧/视频 prompt 全部落盘到 output/tmp/，返回 {key: path}。"""
    TMP.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for shot in (1, 2, 3):
        for tag in ("first", "last"):
            p = TMP / f"p5v4_frame_prompt_shot{shot}_{tag}.txt"
            p.write_text(FRAME_PROMPTS[f"shot{shot}"][tag], encoding="utf-8")
            written[f"frame_shot{shot}_{tag}"] = p
    for shot in (1, 2, 3):
        p = TMP / f"p5v4_prompt_shot{shot}.txt"
        p.write_text(VIDEO_PROMPTS[f"shot{shot}"], encoding="utf-8")
        written[f"video_shot{shot}"] = p
    return written


def main() -> int:
    written = write_prompts()
    for k, p in written.items():
        print(f"[prompts] {k} -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
