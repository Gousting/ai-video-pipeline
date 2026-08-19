#!/usr/bin/env python3
"""调用 minimax-m3 VLM 分析 filmstrip（OpenAI 兼容 chat completions）。

策略：
- 从 .env / vlm_config.py 读 API_KEY / API_URL / MODEL（不硬编码）
- base64 编码整张 filmstrip（≤ 400KB），放进 user message 的 image_url
- 失败重试 2 次（指数退避）
- 原始输出（VLM 回复 + prompt + 元数据）写入 vlm_report_raw.md
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

# 让 vlm_config.py 可 import
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import urllib.request
import urllib.error

from vlm_config import VLM_API_KEY, VLM_API_URL, VLM_MODEL  # noqa: E402

ROOT = Path(__file__).resolve().parent
FILMSTRIP_PATH = ROOT / "ref_analysis" / "filmstrip_ref_6x4.jpg"
OUT_MD = ROOT / "ref_analysis" / "vlm_report_raw.md"


VLM_PROMPT = """你是一名短视频质量评审专家，正在评估一支抖音爆款 AI 视频。

背景信息：
- 视频来源：抖音，账号"小黄的AI日记"（粉丝 1.3 万，获赞 13.6 万）
- 标题："选学姐还是学妹？提示词群里自取即可"
- 数据：点赞 3217 / 收藏 2938 / 评论 108 / 分享 988
- 时长 72.8 秒，竖屏 360x480，8fps（典型 AI 生成特征）
- 内容：minimax h3 直出的两个性格相反二次元角色（学姐/学妹）动漫 PV + 教程

下面是 24 个抽帧（3 秒间隔）合成的 6x4 网格 filmstrip。每帧左上角是序号（#01-#24），右下角是时间码（00:03 - 01:09）。

请从以下 7 个维度做**详细、结构化**的评估（务必具体，引用帧号佐证）：

1. **画面质量**：分辨率观感、细节清晰度、光影处理、有无明显压缩/失真/色块/模糊
2. **角色一致性**：两位角色（学姐/学妹）在跨镜头中的脸型、发色、服装、配饰、气质是否稳定？有没有跳脸/换装/造型漂移？哪些帧保持得好，哪些帧崩了？
3. **镜头语言**：景别变化（中景/近景/特写切换）、运镜方式（推/拉/摇/移/固定）、构图、镜头数（粗估）、节奏快慢
4. **动作流畅度**：肢体动作幅度、面部表情、有无明显扭曲/形变/生成伪影（手指数目异常、肢体融合、面部崩塌）
5. **风格与氛围**：色彩倾向、动漫质感（赛璐璐/厚涂/3D渲染）、光影氛围、是否符合二次元 PV 美学
6. **制作完成度**：是否有字幕、标题卡、片头/片尾、Logo、角标、音效/BGM 暗示（注意这是无音 filmstrip，只能看画面包装）、文字水印
7. **档次定位**：在抖音 AI 视频赛道里属于头部/腰部/尾部？和同类 AI 动漫账号对比处于什么水准？

最后给一个综合评分（满分 100）和一句话总结。

输出格式请用 markdown，结构清晰，便于后面 gap_report 引用。"""


def _encode_image(path: Path) -> str:
    raw = path.read_bytes()
    return base64.b64encode(raw).decode("ascii")


def call_vlm(prompt: str, image_b64: str, *, timeout: int = 180, max_retries: int = 3) -> dict:
    """POST 到 VLM chat completions。返回原始响应 dict。"""
    if not VLM_API_KEY:
        raise RuntimeError("VLM_API_KEY 为空，无法调用。请检查 .env 或环境变量。")

    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
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
            # 必须带 User-Agent，否则 Cloudflare 错误码 1010 (Forbidden) 拦截
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
                return {
                    "ok": True,
                    "status": resp.status,
                    "attempt": attempt + 1,
                    "raw_json": json.loads(data),
                }
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_err = exc
            wait = 2 ** attempt
            print(f"[vlm] attempt {attempt + 1} failed: {exc}; sleep {wait}s", file=sys.stderr)
            time.sleep(wait)
    return {"ok": False, "error": repr(last_err), "attempts": max_retries}


def _extract_assistant_text(resp_json: dict) -> str:
    """兼容 OpenAI / 兼容实现的 choices[0].message.content 形态。"""
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

    print(f"[vlm] filmstrip: {FILMSTRIP_PATH}  ({FILMSTRIP_PATH.stat().st_size} bytes)")
    print(f"[vlm] model    : {VLM_MODEL}")
    print(f"[vlm] endpoint : {VLM_API_URL}")

    b64 = _encode_image(FILMSTRIP_PATH)
    print(f"[vlm] base64   : {len(b64)} chars")

    resp = call_vlm(VLM_PROMPT, b64)

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)

    # 写原始报告
    with OUT_MD.open("w", encoding="utf-8") as f:
        f.write("# VLM 原始分析报告（参考视频 filmstrip）\n\n")
        f.write(f"- 模型：`{VLM_MODEL}`\n")
        f.write(f"- 端点：`{VLM_API_URL}`\n")
        f.write(f"- 输入：filmstrip_ref_6x4.jpg（{FILMSTRIP_PATH.stat().st_size} bytes）\n")
        f.write(f"- 抽帧：24 帧 × 3 秒间隔 = 72 秒覆盖\n")
        f.write(f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("---\n\n")
        f.write("## Prompt\n\n```\n")
        f.write(VLM_PROMPT)
        f.write("\n```\n\n---\n\n")
        f.write("## 原始响应\n\n```json\n")
        f.write(json.dumps(resp, ensure_ascii=False, indent=2))
        f.write("\n```\n\n---\n\n")

    if not resp.get("ok"):
        f_unsafe = OUT_MD.open("a", encoding="utf-8")
        f_unsafe.write("## 错误\n\n")
        f_unsafe.write(f"VLM 调用失败：{resp.get('error')}\n")
        f_unsafe.close()
        print(f"ERROR: VLM call failed. See {OUT_MD}")
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

    print(f"[vlm] wrote {OUT_MD}")
    print(f"[vlm] response text length: {len(text)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())