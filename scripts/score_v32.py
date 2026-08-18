#!/usr/bin/env python3
"""v3.2 VLM 七维度评分。

差异 vs ab_score.py：
  - 参考视频：input_h3_pv_ref.mp4（v3.2 任务指定的 MiniMax H3 参考 PV）
  - 待评估：output/pipeline_v3/final_v3.mp4
  - prompt 强调 v3.2 关键工艺差异（无独白 / 节拍对齐 / Mai Yoneyama / in-prompt 转场）

CLI:
  python score_v32.py
"""
from __future__ import annotations

import argparse
import base64
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
REF_VIDEO = ROOT / "input_h3_pv_ref.mp4"
FINAL = ROOT / "output" / "pipeline_v3" / "final_v3.mp4"
QA_DIR = ROOT / "output" / "pipeline_v3" / "qa"
QA_DIR.mkdir(parents=True, exist_ok=True)

WEIGHTS = {
    "画面质量": 1.0,
    "角色一致性": 1.5,
    "镜头语言": 1.5,
    "动作流畅度": 1.0,
    "风格与氛围": 1.0,
    "制作完成度": 1.0,
    "档次定位": 1.0,
}
DIMS = list(WEIGHTS.keys())


def extract_frames(video: Path, n: int = 24) -> list:
    from PIL import Image
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(r.stdout.strip() or 60.0)
    ts = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    tmpdir = video.parent / f"qa_frames_{video.stem}_v32"
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


VLM_PROMPT = """你是一名短视频质量评审专家，正在做一次**严肃的工艺还原度评估**。

任务背景：
- **参考视频**（上面 filmstrip）：MiniMax H3 T2V 参考 PV（米山舞 MV 风格，抖音爆款，作者声明 AI 生成 0 垫图，标注分 68.5 / 100）。
- **待评估 v3.2**（下面 filmstrip）：本地复刻同一工艺的成片。

参考视频的工艺核心（v3.2 任务要还原的工艺）：
1. **纯 T2V 直出 0 垫图**（v3.2 同样纯直出，未用 Z-Image）
2. **米山舞风赛璐璐 + 高饱和霓虹 CMYK 配色**（品红/青/荧光绿/柠檬黄）
3. **快切 MV 节奏**：31s 塞 15-20 个视觉镜头，每镜 1.5-3s
4. **转场设计进 prompt**：色彩爆炸/布料扫镜/斜切划像/漫画分屏等都是生成时画出来的
5. **零对白纯音乐**（v3.2 必须无独白）
6. **角色漂移存在但被快切掩盖**

v3.2 与参考的**内容主题不同**（v3.2 是"学姐 vs 学妹"双角色对比），但**工艺**必须还原。

请按以下 7 维度对 **v3.2**（下面 filmstrip）逐一评分（0-100 整数）：

1. **画面质量**（权重 1.0）：分辨率观感、细节清晰度、光影处理、有无明显压缩/失真/色块/模糊
2. **角色一致性**（权重 1.5）：跨镜头中角色（学姐/学妹）脸型/发色/服装/气质稳定度？v3.2 接受漂移但靠快切掩盖，所以扣分要克制
3. **镜头语言**（权重 1.5）：景别变化、运镜方式、构图、节奏快慢
4. **动作流畅度**（权重 1.0）：肢体动作、面部表情、有无明显扭曲/形变/伪影
5. **风格与氛围**（权重 1.0）：色彩倾向、米山舞/赛璐璐质感、霓虹 CMYK 配色
6. **制作完成度**（权重 1.0）：字幕、标题卡、片头/片尾、包装
7. **档次定位**（权重 1.0）：整体观感水准

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

最后给一句对比结论（参考 68.5，v3.2 约 X 分，差距 Y，结论 <严格优于 / 大致相当 / 显著优于 / 相当 / 显著弱于>）。
"""


def call_vlm(prompt: str, image_b64s: list[str], *,
             timeout: int = 300, max_retries: int = 3) -> dict:
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {"role": "user",
             "content": [{"type": "text", "text": prompt}]}
        ],
        "temperature": 0.4,
        "max_tokens": 4000,
    }
    for b64 in image_b64s:
        payload["messages"][0]["content"].append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        VLM_API_URL, data=body,
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
    scores = {}
    table_rows = re.findall(r"\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|", text)
    for row in table_rows:
        cells = [c.strip().lstrip("#").strip() for c in row]
        c0 = cells[0]
        candidates = []
        for ci in cells[1:]:
            m = re.fullmatch(r"\*?\*?(\d{1,3})\*?\*?", ci.replace(" ", ""))
            if m:
                candidates.append(int(m.group(1)))
        if not candidates:
            continue
        for d in DIMS:
            if c0 == d or c0.startswith(d[:3]):
                for cs in candidates:
                    if 0 <= cs <= 100:
                        scores[d] = cs
                        break
                if d not in scores and candidates:
                    scores[d] = candidates[0]
                break
    if all(d in scores for d in DIMS):
        return scores

    sep = r"[—\-·]|\s+"
    for d in DIMS:
        if d in scores:
            continue
        m = re.search(rf"##\s*\d+\.\s*{re.escape(d)}\s*{sep}\s*\*?\*?(\d{{1,3}})\s*/\s*100", text)
        if m:
            scores[d] = int(m.group(1))
    if all(d in scores for d in DIMS):
        return scores

    for d in DIMS:
        if d in scores:
            continue
        m = re.search(rf"{re.escape(d)}\s*{sep}\s*\*?\*?(\d{{1,3}})\b", text)
        if m:
            scores[d] = int(m.group(1))
    return scores


def main() -> int:
    print(f"[vlm] 抽参考视频 24 帧 ...", flush=True)
    ref_frames = extract_frames(REF_VIDEO, 24)
    print(f"[vlm] 抽 v3.2 24 帧 ...", flush=True)
    v32_frames = extract_frames(FINAL, 24)

    print(f"[vlm] 合成参考 filmstrip ...", flush=True)
    ref_b64 = filmstrip_b64(ref_frames, labels=False, target_kb=300, prefix=False)
    ref_fs = QA_DIR / "filmstrip_ref_24_v32.jpg"
    ref_fs.write_bytes(base64.b64decode(ref_b64))
    print(f"  ref filmstrip: {ref_fs} ({ref_fs.stat().st_size} bytes)", flush=True)

    print(f"[vlm] 合成 v3.2 filmstrip ...", flush=True)
    v32_b64 = filmstrip_b64(v32_frames, labels=False, target_kb=300, prefix=False)
    v32_fs = QA_DIR / "filmstrip_v32_24.jpg"
    v32_fs.write_bytes(base64.b64decode(v32_b64))
    print(f"  v32 filmstrip: {v32_fs} ({v32_fs.stat().st_size} bytes)", flush=True)

    print(f"[vlm] 调 VLM ({VLM_MODEL}) ...", flush=True)
    resp = call_vlm(VLM_PROMPT, [ref_b64, v32_b64])

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

    md_path = QA_DIR / "vlm_report_v32.md"
    md = [
        "# v3.2 VLM 七维度评分报告（对比参考视频）",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模型：`{VLM_MODEL}`",
        f"- 端点：`{VLM_API_URL}`",
        f"- 参考视频：`{REF_VIDEO}`（31.34s, 1358x576, h264+aac, 抖音「AI赛先生」作品，作者声明 AI 生成 0 垫图，标注分 68.5）",
        f"- 待评估 v3.2：`{FINAL}`（60.79s, 720x1280, h264+aac, 24fps）",
        f"- filmstrip：参考 24 帧 / v3.2 24 帧（~2.5s 间隔）",
        f"- filmstrip 文件：`{ref_fs}` / `{v32_fs}`",
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
    print(f"[vlm] v3.2 综合加权分: {weighted_score}", flush=True)

    sj_path = QA_DIR / "vlm_scores_v32.json"
    sj_path.write_text(json.dumps({
        "ref_video": str(REF_VIDEO),
        "v32_video": str(FINAL),
        "dim_scores": scores,
        "weighted_score": weighted_score,
        "weights": WEIGHTS,
        "vlm_model": VLM_MODEL,
        "ref_filmstrip": str(ref_fs),
        "v32_filmstrip": str(v32_fs),
        "report_md": str(md_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[vlm] 评分 JSON: {sj_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
