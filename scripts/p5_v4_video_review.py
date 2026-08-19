#!/usr/bin/env python3
"""P5-v4 视频段 VLM 审查（qwen3.8-max）：动作/角色/道具连续性/招牌/风格 五维。

CLI:
  python p5_v4_video_review.py --video <mp4> --first <shotN_first.png> --out <review.json> [--frames 4]

审查维度（对应任务书 v4）：
  1) action_coherent   动作连贯、无明显跳变；
  2) char_consistent   主角与本镜头首帧关键帧一致（同一人，东方面孔/黑短发/细框眼镜）；
  3) prop_consistent   道具连续性：硬币与【绿色】五元纸币形态/颜色跨帧一致，
                       不硬币变纸币、不绿变红；
  4) signboard_no_text 招牌为纯色发光霓虹灯牌、无乱码无文字、稳定不漂移；
  5) anime_style       全段新海诚动画风、无写实残留、无风格跳变。
pass = score>=70 且 action_coherent 且 char_consistent 且 prop_consistent
       且 signboard_no_text 且 anime_style。
角色参考用本镜头首帧关键帧（非 P4 西方面孔定妆图）。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image

from vlm_config import API_KEY, API_URL, MODEL  # 统一 VLM 配置入口（Phase F）


def frame_to_b64(img: Image.Image, target_kb: int = 80) -> str:
    img = img.convert("RGB")
    img.thumbnail((768, 768))
    quality = 78
    for _ in range(6):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if len(buf.getvalue()) <= target_kb * 1024:
            break
        quality -= 12
    return base64.b64encode(buf.getvalue()).decode()


def extract_frames(video: Path, n: int = 4) -> list[Image.Image]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(probe.stdout.strip() or 0.0)
    if dur <= 0:
        dur = 5.0
    ts = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    for t in ts:
        out = video.with_suffix(".tmp.jpg")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", "-y", str(out)],
            capture_output=True, check=True,
        )
        frames.append(Image.open(out).convert("RGB"))
        out.unlink(missing_ok=True)
    return frames


def chat(messages: list, attempts: int = 4) -> str:
    last = None
    for i in range(attempts):
        try:
            r = requests.post(
                API_URL,
                json={"model": MODEL, "messages": messages},
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=180,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last = f"{r.status_code}: {r.text[:300]}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:300]
        time.sleep(4 + i * 3)
    raise RuntimeError(f"VLM 调用失败: {last}")


REVIEW_PROMPT = (
    "你是一名短视频画面质量审查员。我会先给你 1 张该镜头首帧关键帧（角色定妆参考），"
    "随后给你从同一段视频抽出的若干帧（按时间顺序）。请审查这段视频：\n"
    "1) 动作/运动是否连贯自然、无明显跳变；\n"
    "2) 主角外貌/服装/气质是否与首帧关键帧保持一致（同一人：东方面孔、黑短发、细框眼镜）；\n"
    "3) 道具连续性：若本镜头涉及零钱（数零钱/付钱/放回柜台），核心检查【绿色五元纸币不得变红/变紫、"
    "硬币不得变纸币、纸币不得变硬币】；数零钱镜头应有几枚硬币+一张绿色五元纸币，"
    "放回柜台镜头可仅有一张绿色五元纸币（硬币非必需）；"
    "若本镜头不涉及零钱道具（如撑伞走路、推门），则 prop_consistent 直接判 true；\n"
    "4) 画面中的招牌是否为纯色发光霓虹灯牌（无文字、无乱码、稳定不漂移）；\n"
    "5) 全段是否统一为新海诚动画电影风（唯美光影、清新通透色调、雨夜霓虹倒映、雨丝光斑），"
    "无写实摄影残留、无风格跳变。\n"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"score": <0-100 整数>, "action_coherent": <true/false>, '
    '"char_consistent": <true/false>, "char_comment": "<角色一致性说明>", '
    '"prop_consistent": <true/false>, "prop_comment": "<道具连续性说明>", '
    '"signboard_no_text": <true/false>, "signboard_comment": "<招牌文字/漂移说明>", '
    '"anime_style": <true/false>, "style_comment": "<画风说明>", '
    '"deformation": "<变形/崩坏问题描述，无则填无>", "opinion": "<一句话结论>"}'
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--first", required=True, help="本镜头首帧关键帧（角色参考）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=4)
    args = ap.parse_args(argv)

    video = Path(args.video)
    ref = Path(args.first)

    frames = extract_frames(video, args.frames)
    ref_img = Image.open(ref)

    content = [{"type": "text", "text": REVIEW_PROMPT}]
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(ref_img)}"}})
    for fr in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(fr)}"}})

    raw = chat([{"role": "user", "content": content}])

    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        review = json.loads(cleaned)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        review = json.loads(raw[start:end + 1]) if start != -1 and end > start else {"parse_error": True, "raw": raw}

    review["raw"] = raw
    score = int(review.get("score", 0) or 0)
    review["pass"] = (
        score >= 70
        and bool(review.get("action_coherent", False))
        and bool(review.get("char_consistent", False))
        and bool(review.get("prop_consistent", False))
        and bool(review.get("signboard_no_text", False))
        and bool(review.get("anime_style", False))
    )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
