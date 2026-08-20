#!/usr/bin/env python3
"""v3.6.3 验证脚本：stream-level 严格断言（per 任务书 v363 Step 2）。

强制断言（**禁止只信 format.duration**，那是容器级总时长，可能被音频骗）：

1. video stream duration ≈ 54.55s ± 0.3s（方案 B 比 v362 略多 MARGIN 边距）
2. video stream nb_frames ≈ 1310（54.55s × 24fps ± 30）
3. audio stream duration ≈ 视频 duration（±0.3s）
4. 6 个采样点（3/12/25/38/48/53s）全部 YAVG > 5（非黑屏）
5. dissolve 前后帧（39s / 51s 附近）有画面

v36 错版症状：video duration 10.2s / nb_frames 245（截断到第一段）
v361 错版症状：video duration 109.5s / nb_frames 2627（翻倍重复）
v362 错版症状：同样 10.2s / nb_frames 245（xfade 边界 bug）
v363 期望（方案 B）：video duration 54.55s / nb_frames ≈ 1310

CLI:
  python verify_v363.py
  python verify_v363.py --video output/pipeline_v36/final_v36_60s_v363.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v363.mp4"

# 期望值（方案 B 手算 + ffmpeg xfade 编码开销微调）
# 总时长 = 10.125 + 4*8.75 + 10.125 - 0.3 - 0.4 = 54.55s
EXPECTED_VIDEO_DURATION_SEC = 54.55
EXPECTED_VIDEO_NB_FRAMES = 1310      # 54.55 × 24 ≈ 1309.2 → 取整 ±30
EXPECTED_FPS = 24
DURATION_TOLERANCE_SEC = 0.3
NB_FRAMES_TOLERANCE = 30

# 6 个时间采样点（覆盖 3 段头/转场附近 + 末段）
SAMPLE_TIMES_SEC = (3.0, 12.0, 25.0, 38.0, 48.0, 53.0)
# Dissolve 前后帧：shot4→shot5 dissolve 在 36.075s 起点；shot5→shot6 在 44.425s
DISSOLVE_CHECK_TIMES = (39.0, 51.0)
YAVG_MIN_THRESHOLD = 5.0


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
    """抽 1 帧测 YAVG（亮度均值）。"""
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


def verify_video(path: Path) -> dict:
    print(f"[verify-v363] ============================================")
    print(f"[verify-v363] target = {path}")
    print(f"[verify-v363] ============================================")

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
    except (ValueError, TypeError):
        return {"ok": False, "reason": "video duration/nb_frames parse failed"}

    a_dur = float(a_stream.get("duration", "0") or 0) if a_stream else 0.0
    fmt_dur = float(fmt.get("duration", "0") or 0)

    # ---- 断言 1: video duration ----
    v_dur_ok = abs(v_dur - EXPECTED_VIDEO_DURATION_SEC) <= DURATION_TOLERANCE_SEC
    v_dur_delta = v_dur - EXPECTED_VIDEO_DURATION_SEC

    # ---- 断言 2: video nb_frames ----
    v_frames_ok = abs(v_frames - EXPECTED_VIDEO_NB_FRAMES) <= NB_FRAMES_TOLERANCE
    v_frames_delta = v_frames - EXPECTED_VIDEO_NB_FRAMES

    # ---- 断言 3: audio ≈ video duration ----
    a_av_match = abs(v_dur - a_dur) <= DURATION_TOLERANCE_SEC if a_dur else True

    # ---- 断言 4: 6 采样点 YAVG > 5（非黑屏）----
    sample_results = []
    sample_ok = True
    for t in SAMPLE_TIMES_SEC:
        if t > v_dur + 0.5:
            sample_results.append({"t": t, "yavg": None, "ok": False,
                                    "note": "t > video duration"})
            sample_ok = False
            continue
        yavg, note = yavg_at(path, t)
        ok = (yavg is not None and yavg > YAVG_MIN_THRESHOLD)
        sample_results.append({"t": round(t, 3), "yavg": yavg, "ok": ok,
                                "note": note})
        if not ok:
            sample_ok = False

    # ---- 断言 5: dissolve 前后帧有画面 ----
    dissolve_results = []
    dissolve_ok = True
    for t in DISSOLVE_CHECK_TIMES:
        if t > v_dur + 0.5:
            dissolve_results.append({"t": t, "yavg": None, "ok": False,
                                     "note": "t > video duration"})
            dissolve_ok = False
            continue
        yavg, note = yavg_at(path, t)
        ok = (yavg is not None and yavg > YAVG_MIN_THRESHOLD)
        dissolve_results.append({"t": round(t, 3), "yavg": yavg, "ok": ok,
                                  "note": note})
        if not ok:
            dissolve_ok = False

    all_ok = (v_dur_ok and v_frames_ok and a_av_match
              and sample_ok and dissolve_ok)

    summary = {
        "ok": bool(all_ok),
        "target": str(path),
        "format_duration_sec": round(fmt_dur, 4),
        "video_stream_duration_sec": round(v_dur, 4),
        "video_stream_nb_frames": v_frames,
        "audio_stream_duration_sec": round(a_dur, 4),
        "expected_video_duration_sec": EXPECTED_VIDEO_DURATION_SEC,
        "expected_video_nb_frames": EXPECTED_VIDEO_NB_FRAMES,
        "duration_tolerance_sec": DURATION_TOLERANCE_SEC,
        "nb_frames_tolerance": NB_FRAMES_TOLERANCE,
        "video_duration_ok": v_dur_ok,
        "video_duration_delta": round(v_dur_delta, 4),
        "video_nb_frames_ok": v_frames_ok,
        "video_nb_frames_delta": v_frames_delta,
        "audio_av_match": a_av_match,
        "sample_results": sample_results,
        "all_samples_ok": sample_ok,
        "dissolve_results": dissolve_results,
        "all_dissolve_ok": dissolve_ok,
        "yavg_threshold": YAVG_MIN_THRESHOLD,
    }

    # 输出
    print(f"[verify-v363] format.duration       = {fmt_dur:.4f}s  (容器级 — 可被音频骗)")
    print(f"[verify-v363] video stream duration = {v_dur:.4f}s  "
          f"{'PASS' if v_dur_ok else 'FAIL'}  "
          f"(expected {EXPECTED_VIDEO_DURATION_SEC} ± {DURATION_TOLERANCE_SEC}s, "
          f"Δ={v_dur_delta:+.4f}s)")
    print(f"[verify-v363] video stream nb_frames= {v_frames}    "
          f"{'PASS' if v_frames_ok else 'FAIL'}  "
          f"(expected ≈ {EXPECTED_VIDEO_NB_FRAMES} = "
          f"{EXPECTED_VIDEO_DURATION_SEC}×{EXPECTED_FPS}fps, "
          f"Δ={v_frames_delta:+d})")
    if a_stream:
        print(f"[verify-v363] audio stream duration = {a_dur:.4f}s  "
              f"{'PASS' if a_av_match else 'FAIL'}  "
              f"(video ± {DURATION_TOLERANCE_SEC}s)")
    print(f"[verify-v363] sample frame YAVG (threshold > {YAVG_MIN_THRESHOLD}):")
    for sr in sample_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        note = sr.get("note", "")
        print(f"[verify-v363]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}  {note}")
    print(f"[verify-v363] dissolve前后帧 YAVG (threshold > {YAVG_MIN_THRESHOLD}):")
    for sr in dissolve_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        note = sr.get("note", "")
        print(f"[verify-v363]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}  {note}")
    print(f"[verify-v363] OVERALL: {'PASS' if all_ok else 'FAIL'}")
    print()
    print(f"[verify-v363] vs prior failures:")
    print(f"[verify-v363]   v36  : video duration=10.2s nb_frames=245 (FAIL 截断)")
    print(f"[verify-v363]   v361 : video duration=109.5s nb_frames=2627 (FAIL 翻倍)")
    print(f"[verify-v363]   v362 : video duration=10.2s nb_frames=245 (FAIL xfade 边界)")
    print(f"[verify-v363]   v363 : video duration={v_dur:.3f}s "
          f"nb_frames={v_frames} ({'OK' if all_ok else 'BUG'})")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--json", default=None,
                    help="写出验证结果 JSON 到指定路径")
    args = ap.parse_args(argv)

    video_path = Path(args.video)
    summary = verify_video(video_path)

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[verify-v363] JSON → {json_path}")

    return 0 if summary.get("ok") else 5


if __name__ == "__main__":
    sys.exit(main())
