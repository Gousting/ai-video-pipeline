#!/usr/bin/env python3
"""P5-v5 成片终验 VLM：全片新海诚动画风统一 + 三修复落地 + 空间稳定性检查。

CLI:
  python p5_v5_final_check.py --video <final_v5_1080p.mp4> --out <final_style_review.json> [--frames 6]

抽 6 帧（每镜头 2 帧）审查：
  1) 全片统一新海诚动画电影风，无写实残留、无风格跳变；
  2) 招牌为纯色发光灯牌、无文字、不漂移、无乱码；
  3) 道具绿色五元纸币统一（不硬币变纸币、不绿变红）；
  4) 空间稳定性：锅/柜台/门/招牌等固定物体跨帧不漂移。
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

API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
API_KEY = "sk-RoSmCwFjehQiKaliD9TzgLrgXnoOiVlSoIvqOckrRpclpVVoo5L7r3AL1AcmM3ni"
MODEL = "qwen3.8-max"


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


def extract_frames(video: Path, n: int = 6) -> list[Image.Image]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(probe.stdout.strip() or 0.0)
    if dur <= 0:
        dur = 15.0
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
    "你是一名成片终验审查员。我会给你从同一支短片（雨夜便利店故事）中按时间顺序"
    "抽取的若干帧（覆盖三个镜头）。请审查：\n"
    "1) 全片是否统一为新海诚动画电影风（唯美细腻光影、清新通透色调、雨夜霓虹倒映、"
    "层次天空、雨丝光斑），有无写实摄影残留、有无风格跳变；\n"
    "2) 画面中的便利店招牌/灯牌是否为纯色发光霓虹灯牌（无文字、无乱码、稳定不漂移）；\n"
    "3) 道具连续性：手中零钱/放回柜台的纸币是否为【一张绿色五元纸币 + 几枚硬币】，"
    "是否出现硬币变纸币、是否出现纸币变红色；\n"
    "4) 空间稳定性：画面中固定物体（关东煮锅、柜台、玻璃门、招牌、收银机等）的位置"
    "跨镜头/跨帧是否稳定不漂移、不扭曲、不重新排布。\n"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"anime_style_unified": <true/false>, "style_comment": "<画风统一性说明>", '
    '"signboard_no_text": <true/false>, "signboard_comment": "<招牌说明>", '
    '"prop_consistent": <true/false>, "prop_comment": "<道具连续性说明>", '
    '"spatial_stability": <true/false>, "spatial_comment": "<空间稳定性说明>", '
    '"opinion": "<一句话结论>"}'
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=6)
    args = ap.parse_args(argv)

    video = Path(args.video)
    frames = extract_frames(video, args.frames)

    content = [{"type": "text", "text": REVIEW_PROMPT}]
    for fr in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(fr)}"}})

    raw = chat([{"role": "user", "content": content}])

    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        review = json.loads(cleaned)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        review = json.loads(raw[s:e + 1]) if s != -1 and e > s else {"parse_error": True, "raw": raw}

    review["raw"] = raw
    review["pass"] = (
        bool(review.get("anime_style_unified", False))
        and bool(review.get("signboard_no_text", False))
        and bool(review.get("prop_consistent", False))
        and bool(review.get("spatial_stability", False))
    )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
