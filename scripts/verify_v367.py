#!/usr/bin/env python3
"""v3.6.7 验证脚本: 流级断言 + 与参考视频的色彩对比 (任务书 §4 §6 关键).

任务书 oc_task_v367.txt §4 §6:
- 断言分辨率 1344x576 / pix_fmt yuv420p / 时长 / YAVG
- **色彩对比参考**: 抽成片与参考视频的多帧, 比较 RGB 直方图余弦相似度
  (验证成片色彩/人物/风格是否真的跟参考一致, 而非只看段间)

CLI:
    python verify_v367.py
    python verify_v367.py --video output/pipeline_v36/final_v36_60s_v367.mp4
    python verify_v367.py --ref-video input_h3_pv_ref.mp4
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v367.mp4"
DEFAULT_REF_VIDEO = ROOT / "input_h3_pv_ref.mp4"
DEFAULT_CLIPS_DIR = ROOT / "output" / "pipeline_v36" / "clips_v367"
DEFAULT_SHOTS_DIR = ROOT / "output" / "pipeline_v36" / "shots_v367"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "qa_frames_v367"

EXPECTED_VIDEO_DURATION_SEC = 40.0
EXPECTED_VIDEO_NB_FRAMES = 960  # 40s * 24fps
EXPECTED_FPS = 24
EXPECTED_WIDTH = 1344
EXPECTED_HEIGHT = 576
EXPECTED_PIX_FMT = "yuv420p"
DURATION_TOLERANCE_SEC = 1.5
NB_FRAMES_TOLERANCE = 60

SAMPLE_TIMES_SEC = (3.0, 8.0, 14.0, 22.0, 30.0, 38.0)
YAVG_MIN_THRESHOLD = 5.0
YAVG_FIRST_MAX_THRESHOLD = 245.0
YAVG_SAMPLE_MAX_THRESHOLD = 245.0

# 任务书 §4: 与参考视频的色彩对比阈值 (RGB 直方图余弦)
# v367 强制: 色彩/人物/背景对齐参考. 余弦 0.85+ 算强对齐, 0.70-0.85 算可接受.
COLOR_COS_MIN_THRESHOLD = 0.65  # 低于此算明显偏离参考
COLOR_COS_MEAN_GOOD = 0.80     # 全片与参考平均余弦 >= 0.80 算好


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def ffprobe_streams(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        return {"_error": r.stderr.strip()}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return {"_error": f"json parse failed: {e}"}


def yavg_at(video_path: Path, t_sec: float) -> tuple[float | None, str]:
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=320:-1,signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ]
    out = run(cmd)
    for line in out.splitlines():
        line_l = line.lower()
        if "yavg=" in line_l:
            try:
                val = line_l.split("yavg=")[1].split()[0].rstrip(",")
                return float(val), "ok"
            except (ValueError, IndexError):
                continue
    return None, f"yavg not found in output: {out[-200:]}"


def extract_frame(video_path: Path, t_sec: float, out_path: Path,
                  w: int = 1344, h: int = 576) -> None:
    """抽 1 帧, 缩到指定尺寸。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", (f"scale={w}:{h}:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1:1"),
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"抽帧失败: {r.stderr[-300:]}")


def hist_cosine(p1: Path, p2: Path) -> float:
    """RGB 直方图 (256-bin per channel) 余弦相似度。"""
    from PIL import Image

    def vec(p: Path) -> list[float]:
        img = Image.open(p).convert("RGB").resize((192, 108), Image.LANCZOS)
        h = img.histogram()
        total = sum(h) or 1
        return [c / total for c in h]

    v1 = vec(p1)
    v2 = vec(p2)
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    return dot / (n1 * n2 + 1e-9)


def mean_rgb(img_path: Path) -> tuple[int, int, int]:
    """取整张图的 RGB 均值 (用于粗略色彩对齐检测)."""
    from PIL import Image
    img = Image.open(img_path).convert("RGB").resize((64, 36), Image.LANCZOS)
    pixels = list(img.getdata())
    n = len(pixels)
    r = sum(p[0] for p in pixels) // n
    g = sum(p[1] for p in pixels) // n
    b = sum(p[2] for p in pixels) // n
    return (r, g, b)


def color_compare_with_ref(video_path: Path, ref_video: Path,
                           qa_dir: Path) -> dict:
    """任务书 §4 §6 核心: 与参考视频的色彩直方图对比。

    抽参考视频和成片各 8 帧 (覆盖全片时段), 对每对帧算 RGB 直方图余弦.
    """
    from PIL import Image
    qa_dir.mkdir(parents=True, exist_ok=True)
    sub_ref = qa_dir / "color_compare" / "ref"
    sub_out = qa_dir / "color_compare" / "out"
    sub_ref.mkdir(parents=True, exist_ok=True)
    sub_out.mkdir(parents=True, exist_ok=True)

    # 参考视频时长
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(ref_video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ref_dur = float(r.stdout.strip() or 31.33)

    # 成片时长
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out_dur = float(r.stdout.strip() or 40.0)

    # 8 个时间点: 参考视频和成片各自归一化取样
    n = 8
    pairs = []
    sims = []
    mean_ref_rgb = []
    mean_out_rgb = []
    for i in range(n):
        t_frac = (i + 0.5) / n
        t_ref = ref_dur * t_frac
        t_out = out_dur * t_frac
        fp_ref = sub_ref / f"ref_t{t_ref:.2f}s.jpg"
        fp_out = sub_out / f"out_t{t_out:.2f}s.jpg"
        extract_frame(ref_video, t_ref, fp_ref, w=320, h=180)
        extract_frame(video_path, t_out, fp_out, w=320, h=180)
        cos = hist_cosine(fp_ref, fp_out)
        m_ref = mean_rgb(fp_ref)
        m_out = mean_rgb(fp_out)
        mean_ref_rgb.append(m_ref)
        mean_out_rgb.append(m_out)
        sims.append(cos)
        pairs.append({
            "ref_t_sec": round(t_ref, 3),
            "out_t_sec": round(t_out, 3),
            "ref_frame": str(fp_ref),
            "out_frame": str(fp_out),
            "cosine_similarity": round(cos, 4),
            "ref_mean_rgb": list(m_ref),
            "out_mean_rgb": list(m_out),
        })

    if sims:
        mean_sim = sum(sims) / len(sims)
        min_sim = min(sims)
        max_sim = max(sims)
    else:
        mean_sim = min_sim = max_sim = 0.0

    # mean RGB 偏差
    def avg(lst):  # noqa
        return tuple(sum(x[i] for x in lst) // len(lst) for i in range(3))
    ar = avg(mean_ref_rgb)
    ao = avg(mean_out_rgb)
    rgb_delta = tuple(abs(ao[i] - ar[i]) for i in range(3))

    # 平均 RGB 色相相似度 (粗略)
    # 距离 / sqrt(3*255^2) 归一化到 0-1, 1 = 完全一致
    rgb_dist = math.sqrt(sum(d * d for d in rgb_delta))
    rgb_norm_dist = rgb_dist / math.sqrt(3 * 255 * 255)
    rgb_similarity = max(0.0, 1.0 - rgb_norm_dist)

    report = {
        "n_pairs": n,
        "pairs": pairs,
        "mean_similarity": round(mean_sim, 4),
        "min_similarity": round(min_sim, 4),
        "max_similarity": round(max_sim, 4),
        "mean_ref_rgb": list(ar),
        "mean_out_rgb": list(ao),
        "mean_rgb_delta": list(rgb_delta),
        "mean_rgb_similarity": round(rgb_similarity, 4),
        "ok_histogram": mean_sim >= COLOR_COS_MIN_THRESHOLD,
        "ok_mean_rgb_similarity": rgb_similarity >= 0.70,
        "thresholds": {
            "histogram_min": COLOR_COS_MIN_THRESHOLD,
            "histogram_mean_good": COLOR_COS_MEAN_GOOD,
            "rgb_similarity_good": 0.70,
        },
        "all_pairs_above_threshold": all(s >= COLOR_COS_MIN_THRESHOLD for s in sims),
        "interpretation": (
            "任务书 §4 §6 核心验证: 成片与参考视频的色彩直方图相似度."
            "若 mean_similarity >= 0.80 算强对齐 (good); "
            "0.65-0.80 算可接受 (acceptable); "
            "< 0.65 算明显漂移 (drift)."
        ),
    }
    return report


def _resolve_clips_dir(clips_dir_arg: str) -> Path:
    return Path(clips_dir_arg) if clips_dir_arg else DEFAULT_CLIPS_DIR


def _resolve_shots_dir(shots_dir_arg: str) -> Path:
    return Path(shots_dir_arg) if shots_dir_arg else DEFAULT_SHOTS_DIR


def per_shot_meta_check(shots_dir: Path) -> dict:
    """检查每段 meta 的关键字段 (r2v method 等)。"""
    report = {"n_shots": 0, "per_shot": {}, "all_ok": True, "errors": []}
    for cp in sorted(shots_dir.glob("shot*_meta.json"),
                     key=lambda p: int(p.stem.split("_")[0].replace("shot", ""))):
        shot_idx = int(cp.stem.split("_")[0].replace("shot", ""))
        report["n_shots"] += 1
        try:
            m = json.loads(cp.read_text(encoding="utf-8-sig"))
        except Exception as e:
            report["errors"].append(f"{cp.name}: parse failed: {e}")
            report["all_ok"] = False
            continue
        info = {
            "phase": m.get("phase"),
            "duration_sec": m.get("duration_sec"),
            "method": m.get("method"),
            "node_class": m.get("node_class"),
            "ref_image_size": m.get("ref_image_size"),
            "ref_images_count": m.get("ref_images_count"),
            "yavg_ok": (m.get("yavg_check", {}) or {}).get("ok"),
        }
        report["per_shot"][shot_idx] = info
        # 硬约束: method 必须是 reference_to_video_v367
        if info["method"] != "reference_to_video_v367":
            report["errors"].append(
                f"shot{shot_idx:02d}: method should be "
                f"reference_to_video_v367, got {info['method']}")
            report["all_ok"] = False
        if info["node_class"] != "MiniMaxH3ReferenceToVideo":
            report["errors"].append(
                f"shot{shot_idx:02d}: node_class should be "
                f"MiniMaxH3ReferenceToVideo, got {info['node_class']}")
            report["all_ok"] = False
        if info["ref_images_count"] != 4:
            report["errors"].append(
                f"shot{shot_idx:02d}: ref_images_count should be 4, "
                f"got {info['ref_images_count']}")
            report["all_ok"] = False
    return report


def verify_video(path: Path, ref_video: Path, shots_dir: Path) -> dict:
    print(f"[verify-v367] ============================================")
    print(f"[verify-v367] target  = {path}")
    print(f"[verify-v367] ref vid = {ref_video}")
    print(f"[verify-v367] shots   = {shots_dir}")
    print(f"[verify-v367] ============================================")

    if not path.exists():
        return {"ok": False, "reason": f"file not found: {path}"}

    data = ffprobe_streams(path)
    if "_error" in data:
        return {"ok": False, "reason": data["_error"]}

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if v_stream is None:
        return {"ok": False, "reason": "no video stream"}

    try:
        v_dur = float(v_stream.get("duration", "0") or 0)
        v_frames = int(v_stream.get("nb_frames", "0") or 0)
        v_width = int(v_stream.get("width", "0") or 0)
        v_height = int(v_stream.get("height", "0") or 0)
        v_pix_fmt = v_stream.get("pix_fmt", "")
    except (ValueError, TypeError):
        return {"ok": False, "reason": "video parse failed"}

    a_dur = float(a_stream.get("duration", "0") or 0) if a_stream else 0.0
    fmt_dur = float(fmt.get("duration", "0") or 0)

    v_dur_ok = abs(v_dur - EXPECTED_VIDEO_DURATION_SEC) <= DURATION_TOLERANCE_SEC
    v_dur_delta = v_dur - EXPECTED_VIDEO_DURATION_SEC
    v_frames_ok = abs(v_frames - EXPECTED_VIDEO_NB_FRAMES) <= NB_FRAMES_TOLERANCE
    v_frames_delta = v_frames - EXPECTED_VIDEO_NB_FRAMES
    a_av_match = abs(v_dur - a_dur) <= DURATION_TOLERANCE_SEC if a_dur else True
    res_ok = (v_width == EXPECTED_WIDTH and v_height == EXPECTED_HEIGHT)
    pix_ok = (v_pix_fmt == EXPECTED_PIX_FMT)

    sample_results = []
    sample_ok = True
    for t in SAMPLE_TIMES_SEC:
        if t > v_dur + 0.5:
            sample_results.append({"t": t, "yavg": None, "ok": False,
                                    "note": "t > video duration"})
            sample_ok = False
            continue
        yavg, note = yavg_at(path, t)
        ok = (yavg is not None and yavg > YAVG_MIN_THRESHOLD
              and yavg < YAVG_SAMPLE_MAX_THRESHOLD)
        sample_results.append({"t": round(t, 3), "yavg": yavg, "ok": ok,
                                "note": note})
        if not ok:
            sample_ok = False

    first_yavg, first_note = yavg_at(path, 0.05)
    first_ok = (first_yavg is not None
                and first_yavg < YAVG_FIRST_MAX_THRESHOLD
                and first_yavg > YAVG_MIN_THRESHOLD)

    # 色彩对比参考视频 (任务书 §4 §6 核心)
    color_compare = color_compare_with_ref(
        path, ref_video,
        ROOT / "output" / "pipeline_v36" / "qa_frames_v367" / "color_compare")

    meta_check = per_shot_meta_check(shots_dir)

    all_ok = (v_dur_ok and v_frames_ok and a_av_match
              and res_ok and pix_ok and sample_ok
              and first_ok
              and color_compare["ok_histogram"]
              and color_compare["ok_mean_rgb_similarity"]
              and meta_check["all_ok"])

    summary = {
        "ok": bool(all_ok),
        "target": str(path),
        "format_duration_sec": round(fmt_dur, 4),
        "video_stream_duration_sec": round(v_dur, 4),
        "video_stream_nb_frames": v_frames,
        "video_stream_width": v_width,
        "video_stream_height": v_height,
        "video_stream_pix_fmt": v_pix_fmt,
        "audio_stream_duration_sec": round(a_dur, 4),
        "expected_video_duration_sec": EXPECTED_VIDEO_DURATION_SEC,
        "expected_video_nb_frames": EXPECTED_VIDEO_NB_FRAMES,
        "expected_resolution": f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}",
        "expected_pix_fmt": EXPECTED_PIX_FMT,
        "duration_tolerance_sec": DURATION_TOLERANCE_SEC,
        "nb_frames_tolerance": NB_FRAMES_TOLERANCE,
        "video_duration_ok": v_dur_ok,
        "video_duration_delta": round(v_dur_delta, 4),
        "video_nb_frames_ok": v_frames_ok,
        "video_nb_frames_delta": v_frames_delta,
        "audio_av_match": a_av_match,
        "resolution_ok": res_ok,
        "pix_fmt_ok": pix_ok,
        "first_frame_yavg": first_yavg,
        "first_frame_ok": first_ok,
        "first_frame_note": first_note,
        "sample_results": sample_results,
        "all_samples_ok": sample_ok,
        "color_compare_with_ref": color_compare,  # 任务书 §4 §6 核心
        "per_shot_meta_check": meta_check,
        "yavg_min_threshold": YAVG_MIN_THRESHOLD,
        "yavg_first_max_threshold": YAVG_FIRST_MAX_THRESHOLD,
        "vs_v366_changes": [
            "MiniMaxH3ReferenceToVideo (NOT MiniMaxH3ImageToVideo)",
            "4 ref_images (NOT single first_frame chain)",
            "no chain dependency between shots",
            "color histogram compared against reference video "
            "(任务书 §4 §6 关键)",
            "mean RGB delta vs reference (任务书 §4 §6 关键)",
        ],
    }

    print(f"[verify-v367] format.duration       = {fmt_dur:.4f}s")
    print(f"[verify-v367] video stream duration = {v_dur:.4f}s  "
          f"{'PASS' if v_dur_ok else 'FAIL'}  "
          f"(expected {EXPECTED_VIDEO_DURATION_SEC} ± "
          f"{DURATION_TOLERANCE_SEC}s, Δ={v_dur_delta:+.4f}s)")
    print(f"[verify-v367] video stream nb_frames= {v_frames}    "
          f"{'PASS' if v_frames_ok else 'FAIL'}  "
          f"(Δ={v_frames_delta:+d})")
    if a_stream:
        print(f"[verify-v367] audio stream duration = {a_dur:.4f}s  "
              f"{'PASS' if a_av_match else 'FAIL'}")
    print(f"[verify-v367] resolution            = {v_width}x{v_height}  "
          f"{'PASS' if res_ok else 'FAIL'}")
    print(f"[verify-v367] pix_fmt               = {v_pix_fmt}  "
          f"{'PASS' if pix_ok else 'FAIL'}")
    print(f"[verify-v367] first frame YAVG      = {first_yavg}  "
          f"{'PASS' if first_ok else 'FAIL'}")
    print(f"[verify-v367] sample frame YAVG:")
    for sr in sample_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        print(f"[verify-v367]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}")

    print(f"\n[verify-v367] ============== 色彩对比参考视频 (任务书 §4 §6 核心) ==============")
    print(f"[verify-v367] {color_compare['n_pairs']} 对帧 RGB 直方图余弦:")
    for p in color_compare["pairs"]:
        print(f"[verify-v367]   ref_t={p['ref_t_sec']:5.2f}s  out_t={p['out_t_sec']:5.2f}s"
              f"  cos={p['cosine_similarity']:.4f}  "
              f"ref_RGB={p['ref_mean_rgb']}  out_RGB={p['out_mean_rgb']}")
    print(f"[verify-v367]   min={color_compare['min_similarity']:.4f}  "
          f"mean={color_compare['mean_similarity']:.4f}  "
          f"max={color_compare['max_similarity']:.4f}  "
          f"{'PASS' if color_compare['ok_histogram'] else 'FAIL'} "
          f"(threshold {COLOR_COS_MIN_THRESHOLD})")
    print(f"[verify-v367] mean RGB: ref={color_compare['mean_ref_rgb']} "
          f"out={color_compare['mean_out_rgb']} "
          f"delta={color_compare['mean_rgb_delta']} "
          f"similarity={color_compare['mean_rgb_similarity']:.4f}  "
          f"{'PASS' if color_compare['ok_mean_rgb_similarity'] else 'FAIL'}")
    print(f"[verify-v367] 评估:")
    if color_compare['mean_similarity'] >= COLOR_COS_MEAN_GOOD:
        print(f"[verify-v367]   ✓ 强对齐 (mean cos >= {COLOR_COS_MEAN_GOOD})")
    elif color_compare['mean_similarity'] >= COLOR_COS_MIN_THRESHOLD:
        print(f"[verify-v367]   △ 可接受 (mean cos {COLOR_COS_MIN_THRESHOLD}-"
              f"{COLOR_COS_MEAN_GOOD})")
    else:
        print(f"[verify-v367]   ✗ 明显漂移 (mean cos < {COLOR_COS_MIN_THRESHOLD})")

    print(f"\n[verify-v367] per_shot_meta_check:")
    for idx, info in meta_check["per_shot"].items():
        print(f"[verify-v367]   shot{idx:02d}: phase={info['phase']} "
              f"dur={info['duration_sec']:.1f}s "
              f"method={info['method']} "
              f"node={info['node_class']} "
              f"ref_imgs={info['ref_images_count']} "
              f"yavg_ok={info['yavg_ok']}")
    if meta_check.get("errors"):
        for e in meta_check["errors"]:
            print(f"[verify-v367]   meta error: {e}")
    print(f"\n[verify-v367] OVERALL: {'PASS' if all_ok else 'FAIL'}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--ref-video", default=str(DEFAULT_REF_VIDEO))
    ap.add_argument("--clips-dir", default=str(DEFAULT_CLIPS_DIR))
    ap.add_argument("--shots-dir", default=str(DEFAULT_SHOTS_DIR))
    ap.add_argument("--json", default=None,
                    help="写出验证结果 JSON 到指定路径")
    args = ap.parse_args(argv)

    video_path = Path(args.video)
    ref_video = Path(args.ref_video)
    shots_dir = Path(args.shots_dir)
    summary = verify_video(video_path, ref_video, shots_dir)

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[verify-v367] JSON -> {json_path}")

    return 0 if summary.get("ok") else 5


if __name__ == "__main__":
    sys.exit(main())
