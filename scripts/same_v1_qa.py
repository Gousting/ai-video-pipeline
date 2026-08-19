#!/usr/bin/env python3
"""对 final_same_v1.mp4 做 VLM 质检：抽帧 + filmstrip + 评分 + 报告。

对比 ref_analysis/vlm_report_raw.md 中的参考视频评分标准。

CLI:
    python scripts/same_v1_qa.py --video D:/ai-video-pipeline/output/same_v1/final_same_v1.mp4
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from filmstrip import filmstrip_b64  # noqa: E402
from vlm_config import VLM_API_KEY, VLM_API_URL, VLM_MODEL  # noqa: E402


SAME_DIR = Path(r"D:\ai-video-pipeline\output\same_v1")
REF_DIR = Path(r"D:\ai-video-pipeline\ref_analysis")
REPORT_DIR = SAME_DIR / "qa"


VLM_PROMPT = """你是一名短视频质量评审专家，正在评估一支**AI 自动生成的二次元短视频**（项目自己产出，用于和抖音爆款做内部差距评估）。

背景信息：
- 来源：我们项目 ai-video-pipeline（script→image→character→video→overlay→audio→final）产物
- 文件：final_same_v1.mp4（"选学姐还是学妹？"双角色对比 PV）
- 时长 ~62 秒，竖屏 720x1280，9:16，24fps
- 选题参考：抖音爆款《选学姐还是学妹？》（学姐/学妹对比 PV）
- 内容：学姐（白发粉眼哥特朋克骷髅风）+ 学妹（黑发棕眼校园水手服小熊挂件风），两角色 PV 展示 + 双人对比 + CTA

下面是 24 个抽帧（每 ~2.5 秒一张）合成的 6x4 网格 filmstrip。每帧左上角是序号（#01-#24），右下角是时间码。

请从以下 7 个维度做**详细、结构化**的评估（务必具体，引用帧号佐证）：

1. **画面质量**：分辨率观感、细节清晰度、光影处理、有无明显压缩/失真/色块/模糊
2. **角色一致性**：两位角色（学姐/学妹）在跨镜头中的脸型、发色、服装、配饰、气质是否稳定？有没有跳脸/换装/造型漂移？哪些帧保持得好，哪些帧崩了？
3. **镜头语言**：景别变化（中景/近景/特写切换）、运镜方式（推/拉/摇/移/固定）、构图、镜头数（粗估）、节奏快慢
4. **动作流畅度**：肢体动作幅度、面部表情、有无明显扭曲/形变/生成伪影（手指数目异常、肢体融合、面部崩塌）
5. **风格与氛围**：色彩倾向、动漫质感（赛璐璐/厚涂/3D渲染）、光影氛围、是否符合二次元 PV 美学
6. **制作完成度**：是否有字幕、标题卡、片头/片尾、Logo、角标、水印、包装（注意：不应出现 "Phase X · Compose" / "Rendered by ai-video-pipeline" 这类研发阶段标签）
7. **档次定位**：作为一支成片，它的整体观感属于什么水准（请客观，不必客气）

最后给一个综合评分（满分 100）和一句话总结。

输出格式请用 markdown，结构清晰。综合分请用 7 维度加权：
- 画面质量 ×1.0
- 角色一致性 ×1.5
- 镜头语言 ×1.5
- 动作流畅度 ×1.0
- 风格与氛围 ×1.2
- 制作完成度 ×1.5
- 档次定位 ×1.0
归一化（除以 8.7）后给一个数字分，并在 markdown 里把每个维度的 0-100 整数分列出来，方便程序解析。"""


def extract_frames(video: Path, n: int = 24) -> list:
    """从视频均匀抽 n 帧。"""
    import subprocess
    from PIL import Image
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(r.stdout.strip() or 60.0)
    ts = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    tmpdir = video.parent / "qa_frames"
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


def call_vlm(prompt: str, image_b64: str, *, timeout: int = 240, max_retries: int = 3) -> dict:
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
        "max_tokens": 5000,
    }
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
            time.sleep(2 ** attempt)
    return {"ok": False, "error": repr(last_err), "attempts": max_retries}


def parse_dim_scores(text: str) -> dict[str, int]:
    """从 VLM 输出 markdown 解析 7 维度整数分。

    VLM 表格格式多变：可能 "| 维度 | 分数 | 权重 | 加权 |" 也可能 "| 维度 | 权重 | 0-100 分 | 加权得分 |"
    先尝试表格解析，再 fallback 到 ## 标题解析。
    """
    import re
    dims = ["画面质量", "角色一致性", "镜头语言", "动作流畅度",
            "风格与氛围", "制作完成度", "档次定位"]
    scores = {}

    # 1) Most reliable path: the VLM writes one `xx / 100` line per
    # numbered section.  Keep this before the generated score-table parser so
    # U+FFFD-corrupted labels cannot turn a previous summary into noise.
    score_lines = [line for line in text.splitlines() if "/ 100" in line]
    score_values = []
    for line in score_lines:
        match = re.search(r"(\d{1,3})\s*/\s*100", line)
        if match:
            score_values.append(int(match.group(1)))
    if len(score_values) >= len(dims):
        for dimension, value in zip(dims, score_values[:len(dims)]):
            scores[dimension] = value
        return scores

    # 1b) The final VLM report commonly places a 7-row summary table after the
    # detailed sections.  Read that table specifically; it is more authoritative
    # than preliminary "Maybe xx/100" numbers in the analysis block.
    summary_start = text.find("## 📊 综合评分")
    if summary_start >= 0:
        final_rows = re.findall(
            r"\|\s*\d+\.\s*([^|]+?)\s*\|\s*\*\*?(\d{1,3})\*\*?\s*\|",
            text[summary_start:],
        )
        for name, value in final_rows:
            for dimension in dims:
                if name.strip().lstrip("0123456789. ") == dimension:
                    scores[dimension] = int(value)
                    break
    if all(d in scores for d in dims):
        return scores

    # 2) 表格解析：找 "| 维度名 | ... | 数字 | ... |"（数字列必须在第 2 或 3 列）
    # 用更宽松的匹配：每个 row 至少 4 列，每列 strip 后是数字或字符串
    table_rows = re.findall(r"\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|\s*([^|\n]*?)\s*\|", text)
    for row in table_rows:
        c0, c1, c2, c3 = [c.strip().lstrip("#").strip() for c in row]
        # 判断哪一列是分数（数字 1-3 位）
        candidates = []
        for ci, name in [(c0, "name"), (c1, "c1"), (c2, "c2"), (c3, "c3")]:
            score_match = re.fullmatch(r"\**(\d{1,3})\**", ci.replace(" ", "").replace("×", "").replace("÷", "").replace("—", "").strip())
            if score_match:
                candidates.append((name, int(score_match.group(1))))
        if not candidates:
            continue
        # 行名匹配维度；拒绝把 U+FFFD 污染后的旧报告摘要表当成维度名。
        if "\ufffd" in c0 or c0.startswith("�"):
            continue
        for d in dims:
            if c0 == d or c0.startswith(d[:3]):
                # 优先选分数列（一般是 0-100 范围）
                for cn, cs in candidates:
                    if 0 <= cs <= 100:
                        scores[d] = cs
                        break
                if d not in scores and candidates:
                    scores[d] = candidates[0][1]
                break
    if all(d in scores for d in dims):
        return scores

    # 2) 匹配 "## 1. 画面质量 · **35 / 100**"
    sep_pattern = r"[—\-·]|\s+"
    for d in dims:
        if d in scores:
            continue
        m = re.search(rf"##\s*\d+\.\s*{re.escape(d)}\s*{sep_pattern}\s*\*?\*?(\d{{1,3}})\s*/\s*100", text)
        if m:
            scores[d] = int(m.group(1))
    if all(d in scores for d in dims):
        return scores

    # 2a) Parse numbered detailed sections by section order.  The OpenAI-compatible
    # endpoint can occasionally return Chinese as U+FFFD in the markdown, so
    # matching the section numbers plus the stable "/100" score marker is more
    # reliable than matching corrupted dimension names.
    if len(scores) < len(dims):
        sections = re.finditer(r"^##\s*(\d+)\.[^\n]*\n(.*?)(?=^##|\Z)", text, re.M | re.S)
        for section in sections:
            number = int(section.group(1))
            if not 1 <= number <= len(dims):
                continue
            score_values_in_section = re.findall(
                r"\b(\d{1,3})\s*/\s*100\b", section.group(2)
            )
            if score_values_in_section:
                value = int(score_values_in_section[-1])
                if 0 <= value <= 100:
                    scores[dims[number - 1]] = value
    if all(d in scores for d in dims):
        return scores

    # 2b) Detailed markdown sections often put the score on a separate
    # "得分：58 / 100" line.  Parse the section body rather than accidentally
    # selecting the weight column from a later table.
    if len(scores) < len(dims):
        for d in dims:
            section = re.search(
                rf"##\s*\d+\.\s*{re.escape(d)}[^\n]*\n(.*?)(?=\n##|\Z)",
                text,
                flags=re.S,
            )
            if section:
                m = re.search(
                    r"(?:得分|评分)\s*[：:]\s*\*?\*?(\d{1,3})",
                    section.group(1),
                )
                if m and 0 <= int(m.group(1)) <= 100:
                    scores[d] = int(m.group(1))
    if all(d in scores for d in dims):
        return scores

    # 3) 匹配 "## 1. 画面质量 - 62"（无 /100 后缀）
    if len(scores) < len(dims):
        for d in dims:
            if d in scores:
                continue
            m = re.search(rf"##\s*\d+\.\s*{re.escape(d)}\s*{sep_pattern}\s*\*?\*?(\d{{1,3}})\b", text)
            if m:
                scores[d] = int(m.group(1))
    if all(d in scores for d in dims):
        return scores

    # 4) 最后 fallback: 匹配 "维度名 · **62 / 100**"
    for d in dims:
        if d in scores:
            continue
        m = re.search(rf"{re.escape(d)}\s*{sep_pattern}\s*\*?\*?(\d{{1,3}})\s*/\s*100", text)
        if m:
            scores[d] = int(m.group(1))
    return scores


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="same_v1 视频 VLM 质检")
    ap.add_argument("--video", default=str(SAME_DIR / "final_same_v1.mp4"))
    ap.add_argument("--out-dir", default=str(REPORT_DIR))
    args = ap.parse_args(argv)

    video = Path(args.video)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not video.is_file():
        print(f"ERROR: 视频不存在 {video}", file=sys.stderr)
        return 2

    print(f"[qa] 抽 24 帧 ...", flush=True)
    frames = extract_frames(video, n=24)

    print(f"[qa] 合成 filmstrip ...", flush=True)
    # labels=False：避免 VLM 把帧序号误认成 "V1/VL" 角标
    fs_b64 = filmstrip_b64(frames, labels=False, target_kb=300, prefix=False)
    fs_png = out_dir / "filmstrip_same_v1_6x4.jpg"
    fs_png.write_bytes(base64.b64decode(fs_b64))
    print(f"  filmstrip: {fs_png} ({fs_png.stat().st_size} bytes)", flush=True)

    print(f"[qa] 调 VLM ...", flush=True)
    resp = call_vlm(VLM_PROMPT, fs_b64)

    # 提取 assistant 文本
    text = ""
    if resp.get("ok"):
        try:
            text = resp["raw_json"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            pass

    # 解析分数
    dim_scores = parse_dim_scores(text) if text else {}
    weights = {"画面质量": 1.0, "角色一致性": 1.5, "镜头语言": 1.5,
               "动作流畅度": 1.0, "风格与氛围": 1.2, "制作完成度": 1.5,
               "档次定位": 1.0}
    if dim_scores:
        weighted = sum(dim_scores[d] * w for d, w in weights.items() if d in dim_scores)
        total_w = sum(w for d, w in weights.items() if d in dim_scores)
        weighted_score = round(weighted / total_w, 1) if total_w > 0 else None
    else:
        weighted_score = None

    # 写报告
    md_path = out_dir / "vlm_report_same_v1.md"
    md_lines = [
        "# same_v1 视频 VLM 质检报告",
        "",
        f"- 视频：`{video}`",
        f"- 模型：`{VLM_MODEL}`",
        f"- 端点：`{VLM_API_URL}`",
        f"- 抽帧：24 帧（~2.5s 间隔，覆盖 ~62s）",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Filmstrip：`{fs_png}`",
        "",
        "## 评分（七维度加权）",
        "",
        "| 维度 | 分数 | 权重 | 加权 |",
        "|---|---:|---:|---:|",
    ]
    for d, w in weights.items():
        s = dim_scores.get(d, "—")
        ws = round(s * w, 1) if isinstance(s, int) else "—"
        md_lines.append(f"| {d} | {s} | {w} | {ws} |")
    md_lines.append(f"| **综合加权** | **{weighted_score}** | 8.7 | — |")
    md_lines.append("")
    md_lines.append("## VLM 完整输出")
    md_lines.append("")
    md_lines.append(text if text else "_(VLM 未返回内容)_")
    md_lines.append("")
    md_lines.append("## 调用信息")
    md_lines.append("")
    md_lines.append("```json")
    md_lines.append(json.dumps({
        "ok": resp.get("ok"),
        "status": resp.get("status"),
        "attempt": resp.get("attempt"),
        "error": resp.get("error"),
        "usage": resp.get("raw_json", {}).get("usage") if resp.get("ok") else None,
    }, ensure_ascii=False, indent=2))
    md_lines.append("```")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[qa] 报告: {md_path}", flush=True)
    print(f"[qa] 综合加权分: {weighted_score}", flush=True)

    # 写评分 JSON 给后续脚本
    score_json = {
        "video": str(video),
        "dim_scores": dim_scores,
        "weighted_score": weighted_score,
        "weights": weights,
        "vlm_model": VLM_MODEL,
        "filmstrip": str(fs_png),
        "report_md": str(md_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    score_path = out_dir / "vlm_scores.json"
    score_path.write_text(json.dumps(score_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[qa] 评分 JSON: {score_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
