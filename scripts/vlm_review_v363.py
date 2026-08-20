#!/usr/bin/env python3
"""v3.6.3 VLM 视觉评审（per 任务书 v363 Step 4）。

抽取 8 帧关键帧（6 段 shot 中心 + 2 个 dissolve 前后），发 minimax-m3 (VLM)
HTTP API 评审：
  - 画面质量
  - 角色一致性
  - 转场是否净面-爆炸（v3.3 三大问题之一）
  - 转场与场景相关性
  - 切点落拍
  - 节奏感
  - 制作完成度

CLI:
  python vlm_review_v363.py
  python vlm_review_v363.py --video output/pipeline_v36/final_v36_60s_v363.mp4
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

ROOT = Path(r"D:\ai-video-pipeline")
sys.path.insert(0, str(ROOT / "scripts"))

from vlm_config import VLM_API_KEY, VLM_API_URL, VLM_MODEL

DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v363.mp4"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "qa_frames_v363"
DEFAULT_OUT_JSON = ROOT / "output" / "pipeline_v36" / "v363_vlm_review.json"

# 抽帧时间表（覆盖 6 段 shot 中部 + 2 个 dissolve 边界前后）
# shot 时长：shot1=10.125 [0-10.125], shot2=8.75 [10.125-18.875], shot3=8.75 [18.875-27.625]
# shot4=8.75 [27.625-36.375], shot5=8.75 [36.375-45.125(经过dissolve)]...
# 实际累计：dissolve 1 起点 36.015s, dissolve 2 起点 44.290s
# 抽帧点（中段或转场前后）：
FRAME_TIMES = {
    "shot01_mid":   4.5,    # shot1 中段（slow_open）
    "shot02_mid":  14.5,    # shot2 中段（fast_middle）
    "shot03_mid":  23.5,    # shot3 中段
    "shot04_mid":  32.5,    # shot4 中段
    "dissolve1_pre": 35.7,  # dissolve 1 前（shot4 末尾）
    "dissolve1_post": 36.5, # dissolve 1 后（shot5 开头）
    "dissolve2_pre": 43.9,  # dissolve 2 前（shot5 末尾）
    "dissolve2_post": 44.7, # dissolve 2 后（shot6 开头）
    "shot06_mid":  49.5,    # shot6 中段
}


def frame_to_b64(img: Image.Image, target_kb: int = 100) -> str:
    img = img.convert("RGB")
    img.thumbnail((768, 768))
    quality = 80
    for _ in range(6):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if len(buf.getvalue()) <= target_kb * 1024:
            break
        quality -= 12
    return base64.b64encode(buf.getvalue()).decode()


def extract_frame(video: Path, t_sec: float, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "3",
        "-y", str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"frame extract failed at {t_sec}s: {r.stderr}")
    return out_path


def chat_vlm(messages: list, attempts: int = 4) -> str:
    last = None
    for i in range(attempts):
        try:
            r = requests.post(
                VLM_API_URL,
                json={"model": VLM_MODEL, "messages": messages},
                headers={"Authorization": f"Bearer {VLM_API_KEY}"},
                timeout=180,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last = f"{r.status_code}: {r.text[:300]}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:300]
        time.sleep(4 + i * 3)
    raise RuntimeError(f"VLM call failed: {last}")


REVIEW_PROMPT = """你是一名短视频画面质量审查员。

我会给你一张短视频成片的关键帧序列（共 8 张，按时间顺序）：
1. shot01_mid: 第一段中部（slow_open，开场定调）
2. shot02_mid: 第二段中部（fast_middle，节奏紧张）
3. shot03_mid: 第三段中部（fast_middle）
4. shot04_mid: 第四段中部（fast_middle）
5. dissolve1_pre: 第四段末尾（dissolve 转场前最后一帧）
6. dissolve1_post: 第五段开头（dissolve 转场后第一帧）
7. dissolve2_pre: 第五段末尾（dissolve 转场前最后一帧）
8. dissolve2_post: 第六段开头（dissolve 转场后第一帧）
9. shot06_mid: 第六段中部（slow_tail，收束）

请基于这些帧做 7 维度评审（0-100 分），并说明理由：
1. **画面质量**：清晰度、构图、光影、色彩。无明显瑕疵扣分。
2. **角色一致性**：同一人物在不同帧中是否保持同一身份（发型/服装/气质）。
3. **转场是否净面-爆炸**：dissolve 转场（5→6, 7→8）前后画面是否干净无空白帧、无突兀爆炸特效。
4. **转场与场景相关性**：5→6, 7→8 dissolve 是否符合"内容连续性"语义（不突兀）。
5. **切点落拍**：相邻帧（1→2, 2→3, 3→4）之间是否节奏自然。
6. **节奏感**：整体三段式（slow 开头 → fast 中段 → slow 收尾）感觉。
7. **制作完成度**：整体是否像可发布成片。

只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：
{
  "画面质量": <0-100>,
  "角色一致性": <0-100>,
  "转场是否净面-爆炸": <0-100>,
  "转场与场景相关性": <0-100>,
  "切点落拍": <0-100>,
  "节奏感": <0-100>,
  "制作完成度": <0-100>,
  "overall_score": <0-100, 七项均分>,
  "issues": ["<issue1>", "<issue2>", ...],
  "highlights": ["<highlight1>", ...],
  "opinion": "<一句话结论>"
}
"""


def parse_json_response(raw: str) -> dict:
    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return {"parse_error": True, "raw": raw}
        return {"parse_error": True, "raw": raw}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    args = ap.parse_args(argv)

    video = Path(args.video)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)

    if not video.exists():
        print(f"ERROR: video not found {video}", file=sys.stderr)
        return 2

    # 抽帧
    frames = []
    for name, t in FRAME_TIMES.items():
        out_p = out_dir / f"{name}.jpg"
        try:
            extract_frame(video, t, out_p)
            img = Image.open(out_p).convert("RGB")
            frames.append({"name": name, "t_sec": t, "img": img, "path": str(out_p)})
            print(f"  extracted {name} @ {t}s → {out_p.name}")
        except RuntimeError as e:
            print(f"  FAILED {name}: {e}", file=sys.stderr)
            frames.append({"name": name, "t_sec": t, "img": None,
                           "path": None, "error": str(e)})

    # 构造 VLM 消息
    content = [{"type": "text", "text": REVIEW_PROMPT}]
    valid_count = 0
    for fr in frames:
        if fr.get("img") is None:
            continue
        valid_count += 1
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(fr['img'])}"}
        })
    if valid_count == 0:
        print("ERROR: no frames extracted", file=sys.stderr)
        return 3

    print(f"\n[vlm-v363] sending {valid_count} frames to VLM "
          f"({VLM_MODEL} @ {VLM_API_URL[:50]}...)", flush=True)

    try:
        raw = chat_vlm([{"role": "user", "content": content}])
        review = parse_json_response(raw)
    except RuntimeError as e:
        print(f"ERROR: VLM call failed: {e}", file=sys.stderr)
        review = {"vlm_error": True, "raw_error": str(e)}
    except Exception as e:
        print(f"ERROR: unexpected VLM error: {e}", file=sys.stderr)
        review = {"vlm_error": True, "raw_error": str(e)}

    review["raw"] = raw if 'raw' in locals() else None
    review["video"] = str(video)
    review["frames_sent"] = valid_count
    review["frame_times"] = FRAME_TIMES
    review["vlm_model"] = VLM_MODEL
    review["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"[vlm-v363] review → {out_json}", flush=True)
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
