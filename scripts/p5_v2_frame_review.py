#!/usr/bin/env python3
"""P5-v2 关键帧首尾一致性 VLM 审查（qwen3.8-max）。

CLI:
  python p5_v2_frame_review.py --first <first.png> --last <last.png> --out <review.json>

审查维度：同一角色 / 同一场景 / 同一构图，仅动作状态或机位距离不同。
score>=70 且 same_character 且 same_scene 判为一致（pass）。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
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
    img.thumbnail((900, 900))
    quality = 78
    for _ in range(6):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if len(buf.getvalue()) <= target_kb * 1024:
            break
        quality -= 12
    return base64.b64encode(buf.getvalue()).decode()


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
    "你是一名关键帧一致性审查员。我会给你同一镜头的两张关键帧："
    "第一张是首帧（动作开始态），第二张是尾帧（动作完成态）。\n"
    "请判断这两张图是否是【同一角色、同一场景、同一构图】，"
    "仅动作状态或机位距离不同（即尾帧是首帧动作的延续，而不是换人/换场景/换视角）。\n"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"score": <0-100 整数>, "same_character": <true/false>, "same_scene": <true/false>, '
    '"same_composition": <true/false>, "action_progression": "<动作是否连贯延续的说明>", '
    '"diff": "<不一致之处描述，无则填无>", "opinion": "<一句话结论>"}'
)


def parse_json(raw: str) -> dict:
    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(raw[s:e + 1])
            except json.JSONDecodeError:
                pass
        return {"parse_error": True, "raw": raw}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    first = Path(args.first)
    last = Path(args.last)

    content = [{"type": "text", "text": REVIEW_PROMPT}]
    for p in (first, last):
        img = Image.open(p)
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(img)}"}})

    raw = chat([{"role": "user", "content": content}])
    review = parse_json(raw)
    review["raw"] = raw
    score = int(review.get("score", 0) or 0)
    review["pass"] = (
        score >= 70
        and bool(review.get("same_character", False))
        and bool(review.get("same_scene", False))
    )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
