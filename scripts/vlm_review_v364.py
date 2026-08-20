#!/usr/bin/env python3
"""v3.6.4 VLM 视觉评审（per 任务书 v364 Step 5）。

vs vlm_review_v363.py 关键差异：

- 抽帧时间改为 v364 节奏：10/8/6/10/6/8s 各段中段 + dissolve 前后 + shot6 收束
- **评审 prompt 重写**：强调任务书 §3.3 强制指令核验项
  - 开场是否直接上角色（shot01 第一帧 = 角色清晰正脸）
  - 是否存在白淡入
  - 转场是否净面（无空白帧/爆炸特效）
  - 角色一致性
  - 节奏是否快慢呼吸（slow_open→fast→peak→fast→tail）
- 沿用 v363 7 维度评分 + JSON 输出

CLI:
  python vlm_review_v364.py
  python vlm_review_v364.py --video output/pipeline_v36/final_v36_60s_v364.mp4
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

DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v364.mp4"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "qa_frames_v364"
DEFAULT_OUT_JSON = ROOT / "output" / "pipeline_v36" / "v364_vlm_review.json"

# 抽帧时间表（覆盖 v364 节奏：10+8+6+10+6+8 = 48s，trim 0.5×6=3s → 45s）
# 各段起点（trim 后）：shot1=0, shot2=9.125, shot3=16.125, shot4=21.708,
#                     shot5=30.833, shot6=36.417
# 段中段 + dissolve 前后帧：
FRAME_TIMES = {
    "shot01_open":     1.0,   # 开场第一秒（关键！验证是否直接上角色）
    "shot01_mid":      5.0,   # shot1 中段（slow_open, 定调）
    "shot02_mid":     13.0,   # shot2 中段（build, 学妹登场）
    "shot03_mid":     19.0,   # shot3 中段（fast, 双人同框）
    "shot04_peak":    26.0,   # shot4 中段（peak, 情绪锚/高潮）
    "dissolve1_pre":  31.5,   # dissolve 1 前（shot4 末尾）
    "dissolve1_post": 32.5,   # dissolve 1 后（shot5 开头）
    "dissolve2_pre":  37.0,   # dissolve 2 前（shot5 末尾）
    "dissolve2_post": 37.8,   # dissolve 2 后（shot6 开头）
    "shot06_tail":    42.0,   # shot6 中段（tail, 收束）
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

我会给你一张短视频成片的关键帧序列（共 10 张，按时间顺序）：
1. shot01_open: 开场第一秒（**关键**：验证是否直接出现清晰的角色正脸，禁止抽象/手部/光斑开场）
2. shot01_mid: 第一段中部（slow_open，定调）
3. shot02_mid: 第二段中部（build，学妹登场）
4. shot03_mid: 第三段中部（fast，双人同框）
5. shot04_peak: 第四段中部（peak，情绪锚/高潮长段）
6. dissolve1_pre: 第四段末尾（dissolve 转场前最后一帧）
7. dissolve1_post: 第五段开头（dissolve 转场后第一帧，**关键**：验证非白帧）
8. dissolve2_pre: 第五段末尾（dissolve 转场前最后一帧）
9. dissolve2_post: 第六段开头（dissolve 转场后第一帧，**关键**：验证非白帧）
10. shot06_tail: 第六段中部（tail，收束）

请基于这些帧做 7 维度评审（0-100 分），并说明理由：

1. **画面质量**：清晰度、构图、光影、色彩。无明显瑕疵扣分；如有抽象/畸变/像素化扣重分。
2. **角色一致性**：同一人物在不同帧中是否保持同一身份（发型/服装/气质）。两位角色
   （棕发双马尾+橙发带学姐 vs 黑长直+choker学妹）跨段是否可识别为同一组。
3. **开场是否直接上角色**（v364 重点）：shot01_open 第一帧必须直接是清晰的角色正脸，
   不可有抽象背景/手部特写/光斑/无内容的开场铺垫。违规扣 30+ 分。
4. **转场是否净面-无白帧**（v364 重点）：dissolve1_post 和 dissolve2_post 必须
   立即呈现 shot5/shot6 的实质内容，**不可**有白淡入/纯白帧/空白帧。违规扣重分。
5. **节奏是否快慢呼吸**（v364 重点）：整体节奏应为「中速开 → 收紧快切 →
   放长一个情绪段 → 结尾加速」的呼吸感，而非均匀等长分配。
6. **切点落拍**：相邻帧之间是否节奏自然、内容连贯。
7. **制作完成度**：整体是否像可发布成片。

只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：
{
  "画面质量": <0-100>,
  "角色一致性": <0-100>,
  "开场是否直接上角色": <0-100>,
  "转场是否净面-无白帧": <0-100>,
  "节奏是否快慢呼吸": <0-100>,
  "切点落拍": <0-100>,
  "制作完成度": <0-100>,
  "overall_score": <0-100, 七项均分>,
  "open_with_character_ok": <bool, shot01 第一帧直接上角色>,
  "no_white_fade_in_ok": <bool, dissolve 后帧非白>,
  "rhythm_breathing_ok": <bool, 节奏快慢呼吸>,
  "issues": ["<issue1>", "<issue2>", ...],
  "highlights": ["<highlight1>", ...],
  "opinion": "<一句话结论>"
}
"""


def parse_json_response(raw: str) -> dict:
    """提取 VLM 返回中的 JSON 部分。

    VLM minimax-m3 经常返回 `<think>...</think>{...JSON...}` 形式，
    先尝试找 </think> 后面的最后一个 {...} 块，再回退到首/末大括号定位。
    """
    import re
    # 1) 尝试在 </think> 后找最后 JSON
    m = re.search(r"</think>\s*(\{.*\})\s*$", raw, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # 2) 找最后一个 { 到最后 } 之间
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

    print(f"\n[vlm-v364] sending {valid_count} frames to VLM "
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
    print(f"[vlm-v364] review → {out_json}", flush=True)
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
