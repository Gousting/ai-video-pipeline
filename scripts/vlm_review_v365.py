#!/usr/bin/env python3
"""v3.6.5 VLM 视觉评审（per 任务书 v365 §4 Step 4 VLM 评审）。

vs vlm_review_v364.py 关键差异：

- **新增核验维度**："转场特效是否可见且不重复"（v365 §4 重点）
  - 抽每个转场点所在段的首 1-2s 帧（共 5 帧，对应 shot02-06）
  - 让 VLM 视觉确认每个段的 [TRANSITION_RHYTHM] + [INCOMING_TRANSITION_CUE]
    指定的转场特效是否真的渲染出来，且全片不重复。
- 沿用 v364 7 维度评分 + 10 关键帧（开场 + 中段 + dissolve 前后 + 收束）
- 沿用 v364 FRAME_TIMES（节奏不变）

CLI:
  python vlm_review_v365.py
  python vlm_review_v365.py --video output/pipeline_v36/final_v36_60s_v365.mp4
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

DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v365.mp4"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "qa_frames_v365"
DEFAULT_OUT_JSON = ROOT / "output" / "pipeline_v36" / "v365_vlm_review.json"
DEFAULT_CHAR_BLOCKS = ROOT / "output" / "pipeline_v36" / "shots_v365" / "char_blocks_v365.json"

# v365 §4: 每个转场特效在 H3 prompt 里指定「raw t=0.7-1.7s」渲染 (避开 trim head 0.5s).
# 抽帧时间表对齐 trimmed 段起点 + 段内 0.7-1.7s:
#   trimmed 段起点 (实测 final v365):
#     shot1: 0s, shot2: 9.625s, shot3: 17.125s, shot4: 23.208s,
#     shot5: 32.533s (after dissolve1 -0.3s), shot6: 38.217s (after dissolve2 -0.4s)
#   转场特效在 trimmed 段内 0.2-1.2s (即 raw 0.7-1.7s):
#     shot2 transition: 9.825 - 10.825s, sample ~10.3s (mid 1.0s)
#     shot3 transition: 17.325 - 18.325s, sample ~17.8s
#     shot4 transition: 23.408 - 24.408s, sample ~23.9s
#     shot5 transition: 32.733 - 33.733s, sample ~33.2s
#     shot6 transition: 38.417 - 39.417s, sample ~38.9s
FRAME_TIMES = {
    # v365 §4: 转场特效帧 (5 帧对应 shot02-06 的 5 个不同转场)
    "shot02_transition_halftone":    10.3,   # shot02 段内 ~0.7s, halftone dot flash
    "shot03_transition_whippan":     17.8,   # shot03 段内 ~0.7s, whip pan+streaks
    "shot04_transition_inkburst":    23.9,   # shot04 段内 ~0.7s, ink burst 爆发瞬间
    "shot05_transition_fabricwipe":  33.2,   # shot05 段内 ~0.7s, fabric 扫镜
    "shot06_transition_diagonalslash": 38.9, # shot06 段内 ~0.7s, diagonal slash

    # 沿用 v364 的 10 关键帧 (节奏不变, 评审连贯性)
    "shot01_open":     1.0,   # 开场第一秒 (验证直接上角色, 无白淡入, 无花哨转场)
    "shot01_mid":      5.0,
    "shot02_mid":     13.0,
    "shot03_mid":     19.0,
    "shot04_peak":    26.0,
    "dissolve1_pre":  31.5,
    "dissolve1_post": 32.5,
    "dissolve2_pre":  37.0,
    "dissolve2_post": 37.8,
    "shot06_tail":    42.0,
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


def load_transition_assignment() -> dict:
    """从 char_blocks_v365.json 抽 TRANSITION_ASSIGNMENT."""
    if not DEFAULT_CHAR_BLOCKS.exists():
        return {}
    j = json.loads(DEFAULT_CHAR_BLOCKS.read_text(encoding="utf-8"))
    return {int(k): v for k, v in j.get("TRANSITION_ASSIGNMENT", {}).items()}


REVIEW_PROMPT = """你是一名短视频画面质量审查员 (兼花哨生成层转场特效审查员)。

我会给你一张短视频成片的关键帧序列（共 15 张，按时间顺序）：

**生成层转场特效核验帧（v365 新增，重点核验）**：
1. shot02_transition_halftone    : 第二段段首 1-2s，应是「halftone dot overlay flash」（半调网点覆盖闪光切入）。验证：是否能看到大尺寸 Ben-Day 网点图案在画面上闪过后褪去？
2. shot03_transition_whippan     : 第三段段首 1-2s，应是「whip pan with motion streaks」（甩镜+运动模糊线条）。验证：是否有横向的运动模糊条纹/速度线？
3. shot04_transition_inkburst    : 第四段段首 1-2s，应是「color explosion / ink burst sweeping across frame」（色彩爆炸/墨爆扫过画幅）。验证：是否有高饱和 CMYK 墨水从画面一侧喷涌/扫过？
4. shot05_transition_fabricwipe  : 第五段段首 1-2s，应是「fabric wipe where clothing sweeps past camera」（布料扫镜：衣服/发丝扫过镜头）。验证：是否能看到柔软的奶油色布料或棕色头发扫过镜头？
5. shot06_transition_diagonalslash: 第六段段首 1-2s，应是「diagonal slash wipe」（斜切划像）。验证：是否有柠檬黄色或高亮色斜向划带扫过画面？

**沿用 v364 的常规核验帧**：
6. shot01_open: 开场第一秒（**关键**：验证是否直接出现清晰的角色正脸，禁止抽象/手部/光斑开场；shot01 不应有任何花哨转场特效）
7. shot01_mid: 第一段中部（slow_open，定调）
8. shot02_mid: 第二段中部（build，学妹登场，转场特效已褪去）
9. shot03_mid: 第三段中部（fast，双人同框，转场特效已褪去）
10. shot04_peak: 第四段中部（peak，情绪锚/高潮长段）
11. dissolve1_pre: 第四段末尾（dissolve 转场前最后一帧）
12. dissolve1_post: 第五段开头（dissolve 转场后第一帧，**关键**：验证非白帧）
13. dissolve2_pre: 第五段末尾（dissolve 转场前最后一帧）
14. dissolve2_post: 第六段开头（dissolve 转场后第一帧，**关键**：验证非白帧）
15. shot06_tail: 第六段中部（tail，收束）

请基于这些帧做 8 维度评审（0-100 分），并说明理由：

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
7. **转场特效可见且全片不重复**（v365 新增，重点核验）：
   - shot02-06 各自的转场特效是否真的在段首 1-2s 渲染出来了？
   - 5 个转场特效是否全片唯一（没有任何重复）？
   - 转场特效是否自然融入画面（不是突兀的滤镜），有物理感？
   评分指南：每个转场特效都正确渲染且唯一 → 90+；4/5 正确 → 70-85；
            3/5 正确 → 50-65；2/5 或更少 → 30 以下。
8. **制作完成度**：整体是否像可发布成片。

只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：
{
  "画面质量": <0-100>,
  "角色一致性": <0-100>,
  "开场是否直接上角色": <0-100>,
  "转场是否净面-无白帧": <0-100>,
  "节奏是否快慢呼吸": <0-100>,
  "切点落拍": <0-100>,
  "转场特效可见且全片不重复": <0-100>,
  "制作完成度": <0-100>,
  "overall_score": <0-100, 八项均分>,
  "open_with_character_ok": <bool, shot01 第一帧直接上角色>,
  "no_white_fade_in_ok": <bool, dissolve 后帧非白>,
  "rhythm_breathing_ok": <bool, 节奏快慢呼吸>,
  "transition_visibility": {
    "shot02_halftone_visible": <bool>,
    "shot03_whippan_visible": <bool>,
    "shot04_inkburst_visible": <bool>,
    "shot05_fabricwipe_visible": <bool>,
    "shot06_diagonalslash_visible": <bool>,
    "transitions_unique_ok": <bool, 全片 5 个不重复>,
    "transitions_physically_motivated_ok": <bool, 转场有物理感, 不突兀>,
    "notes": "<一段话简述每个转场特效的渲染表现>"
  },
  "issues": ["<issue1>", "<issue2>", ...],
  "highlights": ["<highlight1>", ...],
  "opinion": "<一句话结论>"
}
"""


def parse_json_response(raw: str) -> dict:
    import re
    m = re.search(r"</think>\s*(\{.*\})\s*$", raw, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
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
    # Force UTF-8 stdout (Windows GBK default breaks on U+2713 / 中文)
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
        _sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

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

    tr_assign = load_transition_assignment()
    print(f"[vlm-v365] TRANSITION_ASSIGNMENT: {tr_assign}", flush=True)

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

    print(f"\n[vlm-v365] sending {valid_count} frames to VLM "
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
    review["transition_assignment"] = tr_assign
    review["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[vlm-v365] review → {out_json}", flush=True)
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
