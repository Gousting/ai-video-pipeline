#!/usr/bin/env python3
"""AI 视频流水线 Phase C：overlay 烧录合成。

读 storyboard.json → 检查/渲染 overlay → 用 ffmpeg overlay filter 把 title-card /
subtitle-bar / end-card 烧到主视频流上 → 输出 video_with_overlay.mp4（暂不混音，
留出 audio 阶段的接接点）。

输入模式：
  - 单段视频：--video output/out/video_silent_v5.mp4
  - 多段拼接：--videos shot1.mp4 --videos shot2.mp4 --videos shot3.mp4
              （按 storyboard.shots[].duration 拼接，无 acrossfade；想要 acrossfade 请先拼）

overlay 缺失策略：
  - 顶层 overlays / per-shot overlay 字段全部走 Phase B 的 render_overlays.py。
  - sample_storyboard.json 没有 overlay 字段 → 默认 --auto-overlay 自动注入 demo
    （title-card + 每个有 narration 的 shot 的 subtitle-bar + end-card）。
  - 任何一个 overlay 文件缺失 → 调一次 render_overlays.py（同一 storyboard）补齐。

overlay 烧录策略：
  - 片头 title-card（顶层，duration 来自 overlay.duration 或 4.0s 兜底）：
      enable='between(t, 0, title_duration)'
  - 每个 shot 的 subtitle-bar（默认 shot-XX-subtitle-bar.png）：
      enable='between(t, shot_start, shot_end)'（shot 边界取自 storyboard）
  - 片尾 end-card（顶层，duration 同上）：
      enable='between(t, video_duration - end_duration, video_duration)'
  - 滤镜链顺序：主视频 → per-shot subtitle-bars → title-card → end-card
    （title-card / end-card 全屏覆盖，自然盖住字幕；避免片头/片尾期间字幕残留遮挡视觉）

音频处理：
  - 主视频的音频默认 -map 0:a? 直通（不动 audio 阶段的混音输入）。
  - 叠加层文件（title-card.mp4 / end-card.mp4）若带音频，ffmpeg 不会主动 map，自动丢弃。
  - 想完全无声：--no-audio。
  - 想替换主视频音频：预留 --audio-source 占位（默认 None，本期不实现）。

验证：
  - 默认 --verify，开关跑 ffprobe + 关键帧抽样 + PIL 像素统计
    （片头有 title-card 亮像素、字幕条区域 alpha>0 且 RGB 低亮、片尾卡亮像素）。
  - 抽帧图存到 <out>.verify/<t:05.2f>s.jpg，便于人工复核。

CLI：
    python scripts/compose_final.py \
        --storyboard examples/sample_storyboard.json \
        --video output/out/video_silent_v5.mp4 \
        --out output/out/video_with_overlay.mp4 \
        [--overlays-dir output/overlays_e2e]
    python scripts/compose_final.py \
        --storyboard examples/sample_storyboard.json \
        --videos output/clips_v5/shot1.mp4 \
        --videos output/clips_v5/shot2.mp4 \
        --videos output/clips_v5/shot3.mp4 \
        --out output/out/video_with_overlay.mp4 \
        --overlays-dir output/overlays_e2e

报告写到 <out>.verify/verify_report.json，含每个验证点 (time / label / pass / metrics)。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# 默认 overlay 输出目录（与 render_overlays.py 对齐）
DEFAULT_OVERLAYS_DIR = REPO_ROOT / "output" / "overlays"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _log(msg: str, *, flush: bool = True) -> None:
    print(f"[compose_final] {msg}", flush=flush)


def run_subprocess(
    cmd: list[str],
    *,
    timeout: float | None = None,
    label: str = "cmd",
    check: bool = True,
) -> subprocess.CompletedProcess:
    """跑子进程并打日志。出错时抛 RuntimeError。"""
    _log(f"+ {label}: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-3000:]
        raise RuntimeError(f"{label} 失败 rc={proc.returncode}\n{tail}")
    return proc


def ffprobe_json(path: Path) -> dict:
    """ffprobe -of json 拿完整元数据。"""
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,"
            "r_frame_rate,sample_aspect_ratio,duration,nb_frames,channels,sample_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败 rc={r.returncode}: {r.stderr[-500:]}")
    return json.loads(r.stdout)


def ffprobe_video_stream(path: Path) -> dict | None:
    info = ffprobe_json(path)
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def ffprobe_audio_stream(path: Path) -> dict | None:
    info = ffprobe_json(path)
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            return s
    return None


def ffprobe_duration(path: Path) -> float:
    info = ffprobe_json(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# overlay 字段提取
# ---------------------------------------------------------------------------


def has_overlay_definitions(storyboard: dict) -> bool:
    """storyboard 顶层 overlays[] 或任意 shot 的 overlay 字段是否非空。"""
    if storyboard.get("overlays"):
        return True
    for shot in storyboard.get("shots", []) or []:
        if shot.get("overlay"):
            return True
    return False


def _normalize_overlay_field(raw: object) -> dict | None:
    """shot.overlay 可能是 dict 或字符串；统一成 dict 或 None。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return {"engine": raw, "template": "subtitle-bar"}
    return None


def collect_overlay_plan(storyboard: dict, overlays_dir: Path) -> dict:
    """把 storyboard 的 overlay 定义汇总成 plan：每个 plan 条目对应一个待烧录 overlay。

    返回结构：
      {
        "title_card": {"engine": ..., "template": ..., "duration": float,
                       "source": str (overlay 文件路径), "exists": bool},
        "end_card":   {...},
        "subtitles":  [{"shot_index": int, "engine": ..., "template": ...,
                        "duration": float, "start": float, "end": float,
                        "source": str, "exists": bool}, ...],
        "composition": {"width": int, "height": int},
      }

    自动注入规则：sample_storyboard.json 没 overlay 字段时，按 narration 自动生成
    title-card + 每个 shot 的 subtitle-bar + end-card（与 render_overlays.build_demo_storyboard
    对齐，方便复用 manifest.json）。
    """
    overlays_dir = Path(overlays_dir)

    # 1. 顶层 overlays：扫一遍找出 title-card / end-card
    title_card: dict | None = None
    end_card: dict | None = None
    for ov in storyboard.get("overlays", []) or []:
        tmpl = (ov.get("template") or ov.get("id") or "").lower()
        if "title" in tmpl and title_card is None:
            title_card = ov
        elif "end" in tmpl and end_card is None:
            end_card = ov

    # 2. 顶层没有就走 demo 注入
    injected_demo = False
    if title_card is None and end_card is None:
        title_card = {
            "id": "title-card",
            "engine": "hyperframes",
            "template": "title-card",
            "data": {
                "title": storyboard.get("title", "Untitled"),
                "subtitle": "Phase C · Compose",
                "tagline": "OVERLAY BURN-IN",
            },
            "duration": 4.0,
        }
        end_card = {
            "id": "end-card",
            "engine": "hyperframes",
            "template": "end-card",
            "data": {
                "title": storyboard.get("title", "Untitled"),
                "credit": "Rendered by ai-video-pipeline / Phase C",
                "tagline": "END",
            },
            "duration": 4.0,
        }
        injected_demo = True

    # 3. 每个 shot 的字幕条（按 shot.duration 计算 start/end）
    subtitles: list[dict] = []
    cumulative = 0.0
    for shot in storyboard.get("shots", []) or []:
        idx = shot.get("index")
        duration = float(shot.get("duration", 0.0))
        narration = shot.get("narration", "")
        if not narration:
            cumulative += duration
            continue
        ov = _normalize_overlay_field(shot.get("overlay"))
        if ov is None and injected_demo:
            ov = {"engine": "pil", "template": "subtitle-bar"}
        if ov is None:
            cumulative += duration
            continue
        subtitles.append(
            {
                "shot_index": idx,
                "engine": ov.get("engine", "pil"),
                "template": ov.get("template", "subtitle-bar"),
                "duration": duration,
                "start": cumulative,
                "end": cumulative + duration,
                "narration": narration,
            }
        )
        cumulative += duration

    plan = {
        "injected_demo": injected_demo,
        "title_card": _overlay_to_entry(title_card, overlays_dir, kind="title-card")
        if title_card
        else None,
        "end_card": _overlay_to_entry(end_card, overlays_dir, kind="end-card")
        if end_card
        else None,
        "subtitles": [
            _subtitle_to_entry(s, overlays_dir) for s in subtitles
        ],
        "composition": {"width": 1920, "height": 1080},
        "shot_count": len(storyboard.get("shots", []) or []),
        "storyboard_total_duration": cumulative,
    }
    return plan


def _overlay_to_entry(ov: dict, overlays_dir: Path, *, kind: str) -> dict:
    """把顶层 overlays[] 元素转成 plan entry + 期望文件路径。"""
    engine = ov.get("engine", "pil")
    template = ov.get("template", kind)
    duration = float(ov.get("duration", 4.0))
    ext = ".mp4" if engine == "hyperframes" else ".png"
    expected = overlays_dir / f"{kind}.{ext.lstrip('.')}"
    return {
        "kind": kind,
        "engine": engine,
        "template": template,
        "duration": duration,
        "data": ov.get("data", {}),
        "expected_path": str(expected),
        "exists": expected.is_file(),
    }


def _subtitle_to_entry(sub: dict, overlays_dir: Path) -> dict:
    """把 subtitle 条目转成 plan entry + 期望文件路径。"""
    engine = sub["engine"]
    ext = ".mp4" if engine == "hyperframes" else ".png"
    expected = overlays_dir / f"shot-{int(sub['shot_index']):02d}-{sub['template']}.{ext.lstrip('.')}"
    return {
        **sub,
        "expected_path": str(expected),
        "exists": expected.is_file(),
    }


# ---------------------------------------------------------------------------
# 缺失 overlay 渲染（subprocess 调 render_overlays.py）
# ---------------------------------------------------------------------------


def ensure_overlays(
    storyboard: dict,
    storyboard_path: Path,
    overlays_dir: Path,
    *,
    fallback_engine: str = "pil",
) -> dict:
    """调用 render_overlays.py 把缺失的 overlay 渲出来。

    简化策略：直接调一次 render_overlays.py；它会写出 manifest.json 并覆盖现有产物。
    存在性检查交给上游（plan.exists），渲染完后再 probe 一次。
    """
    overlays_dir = Path(overlays_dir)
    overlays_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "render_overlays.py"),
        str(storyboard_path),
        "--out",
        str(overlays_dir),
        "--fallback-engine",
        fallback_engine,
    ]
    # storyboard 没 overlay 字段 → 加 --demo 让 render_overlays 自动注入
    if not has_overlay_definitions(storyboard):
        cmd.append("--demo")
    _log(f"overlay 不全，调 render_overlays.py: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-3000:]
        raise RuntimeError(f"render_overlays.py 失败 rc={proc.returncode}\n{tail}")
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")

    manifest_path = overlays_dir / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------------------
# 多段视频拼接
# ---------------------------------------------------------------------------


def concat_clips(clips: list[Path], out_path: Path) -> Path:
    """把多个 shot 视频拼成一段（无 acrossfade；想要请外部做）。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) < 2:
        raise ValueError(f"concat_clips 需要至少 2 段；当前 {len(clips)}")

    inputs: list[str] = []
    for c in clips:
        inputs.extend(["-i", str(c)])

    fc_parts = "".join(f"[{i}:v][{i}:a]" for i in range(len(clips)))
    fc = (
        f"{fc_parts}concat=n={len(clips)}:v=1:a=1[vout][aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out_path),
    ]
    run_subprocess(cmd, label="concat_clips")
    return out_path


# ---------------------------------------------------------------------------
# overlay 烧录主流程
# ---------------------------------------------------------------------------


def build_overlay_filter_complex(
    main_video: Path,
    overlays: list[dict],
    *,
    target_w: int,
    target_h: int,
    keep_audio: bool,
) -> tuple[str, list[str], list[str]]:
    """拼 ffmpeg -filter_complex 串。

    overlays: 烧录顺序列表（每个 dict 含 path / start / end / kind / engine）。
    返回 (filter_complex, input_args, mapped_streams)。
    """
    # input 索引：0 = 主视频；1..N = 各 overlay（按 overlays 列表顺序）
    # 滤镜链：[vout] = main → +subtitle1 → +subtitle2 → ... → +title → +end
    parts: list[str] = []

    # 主视频：scale + pad 保比例（contain 模式）
    parts.append(
        f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,format=yuv420p[v0]"
    )

    # 每个 overlay：先标准化尺寸/像素格式
    for idx, ov in enumerate(overlays, start=1):
        is_png = ov["path"].lower().endswith(".png")
        if is_png:
            parts.append(
                f"[{idx}:v]scale={target_w}:{target_h},format=rgba,"
                f"setsar=1[ov{idx}]"
            )
        else:
            parts.append(
                f"[{idx}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,format=yuv420p[ov{idx}]"
            )

    # overlay filter 链：按 overlays 顺序串起来（最后加的在最上层）
    prev_tag = "v0"
    for idx, ov in enumerate(overlays, start=1):
        start = max(0.0, float(ov["start"]))
        end = float(ov["end"])
        # ffmpeg enable 表达式：between(t,start,end)
        enable_expr = f"between(t,{start:.3f},{end:.3f})"
        new_tag = f"v{idx}"
        parts.append(
            f"[{prev_tag}][ov{idx}]overlay=x=0:y=0:enable='{enable_expr}'"
            f":shortest=0[{new_tag}]"
        )
        prev_tag = new_tag

    # 音频处理
    if keep_audio:
        # 直通主视频音频（若主视频本身无音频，-map 0:a? 会安全跳过）
        audio_map = ["-map", "0:a?"]
    else:
        audio_map = []

    mapped = ["-map", f"[{prev_tag}]", *audio_map]
    fc = ";\n".join(parts)
    return fc, mapped, []


def burn_overlays(
    main_video: Path,
    overlays: list[dict],
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    crf: int,
    preset: str,
    keep_audio: bool,
) -> Path:
    """主烧录：把 overlays 列表按时间烧到 main_video 上，输出到 out_path。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not overlays:
        # 没 overlay 时直接 remux / 转码到统一规格
        cmd = [
            "ffmpeg", "-y",
            "-i", str(main_video),
            "-vf",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
            f"format=yuv420p",
            "-map", "0:v",
            *([] if not keep_audio else ["-map", "0:a?"]),
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-pix_fmt", "yuv420p",
            *([] if not keep_audio else ["-c:a", "copy"]),
            "-an" if not keep_audio else "-sn",
            str(out_path),
        ]
        run_subprocess(cmd, label="burn_overlays (no overlays, transcode)")
        return out_path

    fc, mapped_streams, _ = build_overlay_filter_complex(
        main_video,
        overlays,
        target_w=target_w,
        target_h=target_h,
        keep_audio=keep_audio,
    )

    cmd = ["ffmpeg", "-y", "-i", str(main_video)]
    for ov in overlays:
        cmd.extend(["-i", str(ov["path"])])
    cmd.extend(
        [
            "-filter_complex", fc,
            *mapped_streams,
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-pix_fmt", "yuv420p",
            *([] if not keep_audio else ["-c:a", "copy"]),
            "-sn",  # 不要字幕轨道
            str(out_path),
        ]
    )
    run_subprocess(cmd, label="burn_overlays")
    return out_path


# ---------------------------------------------------------------------------
# 像素抽样验证
# ---------------------------------------------------------------------------


def extract_frame(video: Path, at_seconds: float, out_jpg: Path) -> Path:
    """从 video 在 at_seconds 抽一帧到 out_jpg。"""
    out_jpg = Path(out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{at_seconds:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_jpg),
    ]
    run_subprocess(cmd, label=f"extract_frame@{at_seconds:.2f}s")
    return out_jpg


def _load_pil():
    try:
        from PIL import Image  # type: ignore
        return Image
    except ImportError:
        sys.stderr.write("错误：缺少 Pillow 依赖，请先执行 pip install pillow\n")
        raise


def check_title_card(image_path: Path) -> dict:
    """title-card 全屏覆盖：暗背景 + 高亮 accent/title 文本。

    通过：暗色像素占比 > 60%，且存在亮色 accent (橙色 / 白色)。
    """
    Image = _load_pil()
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    pixels = img.load()

    dark = 0
    bright_accent = 0
    total = 0
    for y in range(0, h, 6):
        for x in range(0, w, 6):
            r, g, b = pixels[x, y]
            total += 1
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum < 40:
                dark += 1
            # orange accent (R>180, G 100-200, B<120) 或近白 (R>200, G>200, B>200)
            if (r > 180 and 100 < g < 200 and b < 120) or (r > 220 and g > 220 and b > 220):
                bright_accent += 1
    dark_ratio = dark / total
    accent_ratio = bright_accent / total
    return {
        "frame": str(image_path),
        "dark_pixel_ratio": round(dark_ratio, 4),
        "bright_accent_pixel_ratio": round(accent_ratio, 6),
        "size": [w, h],
        "pass": dark_ratio > 0.4 and accent_ratio > 1e-5,
        "note": "title-card 期望暗背景+accent/白色文字",
    }


def check_subtitle_bar(image_path: Path, *, bar_ratio: float = 0.18) -> dict:
    """字幕条：底部 bar_ratio 区域有半透明黑底+白字。

    通过：底部 bar 区域存在低亮度像素（黑底），且存在高亮度像素（白字）。
    """
    Image = _load_pil()
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    pixels = img.load()

    bar_top = int(h * (1 - bar_ratio))
    dark_bar = 0
    bright_text = 0
    total = 0
    for y in range(bar_top, h, 4):
        for x in range(0, w, 4):
            r, g, b = pixels[x, y]
            total += 1
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum < 60:
                dark_bar += 1
            if lum > 220:
                bright_text += 1
    dark_ratio = dark_bar / total
    bright_ratio = bright_text / total
    return {
        "frame": str(image_path),
        "bar_top_y": bar_top,
        "bar_dark_pixel_ratio": round(dark_ratio, 4),
        "bar_bright_pixel_ratio": round(bright_ratio, 6),
        "size": [w, h],
        "pass": dark_ratio > 0.5 and bright_ratio > 1e-5,
        "note": "subtitle-bar 期望底部黑底+白字",
    }


def check_end_card(image_path: Path) -> dict:
    """end-card：与 title-card 类似（全屏覆盖）；可宽松点。"""
    res = check_title_card(image_path)
    res["note"] = "end-card 期望暗背景+title/credit 文字"
    return res


def check_plain_frame(image_path: Path) -> dict:
    """普通帧（主视频原画面）参考：无强制 pass；只记亮度分布。"""
    Image = _load_pil()
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    pixels = img.load()
    dark = mid = bright = 0
    total = 0
    for y in range(0, h, 6):
        for x in range(0, w, 6):
            r, g, b = pixels[x, y]
            total += 1
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum < 40:
                dark += 1
            elif lum < 180:
                mid += 1
            else:
                bright += 1
    return {
        "frame": str(image_path),
        "dark_pixel_ratio": round(dark / total, 4),
        "mid_pixel_ratio": round(mid / total, 4),
        "bright_pixel_ratio": round(bright / total, 4),
        "size": [w, h],
        "pass": True,  # 仅供参考，不强制
        "note": "普通主视频帧（参考）",
    }


def verify_output(
    out_video: Path,
    *,
    main_video_duration: float,
    subtitles: list[dict],
    has_title: bool,
    has_end: bool,
    title_duration: float,
    end_duration: float,
    verify_dir: Path,
) -> dict:
    """抽帧验证 overlay 真的烧上去了。"""
    verify_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict] = []

    def _sample(at: float, label: str, fn, **kw) -> None:
        jpg = verify_dir / f"{int(at * 100):06d}_{label}.jpg"
        try:
            extract_frame(out_video, at, jpg)
            res = fn(jpg, **kw)
            res["time"] = at
            res["label"] = label
            samples.append(res)
        except Exception as exc:  # noqa: BLE001
            samples.append(
                {"time": at, "label": label, "pass": False,
                 "error": f"{type(exc).__name__}: {exc}"}
            )

    # 算 title-card / end-card 的覆盖窗口，便于挑选不被覆盖的字幕抽样时刻
    title_window = (0.0, float(title_duration)) if has_title else None
    end_window = (
        (max(0.0, main_video_duration - float(end_duration)), main_video_duration)
        if has_end
        else None
    )

    def _pick_subtitle_sample(start: float, end: float) -> float | None:
        """在 [start, end] 里找一个不被 title/end 覆盖的抽样时刻；找不到返回 None。"""
        mid = (start + end) / 2
        candidates = [mid, start + 0.5, end - 0.5, start + (end - start) * 0.6]
        for cand in candidates:
            if cand < 0 or cand >= main_video_duration:
                continue
            if title_window and title_window[0] <= cand <= title_window[1]:
                continue
            if end_window and end_window[0] <= cand <= end_window[1]:
                continue
            if start <= cand <= end:
                return cand
        return None

    # 1) 抽 title-card 帧（在 title 时长内，避免与首段字幕冲突）
    if has_title:
        sample_at = min(1.5, max(0.1, title_duration * 0.5))
        _sample(sample_at, "title_card", check_title_card)

    # 2) 抽每个字幕条帧（mid of shot，避开 title/end 覆盖）
    for s in subtitles:
        cand = _pick_subtitle_sample(s["start"], s["end"])
        if cand is None:
            samples.append(
                {
                    "time": s["start"],
                    "label": f"shot{int(s['shot_index']):02d}_subtitle",
                    "pass": False,
                    "note": "shot 完全被 title-card/end-card 覆盖，无安全抽样时刻",
                }
            )
            continue
        _sample(cand, f"shot{int(s['shot_index']):02d}_subtitle", check_subtitle_bar)

    # 3) 抽 end-card 帧（在 end 时长内）
    if has_end:
        sample_at = max(main_video_duration - end_duration * 0.5, main_video_duration - 0.2)
        _sample(sample_at, "end_card", check_end_card)

    # 4) 普通帧参考（mid of video, but 避开 overlay 区；找不到就跳过）
    plain_candidates = [
        main_video_duration / 2,
        main_video_duration * 0.4,
        main_video_duration * 0.6,
    ]
    plain_at = None
    for cand in plain_candidates:
        if 0 <= cand < main_video_duration:
            if title_window and title_window[0] <= cand <= title_window[1]:
                continue
            if end_window and end_window[0] <= cand <= end_window[1]:
                continue
            plain_at = cand
            break
    if plain_at is not None:
        _sample(plain_at, "plain_mid", check_plain_frame)

    passed = sum(1 for s in samples if s.get("pass"))
    total = len(samples)
    summary = {
        "video": str(out_video),
        "duration": main_video_duration,
        "frames_checked": total,
        "frames_passed": passed,
        "samples": samples,
    }
    (verify_dir / "verify_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase C：把 overlay 烧录到视频流，输出 video_with_overlay.mp4。"
    )
    p.add_argument(
        "--storyboard",
        required=True,
        help="storyboard.json 路径",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--video",
        help="已拼接的主视频（单文件）",
    )
    src.add_argument(
        "--videos",
        action="append",
        default=[],
        help="多段 shot 视频（可多次传；自动按出现顺序拼接）",
    )
    p.add_argument(
        "--out",
        required=True,
        help="输出 video_with_overlay.mp4 路径",
    )
    p.add_argument(
        "--overlays-dir",
        default=str(DEFAULT_OVERLAYS_DIR),
        help=f"overlay 输出目录（默认 {DEFAULT_OVERLAYS_DIR}）",
    )
    p.add_argument(
        "--auto-overlay",
        dest="auto_overlay",
        action="store_true",
        default=True,
        help="storyboard 无 overlay 字段时自动注入 demo（默认开）",
    )
    p.add_argument(
        "--no-auto-overlay",
        dest="auto_overlay",
        action="store_false",
        help="storyboard 无 overlay 字段时跳过渲染（仅按现有 overlay 烧录）",
    )
    p.add_argument(
        "--fallback-engine",
        default="pil",
        choices=("pil", "hyperframes"),
        help="HyperFrames 不可用时的兜底 engine（传给 render_overlays.py）",
    )
    p.add_argument(
        "--no-audio",
        action="store_true",
        help="输出无音频（默认直通主视频音频）",
    )
    p.add_argument(
        "--width", type=int, default=1920,
        help="输出宽度（默认 1920，与 overlay 画布对齐）",
    )
    p.add_argument(
        "--height", type=int, default=1080,
        help="输出高度（默认 1080）",
    )
    p.add_argument(
        "--crf", type=int, default=18, help="x264 CRF（默认 18）",
    )
    p.add_argument(
        "--preset", default="medium", help="x264 preset（默认 medium）",
    )
    p.add_argument(
        "--no-verify", action="store_true",
        help="跳过 ffprobe + 抽帧像素验证",
    )
    p.add_argument(
        "--render-clips-out",
        default=None,
        help="多段拼接时的中间文件路径（默认 <out>.concat.mp4）",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    storyboard_path = Path(args.storyboard).resolve()
    if not storyboard_path.is_file():
        sys.stderr.write(f"错误：storyboard 文件不存在 - {storyboard_path}\n")
        return 2
    overlays_dir = Path(args.overlays_dir).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))

    # 1. 准备主视频（多段就先拼）
    if args.videos:
        clips = [Path(v).resolve() for v in args.videos]
        for c in clips:
            if not c.is_file():
                sys.stderr.write(f"错误：clip 不存在 - {c}\n")
                return 2
        render_clips_out = (
            Path(args.render_clips_out).resolve()
            if args.render_clips_out
            else out_path.with_suffix(".concat.mp4")
        )
        _log(f"拼接 {len(clips)} 段 → {render_clips_out}")
        main_video = concat_clips(clips, render_clips_out)
    else:
        main_video = Path(args.video).resolve()
        if not main_video.is_file():
            sys.stderr.write(f"错误：主视频不存在 - {main_video}\n")
            return 2

    main_duration = ffprobe_duration(main_video)
    main_stream = ffprobe_video_stream(main_video) or {}
    main_audio_stream = ffprobe_audio_stream(main_video)
    main_w = int(main_stream.get("width", 0))
    main_h = int(main_stream.get("height", 0))
    _log(
        f"主视频: {main_video} 时长 {main_duration:.3f}s {main_w}x{main_h} "
        f"audio={'yes' if main_audio_stream else 'no'}"
    )

    # 2. 收集 overlay plan
    plan = collect_overlay_plan(storyboard, overlays_dir)
    _log(
        f"plan: title={'yes' if plan['title_card'] else 'no'} "
        f"end={'yes' if plan['end_card'] else 'no'} "
        f"subs={len(plan['subtitles'])} injected_demo={plan['injected_demo']}"
    )

    # 3. 缺失 overlay → 调 render_overlays.py
    needs_render = False
    if plan["title_card"] and not plan["title_card"]["exists"]:
        needs_render = True
    if plan["end_card"] and not plan["end_card"]["exists"]:
        needs_render = True
    if any(not s["exists"] for s in plan["subtitles"]):
        needs_render = True

    if needs_render and args.auto_overlay:
        _log("overlay 不全，调 render_overlays.py 补齐…")
        ensure_overlays(
            storyboard,
            storyboard_path,
            overlays_dir,
            fallback_engine=args.fallback_engine,
        )
        # 重新收集 plan（更新 exists）
        plan = collect_overlay_plan(storyboard, overlays_dir)
    elif needs_render and not args.auto_overlay:
        missing = []
        if plan["title_card"] and not plan["title_card"]["exists"]:
            missing.append(plan["title_card"]["expected_path"])
        if plan["end_card"] and not plan["end_card"]["exists"]:
            missing.append(plan["end_card"]["expected_path"])
        for s in plan["subtitles"]:
            if not s["exists"]:
                missing.append(s["expected_path"])
        sys.stderr.write(
            "错误：以下 overlay 缺失，且未启用 --auto-overlay：\n  "
            + "\n  ".join(missing) + "\n"
        )
        return 3

    # 4. 准备 burn 列表（含 start/end）
    burn_list: list[dict] = []

    # 字幕条（先烧，作为下层）
    for s in plan["subtitles"]:
        if not s["exists"]:
            _log(f"  跳过缺失 subtitle-bar shot={s['shot_index']} -> {s['expected_path']}")
            continue
        burn_list.append(
            {
                "kind": "subtitle",
                "path": s["expected_path"],
                "start": s["start"],
                "end": s["end"],
                "shot_index": s["shot_index"],
            }
        )

    # title-card：在 [0, title_duration]
    if plan["title_card"] and plan["title_card"]["exists"]:
        title_dur = float(plan["title_card"]["duration"])
        burn_list.append(
            {
                "kind": "title",
                "path": plan["title_card"]["expected_path"],
                "start": 0.0,
                "end": min(title_dur, main_duration),
            }
        )

    # end-card：在 [main_duration - end_duration, main_duration]
    if plan["end_card"] and plan["end_card"]["exists"]:
        end_dur = float(plan["end_card"]["duration"])
        burn_list.append(
            {
                "kind": "end",
                "path": plan["end_card"]["expected_path"],
                "start": max(0.0, main_duration - end_dur),
                "end": main_duration,
            }
        )

    _log(f"待烧录 {len(burn_list)} 层（按烧录顺序）:")
    for ov in burn_list:
        _log(f"  - {ov['kind']:>8s} [{ov['start']:.3f} → {ov['end']:.3f}]  {ov['path']}")

    # 5. 主烧录
    keep_audio = (not args.no_audio) and bool(main_audio_stream)
    burn_overlays(
        main_video=main_video,
        overlays=burn_list,
        out_path=out_path,
        target_w=args.width,
        target_h=args.height,
        crf=args.crf,
        preset=args.preset,
        keep_audio=keep_audio,
    )
    out_duration = ffprobe_duration(out_path)
    out_stream = ffprobe_video_stream(out_path) or {}
    out_audio_stream = ffprobe_audio_stream(out_path)
    _log(
        f"产物: {out_path} 时长 {out_duration:.3f}s "
        f"{int(out_stream.get('width', 0))}x{int(out_stream.get('height', 0))} "
        f"audio={'yes' if out_audio_stream else 'no'}"
    )

    # 6. 验证（抽帧 + 像素）
    verify_report = None
    if not args.no_verify:
        verify_dir = out_path.with_suffix(out_path.suffix + ".verify")
        verify_report = verify_output(
            out_video=out_path,
            main_video_duration=main_duration,
            subtitles=plan["subtitles"],
            has_title=bool(plan["title_card"] and plan["title_card"]["exists"]),
            has_end=bool(plan["end_card"] and plan["end_card"]["exists"]),
            title_duration=float(plan["title_card"]["duration"]) if plan["title_card"] else 0.0,
            end_duration=float(plan["end_card"]["duration"]) if plan["end_card"] else 0.0,
            verify_dir=verify_dir,
        )
        passed = verify_report["frames_passed"]
        total = verify_report["frames_checked"]
        _log(f"verify: {passed}/{total} 通过；报告 {verify_dir / 'verify_report.json'}")
        for s in verify_report["samples"]:
            mark = "PASS" if s.get("pass") else "FAIL"
            line = f"  [{mark}] t={s['time']:.2f}s {s['label']}"
            if "error" in s:
                line += f" ERROR={s['error']}"
            _log(line)

    # 7. 写 meta JSON（给 audio 阶段用）
    meta = {
        "compose_version": "0.1",
        "storyboard": str(storyboard_path),
        "main_video": str(main_video),
        "main_duration": main_duration,
        "main_video_resolution": [main_w, main_h],
        "main_video_has_audio": bool(main_audio_stream),
        "overlays_dir": str(overlays_dir),
        "output": str(out_path),
        "output_duration": out_duration,
        "output_resolution": [int(out_stream.get("width", 0)), int(out_stream.get("height", 0))],
        "output_has_audio": bool(out_audio_stream),
        "burn_list": burn_list,
        "plan": {
            "injected_demo": plan["injected_demo"],
            "title_card": plan["title_card"],
            "end_card": plan["end_card"],
            "subtitles_count": len(plan["subtitles"]),
        },
        "verify": (
            {
                "frames_checked": verify_report["frames_checked"],
                "frames_passed": verify_report["frames_passed"],
                "report_json": str(Path(out_path).with_suffix(Path(out_path).suffix + ".verify") / "verify_report.json"),
            }
            if verify_report
            else None
        ),
    }
    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"meta -> {meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())