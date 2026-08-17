#!/usr/bin/env python3
"""P5 VLM 审查脚本：抽帧 + qwen3.8-max(opencode.ai/zen/go/v1) 审查视频段质量。

CLI:
  python p5_vlm_review.py --video <mp4> --ref <ref_half.png> --out <review.json> [--frames 3]

审查维度：动作连贯 / 变形崩坏 / 角色一致性（比对定妆参考图）。
返回 JSON 落盘，含 pass 字段（score>=70 且 action_coherent 且 char_consistent 判为通过）。
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
API_KEY = "sk-VxLhB9Fqnm6XBgd4l1kjloOGq2bJ9g9sKJ2Y0SJTdLwdt6Rtd0olISu02pkmNCZr"
MODEL = "qwen3.8-max"


def frame_to_b64(img: Image.Image, target_kb: int = 80) -> str:
    """把 PIL 图压到 ~target_kb JPEG 再转 base64。"""
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


def extract_frames(video: Path, n: int = 3) -> list[Image.Image]:
    """用 ffmpeg 抽 n 帧（均匀分布），返回 PIL 图列表。"""
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
    """调用 qwen3.8-max，带重试。"""
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
    "你是一名短视频画面质量审查员。我会依次给你 1 张角色定妆参考图（第一张），"
    "以及从同一段视频中抽出的若干帧（后续图片，按时间顺序）。请审查这段视频：\n"
    "1) 动作/运动是否连贯自然、无明显跳变；\n"
    "2) 人物或物体是否有明显变形、崩坏、肢体扭曲等 AI 硬伤；\n"
    "3) 主角外貌/服装/气质是否与定妆参考图保持一致（同一人）。\n"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"score": <0-100 整数>, "action_coherent": <true/false>, '
    '"deformation": "<变形/崩坏问题描述，无则填无>", '
    '"char_consistent": <true/false>, "char_comment": "<角色一致性说明>", '
    '"opinion": "<一句话结论>"}'
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=3)
    args = ap.parse_args(argv)

    video = Path(args.video)
    ref = Path(args.ref)

    frames = extract_frames(video, args.frames)
    ref_img = Image.open(ref)

    content = [{"type": "text", "text": REVIEW_PROMPT}]
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(ref_img)}"}})
    for fr in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(fr)}"}})

    raw = chat([{"role": "user", "content": content}])

    # 解析 JSON（容忍 markdown 包裹）
    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        review = json.loads(cleaned)
    except json.JSONDecodeError:
        # 兜底：从原始文本里抠第一个 { ... }
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                review = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                review = {"parse_error": True, "raw": raw}
        else:
            review = {"parse_error": True, "raw": raw}

    review["raw"] = raw
    score = int(review.get("score", 0) or 0)
    review["pass"] = (
        score >= 70
        and bool(review.get("action_coherent", False))
        and bool(review.get("char_consistent", False))
    )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
