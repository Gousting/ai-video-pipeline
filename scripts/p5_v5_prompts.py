#!/usr/bin/env python3
"""P5-v5 提示词定义（修正首尾帧策略 + 运镜规范，根治空间错乱）。

与 v4 的核心差异（本次修正重点）：
  1. 尾帧不再用 Z-Image 生成（那是 v4 空间错乱的错误源）；尾帧 = 首帧 PIL 同源
     中心裁剪 78% 再 LANCZOS 放大回原尺寸（推近 1.28 倍），两张图 100% 同源同构图，
     锅/柜台/门位置完全一致，差异仅"机位距离"，落在 H3 FL2VA 官方允许范围内。
  2. 动作靠 prompt 时间线描述，不靠首尾帧差异；每镜头只一个主要动作。
  3. 运镜写全三要素 type + amplitude + speed，统一 "push in with small amplitude at
     slow speed"，禁止裸写"镜头推近"。

本脚本只产出 3 张首帧 prompt（尾帧由 p5_v5_tail.py 用 PIL 生成），以及视频 prompt。
三修复（保留不丢）：招牌无文字光招牌 / 绿色五元纸币统一 / shot3 钢琴收束。
角色人设对齐 frames_v2：东方面孔 / 黑短发带雨珠 / 细框眼镜 / 深灰半湿大衣 / 帆布斜挎包。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
TMP = ROOT / "output" / "tmp"

# 风格锚定（全片统一，任务书给定英文锚定短语）
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

# 角色（任务书人设：二十七八岁清瘦、深灰半湿大衣、黑短发带雨珠、细框眼镜、帆布斜挎包，东方面孔）
CHAR_CN = (
    "二十七八岁的年轻男人，东方面孔，身形清瘦，黑色短发微乱带着雨珠，"
    "戴细框眼镜，深灰色羊毛大衣半湿（内搭深色衬衫），深色帆布斜挎包"
)
CHAR_EN = (
    "a lean young man in his late twenties with an East Asian face, "
    "slightly messy black short hair wet with raindrops, thin-rimmed glasses, "
    "a half-wet dark grey wool overcoat over a dark shirt and a dark canvas messenger bag"
)

# 三修复（中文，用于关键帧 prompt）
FIX_SIGN_CN = "便利店招牌是纯色发光霓虹灯牌，无任何文字字符"
FIX_PROP_CN = "几枚硬币和一张绿色五元纸币"

# 三修复（英文，用于视频 prompt；绿色纸币 + 招牌无文字，任务书给定原文）
FIX_SIGN_EN = "signboard is a glowing neon panel with no text, stable and undistorted"
FIX_PROP_EN = "a green five-yuan banknote, never turns red, never becomes coins"

# 运镜规范：三要素写全（type + amplitude + speed），统一小幅慢速推近
CAM_PUSH = "push in with small amplitude at slow speed (type=push in, amplitude=small, speed=slow)"


def _frame(prompt_cn: str) -> str:
    return f"{prompt_cn}。{STYLE_EN}"


# 首帧 prompt（每镜头 1 张；尾帧不再生成，用 PIL 同源裁剪推近）
FRAME_PROMPTS: dict[str, str] = {
    "shot1": _frame(
        f"{STYLE_CN}。雨夜街道，便利店玻璃门前，{CHAR_CN}撑着黑伞站在门前准备收伞，"
        f"伞沿滴着水珠，积水路面倒映着便利店暖色发光霓虹灯牌（{FIX_SIGN_CN}），"
        f"层次丰富的天空（蓝紫到暖橙柔和渐变），细腻雨丝和光斑（bokeh），"
        f"电影感构图，主体居中偏左，玻璃门与灯牌位置固定清晰"
    ),
    "shot2": _frame(
        f"{STYLE_CN}。便利店室内关东煮柜台前，{CHAR_CN}低头数零钱，"
        f"掌心是{FIX_PROP_CN}，关东煮锅在画面右侧升腾白色热气，"
        f"锅与柜台位置固定，暖白日光灯照明，玻璃柜反射店内灯光，室内货架背景，"
        f"店内招牌均为纯色发光灯牌无文字，中景固定机位，主体居中偏左，电影感构图"
    ),
    "shot3": _frame(
        f"{STYLE_CN}。便利店室内，{CHAR_CN}站在柜台边，正对镜头，正脸清晰可见，"
        f"他的手准备把一张绿色五元纸币放回面前的木质柜台台面（柜台带收银机），"
        f"玻璃门在他身后作为背景（门外雨幕与街灯白光透过玻璃门可见），"
        f"暖白日光灯照明，店内招牌纯色发光灯牌无文字，中景，主体居中，电影感构图"
    ),
}


def _video_align(shot: int) -> str:
    # 任务书给定对齐指令（原样）
    return (
        f"How the reference pictures align with the target video — "
        f"Picture 1 (from Shot {shot}) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from Shot {shot}) aligns with the 5.00-second mark of the target video."
    )


VIDEO_PROMPTS: dict[str, str] = {
    "shot1": (
        f"{_video_align(1)}\n\n"
        f"integrated_multimodal_description: In {STYLE_EN}, {CHAR_EN} stands in front of "
        f"the convenience store glass door holding a black umbrella, preparing to close it; "
        f"he closes the umbrella and steps toward the glass door. The camera moves with a "
        f"{CAM_PUSH}, tightening gently from a wide street view toward the doorway. "
        f"Neon reflections shimmer on the wet rain-soaked ground, a layered gradient sky "
        f"(blue-purple to warm orange) fills the background, delicate rain streaks and bokeh, "
        f"fresh transparent color palette, beautiful detailed lighting, cinematic composition. "
        f"{FIX_SIGN_EN}. The storefront, glass door, neon sign and character keep stable "
        f"positions throughout and do not drift or warp. {FIX_PROP_EN}.\n\n"
        f"overall_soundscape: Steady rain pattering on the pavement and umbrella, a faint "
        f"convenience store doorbell chime, distant city hum.\n\n"
        f"non_diegetic_music: N/A"
    ),
    "shot2": (
        f"{_video_align(2)}\n\n"
        f"integrated_multimodal_description: In {STYLE_EN}, {CHAR_EN} stands at the oden "
        f"counter with his head bowed, counting the change in his palm — a few coins and one "
        f"green five-yuan banknote; he lowers his head to count the change, his fingers "
        f"pausing on one green five-yuan banknote. {FIX_PROP_EN}; the coins stay metal and "
        f"round and do not morph. The camera moves with a {CAM_PUSH}. Steam from the oden pot "
        f"keeps rising, warm white light, fresh transparent color palette, beautiful detailed "
        f"lighting, cinematic composition. {FIX_SIGN_EN}. The oden pot, counter and background "
        f"stay in fixed positions and do not drift or warp.\n\n"
        f"overall_soundscape: Soft bubbling of the oden pot, faint clink of coins, muted rain "
        f"through the glass door, quiet interior hum.\n\n"
        f"non_diegetic_music: N/A"
    ),
    "shot3": (
        f"{_video_align(3)}\n\n"
        f"integrated_multimodal_description: In {STYLE_EN}, {CHAR_EN} stands at the counter "
        f"facing the camera; he gently places the green five-yuan banknote back on the counter, "
        f"his fingertips resting on the note. {FIX_PROP_EN}. The glass door stands behind him "
        f"with rain and white street light glowing through it. The camera moves with a "
        f"{CAM_PUSH}. Neon reflections on the wet rain-soaked ground, layered gradient sky, "
        f"delicate rain streaks and bokeh, fresh transparent color palette, beautiful detailed "
        f"lighting, cinematic composition. {FIX_SIGN_EN}. The counter, cash register and "
        f"background stay in fixed positions and do not drift or warp.\n\n"
        f"overall_soundscape: Rain pattering outside the glass door, a faint convenience store "
        f"doorbell chime, quiet interior hum.\n\n"
        f"non_diegetic_music: A soft piano phrase fading out gently as the shot ends."
    ),
}


def write_prompts() -> dict[str, Path]:
    """把首帧/视频 prompt 全部落盘到 output/tmp/，返回 {key: path}。"""
    TMP.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for shot in (1, 2, 3):
        p = TMP / f"p5v5_frame_prompt_shot{shot}_first.txt"
        p.write_text(FRAME_PROMPTS[f"shot{shot}"], encoding="utf-8")
        written[f"frame_shot{shot}"] = p
    for shot in (1, 2, 3):
        p = TMP / f"p5v5_prompt_shot{shot}.txt"
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
