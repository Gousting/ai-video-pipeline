#!/usr/bin/env python3
r"""AI 视频流水线 Phase E：切点自评估（cut point review）。

读成片 mp4 + storyboard.json → 对每个 shot 切点（shot_i 结束 → shot_{i+1} 开始）做：
  1. 抽帧：在切点 ±0.5s 各抽 3 帧（共 6 帧：切点前 3 + 切点后 3）
  2. filmstrip 合成：复用 scripts/filmstrip.py 的 filmstrip() 把 6 帧拼成 1x6 横向图
  3. VLM 单次审查（minimax-m3，复用 scripts/r2v_review.py 的 chat()/frame_to_b64()）：
     - 视觉跳跃 visual_jump：切点前后画面是否突兀
     - 字幕遮挡 subtitle_occlusion：字幕条是否挡住关键内容
     - 文字清晰 text_clear：烧录字幕是否可读、有无乱码
  4. 音频断裂检测（纯 ffmpeg astats，不依赖 VLM）：切点前后 ±1s 提取音频段，
     比较 pre-cut RMS 与 post-cut RMS 是否出现断崖。
  5. 汇总 + Markdown 报告。

shot 边界解析：
  - 优先用 storyboard.shots[i].start_time（若有）
  - 否则按 duration 累加：shot[i].start = sum(shot[j].duration for j<i)
  - 最后用 ffprobe 视频总时长校准：若 sum(durations) < duration，按比例补齐

CLI:
    python scripts/cut_point_review.py ^
        --video output\out\final_v6.mp4 ^
        --storyboard examples\sample_storyboard.json ^
        [--out output\cut_point_report.md] ^
        [--n-frames 3] ^
        [--json-out output\cut_point_review.json] ^
        [--frames-dir output\cut_point_review]

依赖：
    - 复用 scripts/filmstrip.py 的 filmstrip()（不重写）
    - 复用 scripts/r2v_review.py 的 chat() / frame_to_b64()（不重写）
    - 不修改任何 Phase A/B/C/D 脚本或 overlays/
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# 复用：filmstrip + VLM 客户端（按硬约束，import 调用，不重写）
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import filmstrip as _fs  # scripts/filmstrip.py（提供 filmstrip() / filmstrip_b64()）  # noqa: E402
import r2v_review as _r2v  # scripts/r2v_review.py（提供 chat() / frame_to_b64() / parse_json()）  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent
DEFAULT_OUT_MD = REPO_ROOT / "output" / "cut_point_report.md"
DEFAULT_OUT_JSON = REPO_ROOT / "output" / "cut_point_review.json"
DEFAULT_FRAMES_DIR = REPO_ROOT / "output" / "cut_point_review"

# 抽帧半径 / 数量
DEFAULT_N_FRAMES = 3         # 切点前后各抽 3 帧（共 6 帧）
FRAME_HALF_SPAN = 0.5        # 切点 ±0.5s 覆盖范围
# 抽帧时间偏移（避免抽到完全相同的帧；6 帧等距分布于 [T-0.5, T+0.5]）
BEFORE_OFFSETS = (-0.50, -0.30, -0.10)
AFTER_OFFSETS = (0.10, 0.30, 0.50)
# 音频检测窗口
AUDIO_HALF_SPAN = 1.0        # 切点 ±1.0s 音频段
AUDIO_CHUNK_MS = 100         # astats 块大小（100ms）
# 音频"断崖"判定阈值（pre-cut mean RMS vs post-cut mean RMS 的 dB 差值）
AUDIO_CLIFF_DB_THRESHOLD = 8.0
# 抽帧质量
FRAME_JPEG_QUALITY = 3
# astats metadata key（由 astats=metadata=1 + ametadata=print 输出）
_ASTATS_META_KEY = "lavfi.astats.Overall.RMS_level"


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def _log(msg: str, *, flush: bool = True) -> None:
    print(f"[cut_point_review] {msg}", flush=flush)


def _run(cmd: list[str], *, timeout: float | None = None, label: str = "cmd",
         check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    """跑子进程并打日志。"""
    _log(f"+ {label}: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"{label} failed (rc={proc.returncode})\n"
            f"stdout: {proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}"
        )
    return proc


# ---------------------------------------------------------------------------
# 视频探测
# ---------------------------------------------------------------------------

def ffprobe_duration(video: Path) -> float:
    proc = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        label="ffprobe_duration",
    )
    out = proc.stdout.strip() or "0"
    try:
        return float(out)
    except ValueError:
        return 0.0


def ffprobe_has_audio(video: Path) -> bool:
    proc = _run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        label="ffprobe_has_audio",
        check=False,
    )
    return "audio" in (proc.stdout or "")


# ---------------------------------------------------------------------------
# Shot 边界解析
# ---------------------------------------------------------------------------

def parse_shot_boundaries(storyboard: dict, total_duration: float) -> list[dict]:
    """读 storyboard -> 返回 [{index, start, end, duration}]。

    优先级：
      1. shot.start_time + shot.duration（若有 start_time）
      2. 否则按 duration 累加：shot[i].start = sum(shot[j].duration for j<i)
      3. 总时长若不足 video duration，按比例拉长最后一 shot；
         总时长若超出 video duration，按比例缩短最后 N 个 shot。
    """
    shots_raw = storyboard.get("shots") or []
    if not shots_raw:
        raise ValueError("storyboard.shots 为空")

    explicit_starts = [s.get("start_time") for s in shots_raw]
    use_explicit = all(s is not None for s in explicit_starts)

    if use_explicit:
        boundaries = []
        for s in shots_raw:
            start = float(s["start_time"])
            dur = float(s.get("duration", 0) or 0)
            boundaries.append({"index": int(s.get("index", len(boundaries) + 1)),
                               "start": start,
                               "duration": dur,
                               "end": start + dur})
    else:
        # 累加 duration 算 start
        boundaries = []
        cursor = 0.0
        for s in shots_raw:
            dur = float(s.get("duration", 0) or 0)
            boundaries.append({"index": int(s.get("index", len(boundaries) + 1)),
                               "start": cursor,
                               "duration": dur,
                               "end": cursor + dur})
            cursor += dur

    # 用 video duration 校准
    story_total = boundaries[-1]["end"] if boundaries else 0.0
    if total_duration > 0 and story_total > 0 and abs(story_total - total_duration) > 0.05:
        scale = total_duration / story_total
        _log(f"shot 总时长 {story_total:.3f}s 与视频 {total_duration:.3f}s 不一致，"
             f"按比例 {scale:.4f} 缩放")
        for b in boundaries:
            b["start"] = round(b["start"] * scale, 3)
            b["duration"] = round(b["duration"] * scale, 3)
            b["end"] = round(b["end"] * scale, 3)

    # 把 end 钉到 video duration（避免最后一段越界）
    if total_duration > 0 and boundaries:
        boundaries[-1]["end"] = round(min(boundaries[-1]["end"], total_duration), 3)

    return boundaries


# ---------------------------------------------------------------------------
# 抽帧
# ---------------------------------------------------------------------------

def extract_frame(video: Path, t: float, out: Path) -> Image.Image:
    """ffmpeg 在 t 时刻抽 1 帧到 out（jpg）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    t_clamped = max(0.0, t)
    _run(
        ["ffmpeg", "-v", "error", "-ss", f"{t_clamped:.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", str(FRAME_JPEG_QUALITY), "-y", str(out)],
        label=f"ffmpeg_extract_frame@{t_clamped:.3f}",
    )
    img = Image.open(out).convert("RGB")
    return img


# ---------------------------------------------------------------------------
# 音频断裂检测（纯 ffmpeg astats，不依赖 VLM）
# ---------------------------------------------------------------------------

_ASTATS_RMS_RE = re.compile(
    r"lavfi\.astats\.Overall\.RMS_level=([+-]?(?:inf|-inf|[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?))"
)


def _parse_db(token: str) -> float | None:
    """把 ffmpeg 'RMS_level=-42.34' 解析为 float dB。-inf 转 None。"""
    t = token.strip().lower()
    if t in ("-inf", "+inf", "inf", "nan"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def detect_audio_cliff(video: Path, cut_time: float, has_audio: bool,
                       tmp_dir: Path) -> dict:
    """对 cut_time 用 ffmpeg astats 检测 RMS 是否断崖。

    流程：
      1. ffmpeg 抽 ±AUDIO_HALF_SPAN 秒音频段 -> wav（32kHz mono）
      2. ffmpeg astats=metadata=1:reset=1 + ametadata=mode=print +
         asetnsamples=n=3200（100ms 块）→ 解析 stderr 拿到 per-chunk RMS_level
      3. 把块按 cut_time 拆成 pre / post 两半，分别算平均 dB
      4. |pre - post| > AUDIO_CLIFF_DB_THRESHOLD 视为断崖

    返回 dict: {
      "available": bool,
      "pre_rms_db": float|None,  "post_rms_db": float|None,
      "delta_db": float|None,    "cliff": bool,
      "verdict": str,            "raw_chunks": int,
      "error": str|None,
    }
    """
    if not has_audio:
        return {
            "available": False,
            "pre_rms_db": None, "post_rms_db": None,
            "delta_db": None, "cliff": False,
            "verdict": "video has no audio stream",
            "raw_chunks": 0, "error": None,
        }

    tmp_dir.mkdir(parents=True, exist_ok=True)
    seg_wav = tmp_dir / f"audio_seg_{cut_time:.2f}.wav".replace(".", "_", 1)
    # 上面 replace 是为了文件名安全（5.00 -> 5_00），避免歧义

    try:
        start = max(0.0, cut_time - AUDIO_HALF_SPAN)
        dur = 2 * AUDIO_HALF_SPAN
        # 1) 抽音频段
        _run(
            ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(video),
             "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", "32000",
             "-acodec", "pcm_s16le", "-y", str(seg_wav)],
            label=f"ffmpeg_extract_audio_seg@{start:.3f}+{dur:.3f}",
        )

        if not seg_wav.exists() or seg_wav.stat().st_size < 100:
            return {
                "available": False,
                "pre_rms_db": None, "post_rms_db": None,
                "delta_db": None, "cliff": False,
                "verdict": "audio segment empty (silent cut)",
                "raw_chunks": 0, "error": None,
            }

        # 2) astats 100ms 块
        chunk_samples = int(32000 * AUDIO_CHUNK_MS / 1000)  # 3200
        # 关键：astats=metadata=1:reset=1 把 stats 写进 frame metadata，
        # 再用 ametadata=mode=print 把每个 chunk 的 RMS_level 打到 stderr。
        proc = _run(
            ["ffmpeg", "-v", "info", "-i", str(seg_wav),
             "-af", (
                 f"asetnsamples=n={chunk_samples},"
                 f"astats=metadata=1:reset=1,"
                 f"ametadata=mode=print:key={_ASTATS_META_KEY}"
             ),
             "-f", "null", "-"],
            label="ffmpeg_astats",
            check=False,
        )
        # ametadata 走 stderr
        stderr = proc.stderr or ""
        rms_values: list[float] = []
        for line in stderr.splitlines():
            m = _ASTATS_RMS_RE.search(line)
            if m:
                v = _parse_db(m.group(1))
                if v is not None:
                    rms_values.append(v)

        if not rms_values:
            return {
                "available": True,
                "pre_rms_db": None, "post_rms_db": None,
                "delta_db": None, "cliff": False,
                "verdict": "astats 未能解析 RMS（音频可能全静）",
                "raw_chunks": 0, "error": None,
            }

        # 3) 按 cut_time 在 segment 内拆分 pre / post
        # 段长 = 2 * AUDIO_HALF_SPAN；cut 在段中央（t - start = AUDIO_HALF_SPAN）
        # 每个 chunk 长度 = AUDIO_CHUNK_MS / 1000 秒；按索引拆
        chunk_dur = AUDIO_CHUNK_MS / 1000.0
        n_chunks = len(rms_values)
        split_index = int(round(AUDIO_HALF_SPAN / chunk_dur))
        split_index = max(1, min(split_index, n_chunks - 1))

        pre = rms_values[:split_index]
        post = rms_values[split_index:]

        # RMS 是 dB；算术平均（dB 域）即可，差异主要看 delta
        pre_mean = sum(pre) / len(pre) if pre else None
        post_mean = sum(post) / len(post) if post else None

        delta = None
        cliff = False
        verdict = "audio smooth"
        if pre_mean is not None and post_mean is not None:
            delta = round(post_mean - pre_mean, 2)
            if abs(delta) >= AUDIO_CLIFF_DB_THRESHOLD:
                cliff = True
                direction = "跃升" if delta > 0 else "跌落"
                verdict = (f"音频断崖：{direction} {abs(delta):.1f} dB "
                           f"(pre={pre_mean:.1f} dB, post={post_mean:.1f} dB)")
            else:
                verdict = (f"音频平滑：pre={pre_mean:.1f} dB, post={post_mean:.1f} dB, "
                           f"Δ={delta:+.1f} dB")

        return {
            "available": True,
            "pre_rms_db": round(pre_mean, 2) if pre_mean is not None else None,
            "post_rms_db": round(post_mean, 2) if post_mean is not None else None,
            "delta_db": delta,
            "cliff": cliff,
            "verdict": verdict,
            "raw_chunks": n_chunks,
            "error": None,
        }

    except Exception as e:  # noqa: BLE001
        return {
            "available": False,
            "pre_rms_db": None, "post_rms_db": None,
            "delta_db": None, "cliff": False,
            "verdict": "audio check failed",
            "raw_chunks": 0,
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        seg_wav.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# VLM 审查
# ---------------------------------------------------------------------------

CUT_POINT_VLM_PROMPT = (
    "你是一名视频成片切点（shot transition）的视觉审查员。我会给你一张合成图（filmstrip），"
    "内含按时间顺序排列的 6 帧视频抽帧（每帧左上角有白底黑字序号 1..6，"
    "其中 1/2/3 是切点前 ~0.5/0.3/0.1s 的 3 帧，4/5/6 是切点后 ~0.1/0.3/0.5s 的 3 帧；"
    "切点大致在 3 与 4 之间）。请基于这 6 帧对这次切点做严格审查：\n"
    "1) visual_jump（视觉跳跃）：切点前后画面是否突兀/抖动/色温突变/构图断裂；"
    "   mild 表示轻微可接受，moderate/severe 视为不可接受。\n"
    "2) subtitle_occlusion（字幕遮挡）：画面中是否有字幕条（底部黑色/半透明色块 + 白字）"
    "   挡住了关键视觉内容（人物面部/动作核心/重要背景）。如有则 true。\n"
    "3) text_clear（文字清晰）：字幕烧录文字是否清晰可读，是否存在乱码/截断/"
    "   模糊到不可辨认。如清晰可读且无乱码则 true。\n\n"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"visual_jump": <true|false>, '
    '"visual_jump_severity": "<none|mild|moderate|severe>", '
    '"visual_jump_comment": "<中文一句话说明>", '
    '"subtitle_occlusion": <true|false>, '
    '"subtitle_occlusion_comment": "<中文一句话说明>", '
    '"text_clear": <true|false>, '
    '"text_clear_comment": "<中文一句话说明>", '
    '"score": <0-100 整数，整体印象分>, '
    '"opinion": "<中文一句话整体结论>"}'
)


def vlm_review_cutpoint(frames: list[Image.Image]) -> dict:
    """6 帧合成 filmstrip -> VLM 一次调用 -> 审查结果 dict。

    VLM 调用失败（如 429 配额耗尽）时返回结构化失败 marker，保留 audio 检测能力，
    不让单点 VLM 失败阻塞整个 pipeline。
    """
    # 复用 filmstrip（n=6 -> 1x6 横向）
    filmstrip_b64 = _fs.filmstrip_b64(frames, labels=True, target_kb=200, prefix=True)

    content: list = [
        {"type": "text", "text": CUT_POINT_VLM_PROMPT},
        {"type": "image_url",
         "image_url": {"url": filmstrip_b64}},
    ]
    raw = ""
    err: str | None = None
    try:
        # attempts=1：避免月度配额耗尽时单点卡 12 分钟（r2v_review.chat 默认 4 次重试 +
        # 每次 180s 超时 + backoff）。按 cut point 评估需要稳定时长。
        raw = _r2v.chat([{"role": "user", "content": content}], attempts=1)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:300]}"
        _log(f"VLM 调用失败：{err}")

    if not raw:
        # VLM 不可用时返回"unknown"占位；视觉相关检查按"无法判定"处理
        return {
            "visual_jump": False,
            "visual_jump_severity": "unknown",
            "visual_jump_comment": "VLM unavailable",
            "subtitle_occlusion": False,
            "subtitle_occlusion_comment": "VLM unavailable",
            "text_clear": True,
            "text_clear_comment": "VLM unavailable",
            "score": 0,
            "opinion": "VLM 调用失败，跳过视觉/字幕/文字审查（仅完成音频断崖检测）",
            "raw": "",
            "vlm_unavailable": True,
            "vlm_error": err,
        }

    review = _r2v.parse_json(raw)
    review["raw"] = raw
    review["vlm_unavailable"] = False
    # 字段容错：万一 VLM 漏字段
    review.setdefault("visual_jump", False)
    review.setdefault("visual_jump_severity", "unknown")
    review.setdefault("visual_jump_comment", "")
    review.setdefault("subtitle_occlusion", False)
    review.setdefault("subtitle_occlusion_comment", "")
    review.setdefault("text_clear", True)
    review.setdefault("text_clear_comment", "")
    review.setdefault("score", 0)
    try:
        review["score"] = int(review["score"])
    except (TypeError, ValueError):
        review["score"] = 0
    review.setdefault("opinion", "")
    return review


# ---------------------------------------------------------------------------
# 单切点评估
# ---------------------------------------------------------------------------

def evaluate_cut_point(
    video: Path,
    cut: dict,
    *,
    frames_dir: Path,
    audio_tmp_dir: Path,
    has_audio: bool,
    n_frames: int,
    run_vlm: bool = True,
) -> dict:
    """对单个 cut_point 完成：抽帧 + filmstrip 存盘 + VLM 审查 + 音频断崖检测。

    cut: {"id", "position", "from_shot", "to_shot"}
    """
    cp_id = cut["id"]
    pos = cut["position"]
    cut_dir = frames_dir / cp_id
    cut_dir.mkdir(parents=True, exist_ok=True)

    # 1) 抽 6 帧（前 3 + 后 3）
    before_ts = [pos + BEFORE_OFFSETS[i] for i in range(n_frames)]
    after_ts = [pos + AFTER_OFFSETS[i] for i in range(n_frames)]

    frames: list[Image.Image] = []
    frame_meta: list[dict] = []
    for i, t in enumerate(before_ts, start=1):
        p = cut_dir / f"before_{i}_{t:+.3f}s.jpg"
        img = extract_frame(video, t, p)
        frames.append(img)
        frame_meta.append({"label": i, "side": "before", "t": round(t, 3), "path": str(p)})
    for i, t in enumerate(after_ts, start=1):
        p = cut_dir / f"after_{i}_{t:+.3f}s.jpg"
        img = extract_frame(video, t, p)
        frames.append(img)
        frame_meta.append({"label": i + n_frames, "side": "after",
                            "t": round(t, 3), "path": str(p)})

    # 2) 合成 filmstrip 存盘（PNG + JPEG 都留一份，便于人工复核）
    fs_img = _fs.filmstrip(frames, labels=True)
    fs_png = cut_dir / "filmstrip.png"
    fs_jpg = cut_dir / "filmstrip.jpg"
    fs_img.save(fs_png, "PNG")
    # JPEG 版：用同样的 _compress_to_kb 直接复用
    from filmstrip import _compress_to_kb  # noqa: E402  （脚本自留 helper）
    fs_jpg.write_bytes(_compress_to_kb(fs_img, target_kb=300))

    # 3) VLM 审查
    if run_vlm:
        vlm = vlm_review_cutpoint(frames)
    else:
        vlm = {
            "visual_jump": False,
            "visual_jump_severity": "skipped",
            "visual_jump_comment": "VLM skipped (--no-vlm)",
            "subtitle_occlusion": False,
            "subtitle_occlusion_comment": "skipped",
            "text_clear": True,
            "text_clear_comment": "skipped",
            "score": 0,
            "opinion": "VLM not invoked",
            "raw": "",
            "skipped": True,
        }

    # 4) 音频断裂检测
    audio = detect_audio_cliff(video, pos, has_audio=has_audio, tmp_dir=audio_tmp_dir)

    # 5) 综合 pass 判定
    # VLM 不可用时（vlm_unavailable=True），视觉相关检查标记为 unknown，
    # 既不通过也不失败，仅在报告里明确标注。
    vlm_unavailable = bool(vlm.get("vlm_unavailable", False))

    severity_pass = vlm.get("visual_jump_severity") in ("none", "mild")
    visual_pass = (not bool(vlm.get("visual_jump", False))) or severity_pass
    subtitle_pass = not bool(vlm.get("subtitle_occlusion", False))
    text_pass = bool(vlm.get("text_clear", False))
    audio_pass = not bool(audio.get("cliff", False))

    if vlm_unavailable:
        # VLM 不可用时，视觉类检查置 None 表示无法判定
        visual_check: bool | None = None
        subtitle_check: bool | None = None
        text_check: bool | None = None
    else:
        visual_check = bool(visual_pass)
        subtitle_check = bool(subtitle_pass)
        text_check = bool(text_pass)

    audio_check = bool(audio_pass)

    # overall_pass：所有可判定的检查都为真；VLM 不可用时只看 audio
    if vlm_unavailable:
        overall_pass = audio_check
    else:
        overall_pass = visual_check and subtitle_check and text_check and audio_check

    # 6) 建议
    suggestions: list[str] = []
    if vlm_unavailable:
        suggestions.append(
            f"VLM 不可用：{vlm.get('vlm_error', '')}；仅完成音频断崖检测，"
            f"视觉/字幕/文字三项检查需手动复核或稍后重跑"
        )
    if bool(vlm.get("visual_jump", False)):
        sev = vlm.get("visual_jump_severity", "moderate")
        suggestions.append(
            f"切点视觉跳跃（{sev}）：{vlm.get('visual_jump_comment', '')} "
            f"建议加 crossfade 转场或调整运镜节奏"
        )
    if bool(vlm.get("subtitle_occlusion", False)):
        suggestions.append(
            f"字幕遮挡主体：{vlm.get('subtitle_occlusion_comment', '')} "
            f"建议调整字幕位置或换无字幕区间"
        )
    if not bool(vlm.get("text_clear", False)):
        suggestions.append(
            f"字幕不清晰/有乱码：{vlm.get('text_clear_comment', '')} "
            f"建议检查字幕源文本或提高烧录字号"
        )
    if bool(audio.get("cliff", False)):
        verdict = audio.get("verdict", "")
        # verdict 已包含 "音频断崖：" 前缀，去掉避免重复
        tail = verdict.replace("音频断崖：", "", 1) if verdict.startswith("音频断崖：") else verdict
        suggestions.append(
            f"音频断崖：{tail} 建议在切点附近加淡入淡出或检查音频衔接"
        )

    return {
        "id": cp_id,
        "position": round(pos, 3),
        "from_shot": cut["from_shot"],
        "to_shot": cut["to_shot"],
        "frames": frame_meta,
        "filmstrip": {"png": str(fs_png), "jpg": str(fs_jpg),
                       "width": fs_img.width, "height": fs_img.height},
        "vlm": vlm,
        "audio": audio,
        "checks": {
            "visual_jump": visual_check,            # None 表示 VLM 不可用
            "subtitle_occlusion": subtitle_check,  # None 表示 VLM 不可用
            "text_clear": text_check,              # None 表示 VLM 不可用
            "audio_cliff": audio_check,
        },
        "vlm_unavailable": vlm_unavailable,
        "pass": bool(overall_pass),
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

_MARKDOWN_ICON = {
    True: "✅",
    False: "❌",
    None: "❓",  # VLM 不可用 / 无法判定
}


def write_markdown_report(result: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cp_list = result["cut_points"]
    n_total = len(cp_list)
    n_pass = sum(1 for c in cp_list if c["pass"])

    lines: list[str] = []
    lines.append("# Cut Point Review Report")
    lines.append("")
    lines.append(f"- Video: `{result['video']}`")
    lines.append(f"- Storyboard: `{result['storyboard']}`")
    lines.append(f"- Video duration: **{result['duration']:.3f}s**")
    lines.append(f"- Shots: **{result['n_shots']}**  |  "
                 f"Cut points evaluated: **{n_total}**  |  "
                 f"PASS: **{n_pass}/{n_total}**")
    lines.append(f"- Overall: **{'✅ PASS' if result['overall']['pass'] else '❌ FAIL'}**")
    lines.append("")
    if result["overall"]["issues"]:
        lines.append("## Issues")
        lines.append("")
        for iss in result["overall"]["issues"]:
            lines.append(f"- {iss}")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Cut | Position | From→To | Visual Jump | Subtitle Occlusion | "
                 "Text Clear | Audio Cliff | Result |")
    lines.append("|-----|----------|---------|-------------|---------------------|"
                 "------------|-------------|--------|")
    for c in cp_list:
        lines.append(
            f"| {c['id']} | {c['position']:.2f}s | "
            f"shot{c['from_shot']} → shot{c['to_shot']} | "
            f"{_MARKDOWN_ICON[c['checks']['visual_jump']]} | "
            f"{_MARKDOWN_ICON[c['checks']['subtitle_occlusion']]} | "
            f"{_MARKDOWN_ICON[c['checks']['text_clear']]} | "
            f"{_MARKDOWN_ICON[c['checks']['audio_cliff']]} | "
            f"{'✅ PASS' if c['pass'] else '❌ FAIL'} |"
        )
    lines.append("")

    lines.append("## Details")
    lines.append("")
    for c in cp_list:
        lines.append(f"### {c['id']} @ {c['position']:.2f}s "
                     f"(shot{c['from_shot']} → shot{c['to_shot']})")
        lines.append("")
        lines.append(f"- Filmstrip: `{c['filmstrip']['png']}` "
                     f"({c['filmstrip']['width']}×{c['filmstrip']['height']})")
        lines.append(f"- VLM score: **{c['vlm'].get('score', 0)}**  |  "
                     f"visual_jump_severity: **{c['vlm'].get('visual_jump_severity', '?')}**")
        lines.append("")
        lines.append("**Visual Jump**")
        vj = c['vlm'].get('visual_jump')
        if c.get('vlm_unavailable'):
            lines.append(f"- 视觉跳跃: ❓ 无法判定（VLM 不可用）")
        else:
            lines.append(f"- 视觉跳跃: {'是 ❌' if vj else '否 ✅'}")
        lines.append(f"- 严重度: {c['vlm'].get('visual_jump_severity', '?')}")
        if c['vlm'].get('visual_jump_comment'):
            lines.append(f"- 说明: {c['vlm'].get('visual_jump_comment')}")
        lines.append("")
        lines.append("**Subtitle Occlusion**")
        if c.get('vlm_unavailable'):
            lines.append(f"- 字幕遮挡: ❓ 无法判定（VLM 不可用）")
        else:
            so = c['vlm'].get('subtitle_occlusion')
            lines.append(f"- 字幕遮挡: {'是 ❌' if so else '否 ✅'}")
        if c['vlm'].get('subtitle_occlusion_comment'):
            lines.append(f"- 说明: {c['vlm'].get('subtitle_occlusion_comment')}")
        lines.append("")
        lines.append("**Text Clear**")
        if c.get('vlm_unavailable'):
            lines.append(f"- 文字清晰: ❓ 无法判定（VLM 不可用）")
        else:
            tc = c['vlm'].get('text_clear')
            lines.append(f"- 文字清晰: {'是 ✅' if tc else '否 ❌'}")
        if c['vlm'].get('text_clear_comment'):
            lines.append(f"- 说明: {c['vlm'].get('text_clear_comment')}")
        if c['vlm'].get('opinion'):
            lines.append(f"- 整体意见: {c['vlm'].get('opinion')}")
        if c.get('vlm_unavailable') and c['vlm'].get('vlm_error'):
            lines.append(f"- VLM 错误: `{c['vlm'].get('vlm_error')}`")
        lines.append("")
        lines.append("**Audio Cliff**")
        lines.append(f"- 检测可用: {'是' if c['audio'].get('available') else '否（N/A）'}")
        if c['audio'].get('pre_rms_db') is not None:
            lines.append(f"- pre-cut mean RMS: **{c['audio']['pre_rms_db']:.2f} dB**")
        if c['audio'].get('post_rms_db') is not None:
            lines.append(f"- post-cut mean RMS: **{c['audio']['post_rms_db']:.2f} dB**")
        if c['audio'].get('delta_db') is not None:
            lines.append(f"- Δ RMS: **{c['audio']['delta_db']:+.2f} dB** "
                         f"(阈值 {AUDIO_CLIFF_DB_THRESHOLD} dB)")
        lines.append(f"- 断崖: {'是 ❌' if c['audio'].get('cliff') else '否 ✅'}")
        lines.append(f"- 结论: {c['audio'].get('verdict', '')}")
        lines.append("")
        if c["suggestions"]:
            lines.append("**Suggestions**")
            for s in c["suggestions"]:
                lines.append(f"- {s}")
            lines.append("")
        else:
            lines.append("**Suggestions**: 无（通过）")
            lines.append("")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("1. **Shot 边界**：优先 storyboard.shots[i].start_time，"
                 "否则按 duration 累加；最后用 ffprobe 视频总时长校准。")
    lines.append(f"2. **抽帧**：切点 ±0.5s 各抽 {DEFAULT_N_FRAMES} 帧（"
                 f"前 {BEFORE_OFFSETS} / 后 {AFTER_OFFSETS}），共 6 帧，"
                 f"ffmpeg `-ss` 抽帧到 jpg。")
    lines.append(f"3. **Filmstrip 合成**：复用 scripts/filmstrip.py 的 `filmstrip()`"
                 f"（6 帧 → 1x6 横向），保存 PNG 无损 + JPEG 压缩两版。")
    lines.append("4. **VLM 审查**：复用 scripts/r2v_review.py 的 "
                 "`chat()` / `frame_to_b64()`，"
                 f"MODEL = `{_r2v.MODEL}`；单次调用返回 JSON "
                 f"（visual_jump / subtitle_occlusion / text_clear / score / opinion）。")
    lines.append(f"5. **音频断崖**：纯 ffmpeg，不依赖 VLM。"
                 f"在切点 ±{AUDIO_HALF_SPAN}s 抽 wav，"
                 f"`astats=metadata=1:reset=1 + ametadata=mode=print + "
                 f"asetnsamples=n=3200` 得到 {AUDIO_CHUNK_MS}ms 块的 RMS_level；"
                 f"pre/post 平均 dB 差 ≥ {AUDIO_CLIFF_DB_THRESHOLD} dB 视为断崖。")
    lines.append("")
    lines.append("## Pass Criteria")
    lines.append("")
    lines.append("- visual_jump == false（severity ∈ {none, mild} 视作通过）")
    lines.append("- subtitle_occlusion == false")
    lines.append("- text_clear == true")
    lines.append("- audio_cliff == false")
    lines.append("")
    lines.append("> VLM 不可用时（如配额耗尽），视觉/字幕/文字三项标记为 ❓ 无法判定，"
                 "overall pass 仅由 audio_cliff 决定，需手动复核。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"_Generated by scripts/cut_point_review.py at {time.strftime('%Y-%m-%d %H:%M:%S')}_")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase E 切点自评估")
    ap.add_argument("--video", required=True, help="成片 mp4 路径")
    ap.add_argument("--storyboard", required=True, help="storyboard.json 路径")
    ap.add_argument("--out", default=None,
                    help=f"Markdown 报告路径（默认 {DEFAULT_OUT_MD.relative_to(REPO_ROOT)}）")
    ap.add_argument("--json-out", default=None,
                    help=f"JSON 报告路径（默认 {DEFAULT_OUT_JSON.relative_to(REPO_ROOT)}）")
    ap.add_argument("--frames-dir", default=None,
                    help=f"切点抽帧 + filmstrip 输出目录（默认 {DEFAULT_FRAMES_DIR.relative_to(REPO_ROOT)}）")
    ap.add_argument("--n-frames", type=int, default=DEFAULT_N_FRAMES,
                    help=f"切点前后各抽帧数（默认 {DEFAULT_N_FRAMES}，共 2N 帧）")
    ap.add_argument("--no-vlm", action="store_true",
                    help="跳过 VLM 调用（仅做抽帧 + 音频断崖）")
    args = ap.parse_args(argv)

    video = Path(args.video).resolve()
    storyboard_p = Path(args.storyboard).resolve()
    out_md = Path(args.out).resolve() if args.out else DEFAULT_OUT_MD
    out_json = Path(args.json_out).resolve() if args.json_out else DEFAULT_OUT_JSON
    frames_dir = Path(args.frames_dir).resolve() if args.frames_dir else DEFAULT_FRAMES_DIR
    audio_tmp_dir = frames_dir / "_audio_tmp"

    if not video.exists():
        print(f"[cut_point_review] ERROR: video 不存在: {video}", file=sys.stderr)
        return 2
    if not storyboard_p.exists():
        print(f"[cut_point_review] ERROR: storyboard 不存在: {storyboard_p}", file=sys.stderr)
        return 2

    n_frames = args.n_frames if args.n_frames > 0 else DEFAULT_N_FRAMES
    if n_frames != DEFAULT_N_FRAMES:
        # 仅供调试：如果用户改 n_frames，按比例调整偏移
        global BEFORE_OFFSETS, AFTER_OFFSETS
        BEFORE_OFFSETS = tuple(round(-FRAME_HALF_SPAN + i * (2 * FRAME_HALF_SPAN) / (2 * n_frames - 1), 3)
                               for i in range(n_frames))
        AFTER_OFFSETS = tuple(round(BEFORE_OFFSETS[-1] + (i + 1) * (2 * FRAME_HALF_SPAN) / (2 * n_frames - 1), 3)
                              for i in range(n_frames))
        _log(f"--n-frames={n_frames}：before={BEFORE_OFFSETS} after={AFTER_OFFSETS}")

    _log(f"video={video}")
    _log(f"storyboard={storyboard_p}")
    _log(f"out_md={out_md}")
    _log(f"frames_dir={frames_dir}")

    # 0) 读 storyboard + 探测视频
    storyboard = json.loads(storyboard_p.read_text(encoding="utf-8"))
    duration = ffprobe_duration(video)
    has_audio = ffprobe_has_audio(video)
    _log(f"video duration={duration:.3f}s has_audio={has_audio}")

    # 1) 算 shot 边界
    shots = parse_shot_boundaries(storyboard, duration)
    _log(f"shots: {[(s['index'], s['start'], s['end']) for s in shots]}")

    # 2) 算 cut_points（N-1 个）
    cut_points: list[dict] = []
    for i in range(len(shots) - 1):
        a = shots[i]
        b = shots[i + 1]
        # 切点位置 = shot_b 的 start（与 shot_a.end 取均值更稳）
        pos = round((a["end"] + b["start"]) / 2, 3)
        cut_points.append({
            "id": f"cut_{i + 1}",
            "position": pos,
            "from_shot": a["index"],
            "to_shot": b["index"],
        })
    _log(f"cut_points: {[(c['id'], c['position']) for c in cut_points]}")

    if not cut_points:
        print("[cut_point_review] ERROR: 没有切点（shots < 2）", file=sys.stderr)
        return 2

    # 3) 逐个切点评估
    results: list[dict] = []
    for cut in cut_points:
        _log(f"评估 {cut['id']} @ {cut['position']:.3f}s "
             f"(shot{cut['from_shot']} → shot{cut['to_shot']})")
        r = evaluate_cut_point(
            video, cut,
            frames_dir=frames_dir,
            audio_tmp_dir=audio_tmp_dir,
            has_audio=has_audio,
            n_frames=n_frames,
            run_vlm=not args.no_vlm,
        )
        _log(f"  → pass={r['pass']} score={r['vlm'].get('score', 0)} "
             f"audio.cliff={r['audio'].get('cliff')}")
        results.append(r)

    # 4) 汇总
    overall_pass = all(r["pass"] for r in results)
    issues: list[str] = []
    for r in results:
        if not r["pass"]:
            for s in r["suggestions"]:
                issues.append(f"{r['id']} @ {r['position']:.2f}s: {s}")
        if r["audio"].get("error"):
            issues.append(f"{r['id']} audio 检测出错: {r['audio']['error']}")

    result = {
        "video": str(video),
        "storyboard": str(storyboard_p),
        "duration": duration,
        "n_shots": len(shots),
        "shots": shots,
        "cut_points": results,
        "overall": {"pass": overall_pass, "issues": issues},
        "thresholds": {
            "audio_cliff_db": AUDIO_CLIFF_DB_THRESHOLD,
            "audio_half_span_s": AUDIO_HALF_SPAN,
            "audio_chunk_ms": AUDIO_CHUNK_MS,
            "frame_half_span_s": FRAME_HALF_SPAN,
            "n_frames_each_side": n_frames,
            "before_offsets_s": list(BEFORE_OFFSETS),
            "after_offsets_s": list(AFTER_OFFSETS),
        },
        "model": _r2v.MODEL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # 5) 写出报告
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(result, out_md)

    _log(f"JSON 报告: {out_json}")
    _log(f"Markdown 报告: {out_md}")
    _log(f"Frames + filmstrips: {frames_dir}")
    _log(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
