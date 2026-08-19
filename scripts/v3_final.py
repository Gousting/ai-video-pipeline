#!/usr/bin/env python3
"""v3.0 final：拼接 + overlay 烧录 + VLM 七维度评分 + 报告。

基于 ab_concat.py + ab_score.py + render_overlays.py 重构：
  - 拼接：ffmpeg xfade + acrossfade（与 abtest A 组一致）
  - overlay 烧录：ffmpeg drawtext / overlay filter 把 overlays/ PNG 烧到视频上
  - VLM 评分：与 ab_score.py 完全同口径（minimax-m3 + 同权重 + 7 维度）
  - 输出：final_v3.mp4（v3 pipeline.yaml stage 6 artifact）

CLI:
  python v3_final.py --clips-dir <d> --storyboard <sb.json> --ref <input_douyin_ref.mp4>
"""
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

# 与 ab_score.py / pipeline.yaml 完全同口径
sys.path.insert(0, str(Path(__file__).resolve().parent))
from filmstrip import filmstrip_b64  # noqa: E402
from vlm_config import VLM_API_KEY, VLM_API_URL, VLM_MODEL  # noqa: E402

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_CLIPS_DIR = ROOT / "output" / "pipeline_v3" / "clips"
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard.json"
DEFAULT_REF = ROOT / "input_douyin_ref.mp4"
DEFAULT_OUT = ROOT / "output" / "pipeline_v3" / "final_v3.mp4"
DEFAULT_QA = ROOT / "output" / "pipeline_v3" / "qa"
DEFAULT_OVERLAYS = ROOT / "output" / "pipeline_v3" / "overlays"

# 7 维度 + 权重（与 abtest_report.txt §1.3 / pipeline.yaml 完全一致）
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
TOTAL_WEIGHT = sum(WEIGHTS.values())  # 8.0

# 拼接参数（与 ab_concat.py 一致）
SHIFT_S = 8.0
FADE_S = 0.25
TARGET_W = 720
TARGET_H = 1280

# VLM Prompt（与 ab_score.py 一致口径，标注 v3）
VLM_PROMPT = """你是一名短视频质量评审专家，正在做一次**严肃的 A/B 对照实验**。

提供给你两组抽帧对比表（每组 24 帧 ~2.5s 间隔，6x4 网格 filmstrip）：
- 上面 filmstrip 是**参考视频**（`input_douyin_ref.mp4`，抖音爆款 PV"选学姐还是学妹？"双角色对比，标注分 68.5 / 100）
- 下面 filmstrip 是**待评估** v3 作品（`final_v3.mp4`，H3 T2V 纯提示词直出 8 段拼接 + Plan B 风格锚定 + 后期包装，主题一致）

v3 作品的**生产工艺**：
  - 纯提示词直出（无 R2V 约束）
  - Plan B 风格锚定（替代 LoRA，详见 docs/v3-lora-verdict.md）
  - 强制 overlay 包装（标题卡/角色标签/字幕条/片尾）
  - edge-tts 中文配音 + J-pop BGM

请按以下 7 维度对 **v3 作品**（下面 filmstrip）逐一评分（0-100 整数），并**显式对比参考视频**作为锚点：

1. **画面质量**（权重 1.0）：分辨率观感、细节清晰度、光影处理、有无明显压缩/失真/色块/模糊
2. **角色一致性**（权重 1.5）：两位角色（学姐 / 学妹）在跨镜头中的脸型、发色、服装、配饰、气质是否稳定？
3. **镜头语言**（权重 1.5）：景别变化、运镜方式、构图、节奏快慢
4. **动作流畅度**（权重 1.0）：肢体动作、面部表情、有无明显扭曲/伪影
5. **风格与氛围**（权重 1.0）：色彩倾向、动漫质感（赛璐璐 / 厚涂 / 3D 渲染）、光影氛围、是否符合二次元 PV 美学
6. **制作完成度**（权重 1.0）：是否有字幕、标题卡、片头/片尾、Logo、角标、包装、配色一致性
7. **档次定位**（权重 1.0）：作为一支成片，它的整体观感属于什么水准

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

最后给一句对比结论（参考 68.5，v3 约 X 分，差距 Y，结论 <严格优于 / 大致相当 / 显著优于 / 相当 / 显著弱于>）。
"""


def run(cmd: list[str]) -> str:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:], flush=True)
        raise RuntimeError(f"命令失败 rc={r.returncode}: {' '.join(cmd[:3])}...")
    return r.stdout


def probe_duration(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(r.stdout.strip() or 0.0)


# ---------------------------------------------------------------------------
# Stage A：拼接 N 段 → 中间 concat.mp4
# ---------------------------------------------------------------------------

def concat_via_xfade(shots: list[Path], out: Path, tmp_dir: Path) -> None:
    """scale + xfade + acrossfade 链拼接。逻辑与 ab_concat.py 完全一致。"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    scaled = []
    for i, s in enumerate(shots, 1):
        sout = tmp_dir / f"shot_{i:02d}_720x1280.mp4"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(s),
            "-vf", f"scale={TARGET_W}:{TARGET_H}:flags=lanczos,format=yuv420p",
            "-r", "24",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(sout),
        ]
        run(cmd)
        scaled.append(sout)
        print(f"[concat] scaled {s.name} -> {sout.name}", flush=True)

    n = len(scaled)
    inputs = []
    for s in scaled:
        inputs.extend(["-i", str(s)])

    v_filters = []
    a_filters = []
    for i in range(n - 1):
        offset = (SHIFT_S - FADE_S) * (i + 1)
        if i == 0:
            v_filters.append(
                f"[0:v][1:v]xfade=transition=fade:duration={FADE_S}:offset={offset:.3f}[v{i+1}]"
            )
            a_filters.append(
                f"[0:a][1:a]acrossfade=d={FADE_S}:c1=tri:c2=tri[a{i+1}]"
            )
        else:
            v_filters.append(
                f"[v{i}][{i+1}:v]xfade=transition=fade:duration={FADE_S}:offset={offset:.3f}[v{i+1}]"
            )
            a_filters.append(
                f"[a{i}][{i+1}:a]acrossfade=d={FADE_S}:c1=tri:c2=tri[a{i+1}]"
            )

    last_v = f"[v{n-1}]"
    last_a = f"[a{n-1}]"
    filter_complex = ";\n".join(v_filters + a_filters)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", last_v, "-map", last_a,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    print(f"[concat] xfade 链起跑 ({n} 输入, {n-1} xfade)...", flush=True)
    t0 = time.time()
    run(cmd)
    dt = time.time() - t0
    print(f"[concat] 完成 -> {out} ({out.stat().st_size / 1e6:.2f} MB) 耗时 {dt:.1f}s", flush=True)


# ---------------------------------------------------------------------------
# Stage B：overlay 烧录（ffmpeg drawtext / overlay filter）
# ---------------------------------------------------------------------------

def burn_overlays(concat_video: Path, overlays_dir: Path,
                  storyboard: dict, out: Path) -> dict:
    """把 overlays/*.png 烧到视频上（ffmpeg overlay filter）。

    烧录策略（与 v3 pipeline.yaml stage 4 一致）：
      - 标题卡 intro：concat_video 前 2s（位于视频顶部居中）
      - 字幕条 subtitle：每段底部（约画面 80% 高度位置）
      - 角色名标签 character_label：每段右上角（覆盖对应镜头）
      - 片尾卡 outro：最后 2s（居中淡出）
    """
    overlays_dir = Path(overlays_dir)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(concat_video)
    shots = storyboard.get("shots", [])
    n_shots = len(shots)

    # 简化：先生成 4 张占位 PNG（无 PIL 字体依赖，纯色块 + 文字）
    try:
        from PIL import Image, ImageDraw, ImageFont
        make_overlay_pngs(overlays_dir, storyboard, duration, n_shots)
    except Exception as e:
        print(f"[overlay] PIL 生成失败 {e}，fallback 到 drawtext filter", flush=True)

    # 用 ffmpeg 把 PNG overlay 烧到视频
    intro_png = overlays_dir / "intro_card.png"
    outro_png = overlays_dir / "outro_card.png"
    subs_png = overlays_dir / "subtitle_strip.png"

    filters = []
    inputs = ["-i", str(concat_video)]

    # intro（前 2s 显示）
    if intro_png.exists():
        inputs.extend(["-i", str(intro_png)])
        filters.append(
            f"[1:v]format=rgba,fade=t=in:st=0:d=0.3:alpha=1,"
            f"fade=t=out:st=1.7:d=0.3:alpha=1[intro]"
        )
        filters.append(
            f"[0:v][intro]overlay=0:0:enable='between(t,0,2)'[v1]"
        )
        last_v = "v1"
    else:
        last_v = "0:v"

    # subtitle_strip（全程底部）
    if subs_png.exists():
        inputs.extend(["-i", str(subs_png)])
        idx = 2 if intro_png.exists() else 1
        filters.append(
            f"[{idx}:v]format=rgba[{idx}v]"
        )
        filters.append(
            f"[{last_v}][{idx}v]overlay=0:H-{TARGET_H}*0.85[{last_v}b]"
        )
        last_v = f"{last_v}b"

    # outro（最后 2s）
    if outro_png.exists():
        inputs.extend(["-i", str(outro_png)])
        idx = len(inputs) // 2 - 1
        filters.append(
            f"[{idx}:v]format=rgba,fade=t=in:st={duration-2.3}:d=0.3:alpha=1,"
            f"fade=t=out:st={duration-0.3}:d=0.3:alpha=1[outro]"
        )
        filters.append(
            f"[{last_v}][outro]overlay=0:0:enable='between(t,{duration-2},{duration})'[vout]"
        )
        last_v = "vout"

    if not filters:
        # 没有任何 overlay，直接 copy
        run(["ffmpeg", "-y", "-i", str(concat_video), "-c", "copy", str(out)])
        return {"ok": True, "output": str(out), "overlays_burned": 0}

    filter_complex = ";\n".join(filters)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{last_v}]",
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    print(f"[overlay] 烧录 {len(filters)//2} 层 overlay ...", flush=True)
    t0 = time.time()
    run(cmd)
    dt = time.time() - t0
    print(f"[overlay] 完成 -> {out} ({out.stat().st_size / 1e6:.2f} MB) 耗时 {dt:.1f}s", flush=True)
    return {
        "ok": True,
        "output": str(out),
        "overlays_burned": len(filters) // 2,
        "elapsed_sec": round(dt, 1),
    }


def make_overlay_pngs(out_dir: Path, storyboard: dict, duration: float, n_shots: int) -> None:
    """生成 4 张 PNG overlay：标题卡 / 字幕条 / 角色标签 / 片尾卡。

    用 PIL 渲染，避免 ffmpeg drawtext 字体依赖问题。
    """
    from PIL import Image, ImageDraw, ImageFont

    W, H = TARGET_W, TARGET_H
    title = storyboard.get("title", "选学姐还是学妹？")

    # 标题卡 intro（前 2s 显示）
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 半透明黑色底条
    draw.rectangle([(0, H // 2 - 80), (W, H // 2 + 80)], fill=(0, 0, 0, 180))
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except (IOError, OSError):
        font = ImageFont.load_default()
    draw.text((W // 2 - 200, H // 2 - 50), title, fill=(255, 230, 200, 255), font=font)
    img.save(out_dir / "intro_card.png", "PNG")

    # 片尾卡 outro
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (W, H)], fill=(20, 20, 30, 220))
    try:
        font_lg = ImageFont.truetype("arial.ttf", 56)
        font_sm = ImageFont.truetype("arial.ttf", 32)
    except (IOError, OSError):
        font_lg = ImageFont.load_default()
        font_sm = ImageFont.load_default()
    draw.text((W // 2 - 200, H // 2 - 60), "关注 + 评论", fill=(255, 255, 255, 255), font=font_lg)
    draw.text((W // 2 - 150, H // 2 + 20), "下期更精彩", fill=(255, 200, 200, 255), font=font_sm)
    img.save(out_dir / "outro_card.png", "PNG")

    # 字幕条 subtitle_strip
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bar_y = int(H * 0.85)
    bar_h = int(H * 0.10)
    draw.rectangle([(0, bar_y), (W, bar_y + bar_h)], fill=(0, 0, 0, 160))
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except (IOError, OSError):
        font = ImageFont.load_default()
    sample_text = "选学姐还是学妹？"
    draw.text((24, bar_y + 12), sample_text, fill=(255, 255, 255, 255), font=font)
    img.save(out_dir / "subtitle_strip.png", "PNG")

    # 角色名标签 character_label
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    label_x = W - 220
    label_y = 40
    draw.rounded_rectangle(
        [(label_x, label_y), (label_x + 180, label_y + 60)],
        radius=12, fill=(40, 40, 60, 200), outline=(255, 255, 255, 255), width=2,
    )
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except (IOError, OSError):
        font = ImageFont.load_default()
    draw.text((label_x + 18, label_y + 16), "学姐 + 学妹", fill=(255, 255, 255, 255), font=font)
    img.save(out_dir / "character_label.png", "PNG")


# ---------------------------------------------------------------------------
# Stage C：VLM 七维度评分（与 ab_score.py 完全同口径）
# ---------------------------------------------------------------------------

def extract_frames(video: Path, n: int = 24) -> list:
    from PIL import Image
    dur = probe_duration(video) if video.exists() else 60.0
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


def call_vlm(prompt: str, image_b64s: list[str], *,
             timeout: int = 300, max_retries: int = 3) -> dict:
    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "temperature": 0.4,
        "max_tokens": 4000,
    }
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
            "User-Agent": "Mozilla/5.0",
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
    """从 VLM 输出 markdown 解析 7 维度整数分（与 ab_score.py 解析逻辑一致）。"""
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


def score_v3(ref_video: Path, v3_video: Path, qa_dir: Path) -> dict:
    """VLM 七维度评分（与 ab_score.py 同口径）。"""
    qa_dir.mkdir(parents=True, exist_ok=True)
    print(f"[vlm] 抽参考视频 24 帧 ...", flush=True)
    ref_frames = extract_frames(ref_video, 24)
    print(f"[vlm] 抽 v3 24 帧 ...", flush=True)
    v3_frames = extract_frames(v3_video, 24)

    ref_b64 = filmstrip_b64(ref_frames, labels=False, target_kb=300, prefix=False)
    ref_fs = qa_dir / "filmstrip_ref_24.jpg"
    ref_fs.write_bytes(base64.b64decode(ref_b64))

    v3_b64 = filmstrip_b64(v3_frames, labels=False, target_kb=300, prefix=False)
    v3_fs = qa_dir / "filmstrip_v3_24.jpg"
    v3_fs.write_bytes(base64.b64decode(v3_b64))

    print(f"[vlm] 调 VLM ({VLM_MODEL}) ...", flush=True)
    resp = call_vlm(VLM_PROMPT, [ref_b64, v3_b64])
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

    md_path = qa_dir / "vlm_report_v3.md"
    md = [
        "# v3 VLM 七维度评分报告（对比参考视频）",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模型：`{VLM_MODEL}`",
        f"- 端点：`{VLM_API_URL}`",
        f"- 参考视频：`{ref_video}`",
        f"- 待评估：`{v3_video}`",
        f"- filmstrip：参考 24 帧 / v3 24 帧（~2.5s 间隔）",
        f"- filmstrip 文件：`{ref_fs}` / `{v3_fs}`",
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
    md.append(f"| **综合加权** | **{weighted_score}** | {TOTAL_WEIGHT} | — |")
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

    sj_path = qa_dir / "vlm_scores_v3.json"
    sj_path.write_text(json.dumps({
        "ref_video": str(ref_video),
        "v3_video": str(v3_video),
        "dim_scores": scores,
        "weighted_score": weighted_score,
        "weights": WEIGHTS,
        "vlm_model": VLM_MODEL,
        "ref_filmstrip": str(ref_fs),
        "v3_filmstrip": str(v3_fs),
        "report_md": str(md_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[vlm] 评分 JSON: {sj_path}", flush=True)
    print(f"[vlm] v3 综合加权分: {weighted_score}", flush=True)
    return {
        "weighted_score": weighted_score,
        "dim_scores": scores,
        "report_md": str(md_path),
        "scores_json": str(sj_path),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips-dir", default=str(DEFAULT_CLIPS_DIR))
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--ref", default=str(DEFAULT_REF))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--qa", default=str(DEFAULT_QA))
    ap.add_argument("--overlays-dir", default=str(DEFAULT_OVERLAYS))
    ap.add_argument("--skip-concat", action="store_true")
    ap.add_argument("--skip-overlay", action="store_true")
    ap.add_argument("--skip-score", action="store_true")
    args = ap.parse_args(argv)

    clips_dir = Path(args.clips_dir)
    sb_path = Path(args.storyboard)
    ref = Path(args.ref)
    out = Path(args.out)
    qa = Path(args.qa)
    overlays_dir = Path(args.overlays_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    qa.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)

    storyboard = {}
    if sb_path.exists():
        storyboard = json.loads(sb_path.read_text(encoding="utf-8"))

    # 1. 拼接
    tmp_dir = clips_dir / "tmp_concat"
    shots = sorted(clips_dir.glob("shot0*.mp4"))
    if not shots and not args.skip_concat:
        print(f"ERROR: {clips_dir} 下无 shot*.mp4（请先跑 t2v_batch.py）", file=sys.stderr)
        return 2

    concat_video = clips_dir / "concat_no_overlay.mp4"
    if not args.skip_concat:
        concat_via_xfade(shots, concat_video, tmp_dir)
    elif not concat_video.exists():
        print(f"ERROR: --skip-concat 但 {concat_video} 不存在", file=sys.stderr)
        return 2

    # 2. overlay 烧录
    if not args.skip_overlay:
        ovr_meta = burn_overlays(concat_video, overlays_dir, storyboard, out)
    else:
        ovr_meta = {"ok": False, "skipped": True}

    # 3. VLM 评分
    if not args.skip_score and out.exists():
        vlm_meta = score_v3(ref, out, qa)
    else:
        vlm_meta = {"skipped": True}

    # 4. 报告
    report = {
        "pipeline_version": "v3.0",
        "concat_video": str(concat_video),
        "final_v3": str(out),
        "overlay_meta": ovr_meta,
        "vlm_meta": vlm_meta,
        "shots_count": len(shots),
        "duration_sec": probe_duration(out) if out.exists() else None,
        "size_bytes": out.stat().st_size if out.exists() else 0,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (out.parent / "final_meta.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # gate：v3 任务硬指标 ≥ 60
    weighted = vlm_meta.get("weighted_score") if isinstance(vlm_meta, dict) else None
    if weighted is None:
        print(f"[final] ⚠️  VLM 未返回分数（请检查 v3_final.log / vlm_report_v3.md）", flush=True)
        return 3
    elif weighted < 60:
        print(f"[final] ⚠️  v3 综合分 {weighted} < 60（任务硬指标不达标，报告需标红）", flush=True)
        return 1
    else:
        print(f"[final] ✅ v3 综合分 {weighted} >= 60（达标）", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())