#!/usr/bin/env python3
"""P5-v5 首帧单帧 VLM 审查（qwen3.8-max）：画风 / 角色人设 / 招牌 / 道具四维。

CLI:
  python p5_v5_frame_review.py --first <shotN_first.png> --out <review.json>

审查维度：
  1) 新海诚动画电影风（无写实/3D 残留）；
  2) 主角符合人设（东方面孔/黑短发带雨珠/细框眼镜/深灰半湿大衣/帆布斜挎包）；
  3) 招牌纯色发光霓虹灯牌、无文字无乱码；
  4) 涉及零钱镜头：几枚硬币 + 一张绿色五元纸币（无红色纸币）。
pass = score>=70 且 anime_style 且 char_consistent 且 signboard_no_text 且 prop_consistent。
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


def frame_to_b64(img: Image.Image, target_kb: int = 90) -> str:
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
    "你是一名关键帧审查员。我会给你 1 张单张关键帧（雨夜便利店故事某个镜头的首帧）。"
    "请审查：\n"
    "1) 是否为【新海诚动画电影风】（唯美细腻光影、清新通透色调、雨夜霓虹倒映、层次天空、"
    "雨丝光斑），而非写实摄影风、3D 渲染风或其它画风；\n"
    "2) 主角是否符合人设：二十七八岁清瘦【东方面孔】男人、黑色短发带雨珠、细框眼镜、"
    "深灰半湿大衣、深色帆布斜挎包；\n"
    "3) 画面中便利店招牌/灯牌是否为纯色发光霓虹灯牌（无文字、无乱码）；\n"
    "4) 若镜头涉及零钱（数零钱/放回柜台），是否出现【几枚硬币 + 一张绿色五元纸币】，"
    "纸币为绿色、无红色纸币、无硬币纸币互变；若镜头不涉及零钱（如撑伞/推门），"
    "prop_consistent 直接判 true。\n"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"score": <0-100 整数>, "anime_style": <true/false>, "style_comment": "<画风说明>", '
    '"char_consistent": <true/false>, "char_comment": "<角色一致性说明>", '
    '"signboard_no_text": <true/false>, "signboard_comment": "<招牌说明>", '
    '"prop_consistent": <true/false>, "prop_comment": "<道具连续性说明>", '
    '"opinion": "<一句话结论>"}'
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
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    first = Path(args.first)
    img = Image.open(first)

    content = [{"type": "text", "text": REVIEW_PROMPT}]
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(img)}"}})

    raw = chat([{"role": "user", "content": content}])
    review = parse_json(raw)
    review["raw"] = raw
    score = int(review.get("score", 0) or 0)
    review["pass"] = (
        score >= 70
        and bool(review.get("anime_style", False))
        and bool(review.get("char_consistent", False))
        and bool(review.get("signboard_no_text", False))
        and bool(review.get("prop_consistent", False))
    )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
