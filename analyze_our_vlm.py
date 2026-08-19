#!/usr/bin/env python3
"""调用 minimax-m3 VLM 分析我们项目的 final_v6.mp4 filmstrip。"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from vlm_config import VLM_API_KEY, VLM_API_URL, VLM_MODEL  # noqa: E402

ROOT = Path(__file__).resolve().parent
FILMSTRIP_PATH = ROOT / "ref_analysis" / "filmstrip_our_4x4.jpg"
OUT_MD = ROOT / "ref_analysis" / "vlm_report_our_raw.md"


VLM_PROMPT = """你是一名短视频质量评审专家，正在评估一支**我们项目自己生成的 AI 短视频**（用于和一支抖音爆款做内部差距评估）。

背景信息：
- 来源：我们项目 pipeline（script→image→character→video→overlay→audio→final）产物
- 文件：final_v6.mp4
- 时长 15.5 秒，1920x1080 横屏，24fps
- 内容是我们 R2V 生成的视频 + overlay 字幕 + BGM/配音 + 烧录合并的最终成品

下面是 16 个抽帧（1 秒间隔）合成的 4x4 网格 filmstrip。每帧左上角是序号（#01-#16），右下角是时间码（00:00 - 00:15）。

请从以下 7 个维度做**详细、结构化**的评估（务必具体，引用帧号佐证）：

1. **画面质量**：分辨率观感、细节清晰度、光影处理、有无明显压缩/失真/色块/模糊
2. **角色一致性**：主角（若可见）在跨镜头中的脸型、发色、服装、气质是否稳定？有没有跳脸/换装/造型漂移？
3. **镜头语言**：景别变化、运镜方式（推/拉/摇/移/固定）、构图、镜头数（粗估）、节奏快慢
4. **动作流畅度**：肢体动作、面部表情、有无明显扭曲/形变/生成伪影（手指数目异常、肢体融合、面部崩塌）
5. **风格与氛围**：色彩倾向、动漫质感（赛璐璐/厚涂/3D渲染）、光影氛围
6. **制作完成度**：是否有字幕、标题卡、片头/片尾、Logo、角标、水印、包装
7. **档次定位**：作为一支成片，它的整体观感属于什么水准（请客观，不必客气）

最后给一个综合评分（满分 100）和一句话总结。

注意：这是横屏 1920x1080，**不是**抖音标准的 9:16 竖屏，请把这个因素考虑进"档次定位"评估。

输出格式请用 markdown，结构清晰。"""


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def call_vlm(prompt: str, image_b64: str, *, timeout: int = 180, max_retries: int = 3) -> dict:
    if not VLM_API_KEY:
        raise RuntimeError("VLM_API_KEY 为空")
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }
        ],
        "temperature": 0.4,
        "max_tokens": 3000,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        VLM_API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {VLM_API_KEY}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        },
        method="POST",
    )
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8")
                return {"ok": True, "status": resp.status, "attempt": attempt + 1,
                        "raw_json": json.loads(data)}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_err = exc
            time.sleep(2 ** attempt)
    return {"ok": False, "error": repr(last_err), "attempts": max_retries}


def _extract_assistant_text(resp_json: dict) -> str:
    try:
        choices = resp_json["choices"]
        if choices and "message" in choices[0]:
            return choices[0]["message"].get("content", "")
        if choices and "text" in choices[0]:
            return choices[0]["text"]
    except (KeyError, IndexError, TypeError):
        pass
    return ""


def main() -> int:
    if not FILMSTRIP_PATH.is_file():
        print(f"ERROR: {FILMSTRIP_PATH} not found")
        return 1
    print(f"[vlm-our] filmstrip: {FILMSTRIP_PATH}  ({FILMSTRIP_PATH.stat().st_size} bytes)")

    b64 = _encode_image(FILMSTRIP_PATH)
    resp = call_vlm(VLM_PROMPT, b64)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)

    with OUT_MD.open("w", encoding="utf-8") as f:
        f.write("# VLM 原始分析报告（我们 final_v6.mp4 filmstrip）\n\n")
        f.write(f"- 模型：`{VLM_MODEL}`\n")
        f.write(f"- 端点：`{VLM_API_URL}`\n")
        f.write(f"- 输入：filmstrip_our_4x4.jpg（{FILMSTRIP_PATH.stat().st_size} bytes）\n")
        f.write(f"- 抽帧：16 帧 × 1 秒间隔 = 16 秒覆盖（视频实际 15.5s）\n")
        f.write(f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("---\n\n## Prompt\n\n```\n")
        f.write(VLM_PROMPT)
        f.write("\n```\n\n---\n\n## 原始响应\n\n```json\n")
        f.write(json.dumps(resp, ensure_ascii=False, indent=2))
        f.write("\n```\n\n---\n\n")

    if not resp.get("ok"):
        with OUT_MD.open("a", encoding="utf-8") as f:
            f.write(f"\n## 错误\n\nVLM 调用失败：{resp.get('error')}\n")
        return 2

    text = _extract_assistant_text(resp["raw_json"])
    with OUT_MD.open("a", encoding="utf-8") as f:
        f.write("## VLM 输出（assistant message）\n\n")
        f.write(text if text else "_(空内容)_")
        f.write("\n\n")
        usage = resp["raw_json"].get("usage")
        if usage:
            f.write("---\n\n## Usage\n\n```json\n")
            f.write(json.dumps(usage, ensure_ascii=False, indent=2))
            f.write("\n```\n")
    print(f"[vlm-our] wrote {OUT_MD}")
    print(f"[vlm-our] response text length: {len(text)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())