#!/usr/bin/env python3
"""A 组（纯提示词直出）VLM 七维度评分。

策略：
- 抽 24 帧 filmstrip 分别覆盖参考视频 (input_douyin_ref.mp4) 和 final_a.mp4
- 一次 VLM 调用同时发两张 filmstrip，要求 VLM 横向对比、打七维度分
- 显式告诉 VLM 参考视频 68.5 分作为锚点

权重（与任务文件一致）：
- 画面质量 ×1.0
- 角色一致性 ×1.5
- 镜头语言 ×1.5
- 动作流畅度 ×1.0
- 风格与氛围 ×1.0
- 制作完成度 ×1.0
- 档次定位 ×1.0
合计权重 8.0
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from filmstrip import filmstrip_b64  # noqa: E402
from vlm_config import VLM_API_KEY, VLM_API_URL, VLM_MODEL  # noqa: E402

ROOT = Path(r"D:\ai-video-pipeline")
REF_VIDEO = ROOT / "input_douyin_ref.mp4"
FINAL_A = ROOT / "output" / "abtest" / "final_a.mp4"
QA_DIR = ROOT / "output" / "abtest" / "qa"

# 评分口径（与任务文件 §"评分" 节一致：画面×1.0/角色×1.5/镜头×1.5/动作×1.0/风格×1.0/完成度×1.0/档次×1.0，合计 8.0）
WEIGHTS = {
    "画面质量": 1.0,
    "角色一致性": 1.5,
    "镜头语言": 1.5,
    "动作流畅度": 1.0,
    "风格与氛围": 1.0,
    "制作完成度": 1.0,
    "档次定位": 1.0,
}

# 7 维度顺序（与 same_v1_qa 一致，方便 diff）
DIMS = ["画面质量", "角色一致性", "镜头语言", "动作流畅度", "风格与氛围", "制作完成度", "档次定位"]


def extract_frames(video: Path, n: int = 24) -> list:
    """从视频均匀抽 n 帧。"""
    from PIL import Image
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(r.stdout.strip() or 60.0)
    ts = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    tmpdir = video.parent / f"qa_frames_{video.stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(ts):
        out = tmpdir / f"frame_{i+1:02d}_{t:.2f}s.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True, encoding="utf-8", errors="replace", check=True,
        )
        frames.append(Image.open(out).convert("RGB"))
    return frames


# VLM 提示：明确告知参考视频分（68.5）让 VLM 校准尺度，并要求横向对比打分
VLM_PROMPT = """你是一名短视频质量评审专家，正在做一次**严肃的 A/B 对照实验**。

提供给你两组抽帧对比表（每组 24 帧 ~2.5s 间隔，6x4 网格 filmstrip）：
- 上面 filmstrip 是**参考视频**（`input_douyin_ref.mp4`，抖音爆款 PV"选学姐还是学妹？"双角色对比，标注分 68.5 / 100）
- 下面 filmstrip 是**待评估** A 组作品（`final_a.mp4`，H3 T2V 纯提示词直出 8 段拼接，主题一致）

A 组作品的**生产工艺完全不同**（纯提示词直出，无参考图无 R2V 无衍生），所以你需要结合"独立生成的方法局限性"和"7 维度纵深"综合给分。

请按以下 7 维度对 **A 组作品**（下面 filmstrip）逐一评分（0-100 整数），并**显式对比参考视频**作为锚点：

1. **画面质量**（权重 1.0）：分辨率观感、细节清晰度、光影处理、有无明显压缩/失真/色块/模糊
2. **角色一致性**（权重 1.5）：两位角色（学姐 / 学妹）在跨镜头中的脸型、发色、服装、配饰、气质是否稳定？有没有跳脸 / 换装 / 造型漂移？（注意：A 组是 8 段独立生成，一致性天然弱于 R2V 锁角色，扣分要克制）
3. **镜头语言**（权重 1.5）：景别变化、运镜方式（推 / 拉 / 摇 / 移 / 固定）、构图、镜头数、节奏快慢
4. **动作流畅度**（权重 1.0）：肢体动作幅度、面部表情、有无明显扭曲 / 形变 / 生成伪影（手指数目异常、肢体融合、面部崩塌）
5. **风格与氛围**（权重 1.0）：色彩倾向、动漫质感（赛璐璐 / 厚涂 / 3D 渲染）、光影氛围、是否符合二次元 PV 美学
6. **制作完成度**（权重 1.0）：是否有字幕、标题卡、片头 / 片尾、Logo、角标、水印、包装
7. **档次定位**（权重 1.0）：作为一支成片，它的整体观感属于什么水准（请客观，不必客气）

输出格式：markdown，含 7 维度评分表 + 综合加权分（按权重 1.0/1.5/1.5/1.0/1.0/1.0/1.0 = 8.0 归一化）+ 与参考视频 68.5 分的差距判断 + 一句话总结。

分数表格式必须严格如下（用于程序解析）：
| 维度 | 分数 | 权重 | 加权分 |
|---|---:|---:|---:|
| 画面质量 | <0-100> | 1.0 | <分数×1.0> |
| 角色一致性 | <0-100> | 1.5 | <分数×1.5> |
| 镜头语言 | <0-100> | 1.5 | <分数×1.5> |
| 动作流畅度 | <0-100> | 1.0 | <分数×1.0> |
| 风格与氛围 | <0-100> | 1.0 | <分数×1.0> |
| 制作完成度 | <0-100> | 1.0 | <分数×1.0> |
| 档次定位 | <0-100> | 1.0 | <分数×1.0> |
| **综合加权** | — | 8.0 | <sum(s×w)/8.0> |

最后给一句对比结论（参考 68.5，A 组约 X 分，差距 Y，结论 <严格优于 / 大致相当 / 显著优于 / 相当 / 显著弱于>）。
"""


def call_vlm(prompt: str, image_b64s: list[str], *,
             timeout: int = 300, max_retries: int = 3) -> dict:
    """单轮多图 VLM 调用。"""
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
        "temperature": 0.4,
        "max_tokens": 4000,
    }
    # 追加图像（按顺序：参考视频 filmstrip, A 组 filmstrip）
    for b64 in image_b64s:
        payload["messages"][0]["content"].append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        VLM_API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {VLM_API_KEY}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        },
        method="POST",
    )
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8")
                return {"ok": True, "status": resp.status, "attempt": attempt + 1,
                        "raw_json": json.loads(data)}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_err = exc
            print(f"[vlm] attempt {attempt+1} failed: {exc}", flush=True)
            time.sleep(3 + attempt * 2)
    return {"ok": False, "error": repr(last_err), "attempts": max_retries}


def parse_dim_scores(text: str) -> dict[str, int]:
    """从 VLM 输出 markdown 解析 7 维度整数分（复用 same_v1_qa 的解析逻辑）。"""
    scores = {}
    # 1) 表格解析
    table_rows = re.findall(r"\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|", text)
    for row in table_rows:
        cells = [c.strip().lstrip("#").strip() for c in row]
        c0 = cells[0]
        # 找分数列
        candidates = []
        for ci in cells[1:]:
            m = re.fullmatch(r"\*?\*?(\d{1,3})\*?\*?", ci.replace(" ", ""))
            if m:
                candidates.append(int(m.group(1)))
        if not candidates:
            continue
        for d in DIMS:
            if c0 == d or c0.startswith(d[:3]):
                # 选 0-100 范围内的
                for cs in candidates:
                    if 0 <= cs <= 100:
                        scores[d] = cs
                        break
                if d not in scores and candidates:
                    scores[d] = candidates[0]
                break
    if all(d in scores for d in DIMS):
        return scores

    # 2) fallback: ## N. 维度名 · **X / 100**
    sep = r"[—\-·]|\s+"
    for d in DIMS:
        if d in scores:
            continue
        m = re.search(rf"##\s*\d+\.\s*{re.escape(d)}\s*{sep}\s*\*?\*?(\d{{1,3}})\s*/\s*100", text)
        if m:
            scores[d] = int(m.group(1))
    if all(d in scores for d in DIMS):
        return scores

    # 3) fallback: 维度名 · 数字
    for d in DIMS:
        if d in scores:
            continue
        m = re.search(rf"{re.escape(d)}\s*{sep}\s*\*?\*?(\d{{1,3}})\b", text)
        if m:
            scores[d] = int(m.group(1))
    return scores


def main() -> int:
    QA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[vlm] 抽参考视频 24 帧 ...", flush=True)
    ref_frames = extract_frames(REF_VIDEO, 24)
    print(f"[vlm] 抽 A 组 24 帧 ...", flush=True)
    a_frames = extract_frames(FINAL_A, 24)

    print(f"[vlm] 合成参考 filmstrip ...", flush=True)
    ref_b64 = filmstrip_b64(ref_frames, labels=False, target_kb=300, prefix=False)
    ref_fs = QA_DIR / "filmstrip_ref_24.jpg"
    import base64
    ref_fs.write_bytes(base64.b64decode(ref_b64))
    print(f"  ref filmstrip: {ref_fs} ({ref_fs.stat().st_size} bytes)", flush=True)

    print(f"[vlm] 合成 A 组 filmstrip ...", flush=True)
    a_b64 = filmstrip_b64(a_frames, labels=False, target_kb=300, prefix=False)
    a_fs = QA_DIR / "filmstrip_a_24.jpg"
    a_fs.write_bytes(base64.b64decode(a_b64))
    print(f"  A filmstrip: {a_fs} ({a_fs.stat().st_size} bytes)", flush=True)

    print(f"[vlm] 调 VLM ({VLM_MODEL}) ...", flush=True)
    resp = call_vlm(VLM_PROMPT, [ref_b64, a_b64])

    text = ""
    if resp.get("ok"):
        try:
            text = resp["raw_json"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            pass

    scores = parse_dim_scores(text) if text else {}
    if scores:
        weighted = sum(scores[d] * w for d, w in WEIGHTS.items() if d in scores)
        total_w = sum(w for d, w in WEIGHTS.items() if d in scores)
        weighted_score = round(weighted / total_w, 1) if total_w > 0 else None
    else:
        weighted_score = None

    # 写报告
    md_path = QA_DIR / "vlm_report_a.md"
    md = [
        "# A 组 VLM 七维度评分报告（对比参考视频）",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模型：`{VLM_MODEL}`",
        f"- 端点：`{VLM_API_URL}`",
        f"- 参考视频：`{REF_VIDEO}`（38.875s->72.875s, 360x480, 8fps）",
        f"- 待评估：`{FINAL_A}`（62.25s, 720x1280, 24fps）",
        f"- filmstrip：参考 24 帧 / A 组 24 帧（~2.5s 间隔）",
        f"- filmstrip 文件：`{ref_fs}` / `{a_fs}`",
        "",
        "## 评分（七维度加权）",
        "",
        "| 维度 | 分数 | 权重 | 加权分 |",
        "|---|---:|---:|---:|",
    ]
    for d, w in WEIGHTS.items():
        s = scores.get(d, "—")
        ws = round(s * w, 1) if isinstance(s, int) else "—"
        md.append(f"| {d} | {s} | {w} | {ws} |")
    md.append(f"| **综合加权** | **{weighted_score}** | 8.0 | — |")
    md.append("")
    md.append("## VLM 完整输出")
    md.append("")
    md.append(text if text else "_(VLM 未返回内容)_")
    md.append("")
    md.append("## 调用信息")
    md.append("")
    md.append("```json")
    md.append(json.dumps({
        "ok": resp.get("ok"),
        "status": resp.get("status"),
        "attempt": resp.get("attempt"),
        "error": resp.get("error"),
        "usage": resp.get("raw_json", {}).get("usage") if resp.get("ok") else None,
    }, ensure_ascii=False, indent=2))
    md.append("```")
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[vlm] 报告: {md_path}", flush=True)
    print(f"[vlm] A 组综合加权分: {weighted_score}", flush=True)

    # 写评分 JSON
    sj_path = QA_DIR / "vlm_scores_a.json"
    sj_path.write_text(json.dumps({
        "ref_video": str(REF_VIDEO),
        "a_video": str(FINAL_A),
        "dim_scores": scores,
        "weighted_score": weighted_score,
        "weights": WEIGHTS,
        "vlm_model": VLM_MODEL,
        "ref_filmstrip": str(ref_fs),
        "a_filmstrip": str(a_fs),
        "report_md": str(md_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[vlm] 评分 JSON: {sj_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
