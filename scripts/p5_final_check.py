#!/usr/bin/env python3
"""P5 终验：从 final.mp4 抽一帧，VLM 确认画面正常。"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(r"D:\ai-video-pipeline")
FINAL = ROOT / "output" / "out" / "final.mp4"
FRAME = ROOT / "output" / "tmp" / "final_frame.jpg"
OUTJSON = ROOT / "output" / "tmp" / "p5_final_frame_check.json"

API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
API_KEY = "sk-VxLhB9Fqnm6XBgd4l1kjloOGq2bJ9g9sKJ2Y0SJTdLwdt6Rtd0olISu02pkmNCZr"
MODEL = "qwen3.8-max"


def main() -> int:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "10.0", "-i", str(FINAL),
         "-frames:v", "1", "-q:v", "3", "-y", str(FRAME)],
        check=True,
    )
    img = Image.open(FRAME).convert("RGB")
    img.thumbnail((768, 768))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=78)
    b64 = base64.b64encode(buf.getvalue()).decode()

    prompt = (
        "这是最终成片的一帧画面。请判断画面是否正常（人物无明显崩坏、无严重变形、构图正常、可播放）。"
        '只返回 JSON：{"normal": true/false, "opinion": "一句话结论"}'
    )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]
    raw = None
    for i in range(4):
        try:
            r = requests.post(
                API_URL,
                json={"model": MODEL, "messages": [{"role": "user", "content": content}]},
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=120,
            )
            if r.status_code == 200:
                raw = r.json()["choices"][0]["message"]["content"]
                break
        except Exception as e:  # noqa: BLE001
            print("retry", i, str(e)[:150])
        time.sleep(4)

    if raw is None:
        print("VLM 终验调用失败")
        return 1

    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        res = json.loads(cleaned)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        res = json.loads(raw[s:e + 1]) if s != -1 and e > s else {"raw": raw}
    res["raw"] = raw
    OUTJSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
