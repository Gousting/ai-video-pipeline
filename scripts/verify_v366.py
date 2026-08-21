#!/usr/bin/env python3
"""v3.6.6 验证脚本: 流级断言 + 链式首尾帧人物一致性 (YAVG + 直方图)。

任务书 oc_task_v366.txt §6:
- 断言分辨率 1344x576 / pix_fmt yuv420p / 时长 / 首帧非白 / 各段首尾帧人物一致性

CLI:
    python verify_v366.py
    python verify_v366.py --video output/pipeline_v36/final_v36_60s_v366.mp4
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v366.mp4"
DEFAULT_CLIPS_DIR = ROOT / "output" / "pipeline_v36" / "clips_v366"
DEFAULT_SHOTS_DIR = ROOT / "output" / "pipeline_v36" / "shots_v366"
DEFAULT_REF_FRAMES = ROOT / "output" / "pipeline_v36" / "ref_frames_v366"

EXPECTED_VIDEO_DURATION_SEC = 40.0
EXPECTED_VIDEO_NB_FRAMES = 960  # 40s * 24fps
EXPECTED_FPS = 24
EXPECTED_WIDTH = 1344
EXPECTED_HEIGHT = 576
EXPECTED_PIX_FMT = "yuv420p"
DURATION_TOLERANCE_SEC = 1.5  # H3 实际输出比 length 多 0-14 帧, 6 段累积 ~1s
NB_FRAMES_TOLERANCE = 60

SAMPLE_TIMES_SEC = (3.0, 8.0, 14.0, 22.0, 30.0, 38.0)
YAVG_MIN_THRESHOLD = 5.0
YAVG_FIRST_MAX_THRESHOLD = 245.0
YAVG_SAMPLE_MAX_THRESHOLD = 245.0

CHAIN_SIMILARITY_MIN = 0.5  # 相邻段尾/首帧直方图余弦最小值


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


def extract_frame(video_path: Path, t_sec: float, out_path: Path) -> None:
    """抽 1 帧到 out_path, 缩到 1344x576。"""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", (f"scale=1344:576:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad=1344:576:(ow-iw)/2:(oh-ih)/2:black,setsar=1:1"),
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


def chain_consistency_check(clips_dir: Path, qa_dir: Path) -> dict:
    """链式衔接人物一致性: 检查相邻 shot 的尾/首帧直方图相似度。"""
    qa_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "n_pairs": 0,
        "pairs": [],
        "all_pairs_ok": True,
        "min_similarity": None,
        "mean_similarity": None,
        "errors": [],
    }
    sims = []
    for shot_idx in range(1, 6):
        prev_clip = clips_dir / f"shot{shot_idx:02d}.mp4"
        next_clip = clips_dir / f"shot{shot_idx+1:02d}.mp4"
        if not prev_clip.exists() or not next_clip.exists():
            report["errors"].append(
                f"shot{shot_idx:02d} or shot{shot_idx+1:02d} clip missing")
            report["all_pairs_ok"] = False
            continue
        # 抽 prev 尾帧 (duration - 0.10s) 和 next 首帧 (0.05s)
        prev_tail = qa_dir / f"chain_prev_shot{shot_idx:02d}_tail.jpg"
        next_head = qa_dir / f"chain_next_shot{shot_idx+1:02d}_head.jpg"
        try:
            extract_frame(prev_clip, 99.0, prev_tail)  # 临时占位
        except Exception:
            pass
        # 实际抽帧: 用 ffprobe 拿 duration
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(prev_clip)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        dur = float(lines[0]) if lines else 5.0
        extract_frame(prev_clip, max(0.1, dur - 0.10), prev_tail)
        extract_frame(next_clip, 0.05, next_head)

        cos = hist_cosine(prev_tail, next_head)
        ok = cos >= CHAIN_SIMILARITY_MIN
        sims.append(cos)
        report["n_pairs"] += 1
        report["pairs"].append({
            "between": f"shot{shot_idx:02d}_tail -> shot{shot_idx+1:02d}_head",
            "cosine_similarity": round(cos, 4),
            "min_required": CHAIN_SIMILARITY_MIN,
            "ok": ok,
        })
        if not ok:
            report["all_pairs_ok"] = False

    if sims:
        report["min_similarity"] = round(min(sims), 4)
        report["mean_similarity"] = round(sum(sims) / len(sims), 4)
    return report


def per_shot_meta_check(shots_dir: Path) -> dict:
    """检查每段 meta 的关键字段。"""
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
            "anchor_source": m.get("anchor_source"),
            "has_in_prompt_transition": m.get("has_in_prompt_transition"),
            "transition_effect": m.get("transition_effect"),
            "no_abstract_transition_words": m.get("no_abstract_transition_words"),
            "yavg_ok": (m.get("yavg_check", {}) or {}).get("ok"),
        }
        report["per_shot"][shot_idx] = info
        # 硬约束: transition_effect 必须 None, has_in_prompt_transition 必须 False
        if info["transition_effect"] is not None:
            report["errors"].append(
                f"shot{shot_idx:02d}: transition_effect should be None, "
                f"got {info['transition_effect']}")
            report["all_ok"] = False
        if info["has_in_prompt_transition"] is True:
            report["errors"].append(
                f"shot{shot_idx:02d}: has_in_prompt_transition should be False")
            report["all_ok"] = False
        if info["method"] != "chain_first_frame_i2v_v366":
            report["errors"].append(
                f"shot{shot_idx:02d}: method should be "
                f"chain_first_frame_i2v_v366, got {info['method']}")
            report["all_ok"] = False
    return report


def verify_video(path: Path) -> dict:
    print(f"[verify-v366] ============================================")
    print(f"[verify-v366] target = {path}")
    print(f"[verify-v366] ============================================")

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

    # 链式一致性 + meta 字段
    qa_dir = ROOT / "output" / "pipeline_v36" / "qa_frames_v366" / "chain"
    chain_check = chain_consistency_check(DEFAULT_CLIPS_DIR, qa_dir)
    meta_check = per_shot_meta_check(DEFAULT_SHOTS_DIR)

    all_ok = (v_dur_ok and v_frames_ok and a_av_match
              and res_ok and pix_ok and sample_ok
              and first_ok
              and chain_check["all_pairs_ok"]
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
        "chain_consistency": chain_check,
        "per_shot_meta_check": meta_check,
        "yavg_min_threshold": YAVG_MIN_THRESHOLD,
        "yavg_first_max_threshold": YAVG_FIRST_MAX_THRESHOLD,
        "vs_v365_changes": [
            "horizontal 1344x576 (not vertical 768x1344)",
            "chain_first_frame_i2v (not blank T2V)",
            "no in-prompt transition (not 5 unique effects)",
            "unified BGM only at end (not per-segment)",
            "no abstract transition words in prompt",
        ],
    }

    print(f"[verify-v366] format.duration       = {fmt_dur:.4f}s")
    print(f"[verify-v366] video stream duration = {v_dur:.4f}s  "
          f"{'PASS' if v_dur_ok else 'FAIL'}  "
          f"(expected {EXPECTED_VIDEO_DURATION_SEC} ± "
          f"{DURATION_TOLERANCE_SEC}s, Δ={v_dur_delta:+.4f}s)")
    print(f"[verify-v366] video stream nb_frames= {v_frames}    "
          f"{'PASS' if v_frames_ok else 'FAIL'}  "
          f"(expected ≈ {EXPECTED_VIDEO_NB_FRAMES} = "
          f"{EXPECTED_VIDEO_DURATION_SEC}×{EXPECTED_FPS}fps, "
          f"Δ={v_frames_delta:+d})")
    if a_stream:
        print(f"[verify-v366] audio stream duration = {a_dur:.4f}s  "
              f"{'PASS' if a_av_match else 'FAIL'}")
    print(f"[verify-v366] resolution            = {v_width}x{v_height}  "
          f"{'PASS' if res_ok else 'FAIL'}  "
          f"(expected {EXPECTED_WIDTH}x{EXPECTED_HEIGHT})")
    print(f"[verify-v366] pix_fmt               = {v_pix_fmt}  "
          f"{'PASS' if pix_ok else 'FAIL'}  (expected {EXPECTED_PIX_FMT})")
    print(f"[verify-v366] first frame YAVG      = {first_yavg}  "
          f"{'PASS' if first_ok else 'FAIL'}  "
          f"(expected {YAVG_MIN_THRESHOLD} < Y < "
          f"{YAVG_FIRST_MAX_THRESHOLD})")
    print(f"[verify-v366] sample frame YAVG:")
    for sr in sample_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        note = sr.get("note", "")
        print(f"[verify-v366]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}  {note}")
    print(f"[verify-v366] chain consistency (相邻段尾/首帧 直方图余弦, "
          f"min={CHAIN_SIMILARITY_MIN}):")
    for p in chain_check["pairs"]:
        cos = p["cosine_similarity"]
        print(f"[verify-v366]   {p['between']}: cos={cos:.4f}  "
              f"{'PASS' if p['ok'] else 'FAIL'}")
    if chain_check.get("min_similarity") is not None:
        print(f"[verify-v366]   min={chain_check['min_similarity']:.4f} "
              f"mean={chain_check['mean_similarity']:.4f}")
    if chain_check.get("errors"):
        for e in chain_check["errors"]:
            print(f"[verify-v366]   chain error: {e}")
    print(f"[verify-v366] per_shot_meta_check:")
    for idx, info in meta_check["per_shot"].items():
        print(f"[verify-v366]   shot{idx:02d}: phase={info['phase']} "
              f"dur={info['duration_sec']:.1f}s "
              f"method={info['method']} "
              f"anchor={info['anchor_source']} "
              f"transition={info['transition_effect']} "
              f"yavg_ok={info['yavg_ok']}")
    if meta_check.get("errors"):
        for e in meta_check["errors"]:
            print(f"[verify-v366]   meta error: {e}")
    print(f"[verify-v366] OVERALL: {'PASS' if all_ok else 'FAIL'}")
    print()
    print(f"[verify-v366] vs prior versions:")
    print(f"[verify-v366]   v365 : 45.7s / 1097 fr / 768x1344 / yuv420p "
          f"(blank T2V + 5 in-prompt transitions)")
    print(f"[verify-v366]   v366 : {v_dur:.3f}s / {v_frames} fr / "
          f"{v_width}x{v_height} / {v_pix_fmt} ({'OK' if all_ok else 'BUG'}) "
          f"(chain I2V + unified BGM)")
    return summary


def main(argv=None) -> int:
    global DEFAULT_CLIPS_DIR, DEFAULT_SHOTS_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--clips-dir", default=str(DEFAULT_CLIPS_DIR))
    ap.add_argument("--shots-dir", default=str(DEFAULT_SHOTS_DIR))
    ap.add_argument("--json", default=None,
                    help="写出验证结果 JSON 到指定路径")
    args = ap.parse_args(argv)

    DEFAULT_CLIPS_DIR = Path(args.clips_dir)
    DEFAULT_SHOTS_DIR = Path(args.shots_dir)

    video_path = Path(args.video)
    summary = verify_video(video_path)

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[verify-v366] JSON → {json_path}")

    return 0 if summary.get("ok") else 5


if __name__ == "__main__":
    sys.exit(main())
