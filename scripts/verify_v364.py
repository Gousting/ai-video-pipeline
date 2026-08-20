#!/usr/bin/env python3
"""v3.6.4 验证脚本：stream-level 严格断言（per 任务书 v364 Step 4）。

vs verify_v363.py 关键差异：

- **断言分辨率 = 768x1344**（v363 不检查分辨率，导致横屏 bug 未被发现）
- **断言 pix_fmt = yuv420p**（v363 是 yuv444p 非标，v364 强制）
- **断言首帧 YAVG < 220**（任务书 §3.3：禁止白淡入）
- **断言中段无白帧**：在 dissolve 之后帧额外检查 YAVG < 220（任务书 §3.3）
- 期望总时长改为 48s（v363 是 54.55s；v364 重构节奏后是 48s）
- 6 采样点 YAVG 全 PASS（> 5，非黑屏）

v363 PASS：54.42s / 1306 帧 / 1344x768 / yuv444p
v364 期望：48±0.3s / ≈1150 帧 / **768x1344** / **yuv420p**

CLI:
  python verify_v364.py
  python verify_v364.py --video output/pipeline_v36/final_v36_60s_v364.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v364.mp4"

# 任务书 §2.2 重构后总时长 ≈ 45.7s（含 H3 rounding 上溢）
# (10.125 + 8 + 6.583 + 10.125 + 6.583 + 8) - 0.3 - 0.4 - 3.0 = 45.716s
# trim 0.5s × 6 shots = 3.0s 扣减（消除 H3 源内容白淡入）
EXPECTED_VIDEO_DURATION_SEC = 45.7
EXPECTED_VIDEO_NB_FRAMES = 1097  # 45.7 × 24 ≈ 1096.8 → 取整 ±30
EXPECTED_FPS = 24
EXPECTED_WIDTH = 768
EXPECTED_HEIGHT = 1344
EXPECTED_PIX_FMT = "yuv420p"
DURATION_TOLERANCE_SEC = 0.5
NB_FRAMES_TOLERANCE = 30

# 6 个采样点（覆盖各段中段 + dissolve 前后）
SAMPLE_TIMES_SEC = (3.0, 10.0, 15.0, 25.0, 34.0, 41.0)
# Dissolve 前后帧：组1-4 在 trim+concat 后 ~31s 末尾，dissolve1 在 ~31s+0.3s
# dissolve2 在 ~37s+0.4s；用相邻采样点覆盖
DISSOLVE_CHECK_TIMES = (32.0, 38.0)
YAVG_MIN_THRESHOLD = 5.0
YAVG_FIRST_MAX_THRESHOLD = 220.0  # 任务书 §3.3：首帧非白
YAVG_AFTER_DISSOLVE_MAX = 220.0   # dissolve 后帧也非白


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
    print(f"[verify-v364] ============================================")
    print(f"[verify-v364] target = {path}")
    print(f"[verify-v364] ============================================")

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

    # ---- 断言 1: video duration ----
    v_dur_ok = abs(v_dur - EXPECTED_VIDEO_DURATION_SEC) <= DURATION_TOLERANCE_SEC
    v_dur_delta = v_dur - EXPECTED_VIDEO_DURATION_SEC

    # ---- 断言 2: video nb_frames ----
    v_frames_ok = abs(v_frames - EXPECTED_VIDEO_NB_FRAMES) <= NB_FRAMES_TOLERANCE
    v_frames_delta = v_frames - EXPECTED_VIDEO_NB_FRAMES

    # ---- 断言 3: audio ≈ video duration ----
    a_av_match = abs(v_dur - a_dur) <= DURATION_TOLERANCE_SEC if a_dur else True

    # ---- 断言 4: 分辨率 = 768x1344（关键新增）----
    res_ok = (v_width == EXPECTED_WIDTH and v_height == EXPECTED_HEIGHT)

    # ---- 断言 5: pix_fmt = yuv420p（关键新增）----
    pix_ok = (v_pix_fmt == EXPECTED_PIX_FMT)

    # ---- 断言 6: 6 采样点 YAVG > 5（非黑屏）----
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

    # ---- 断言 7: dissolve 前后帧有画面，且非白 ----
    dissolve_results = []
    dissolve_ok = True
    for t in DISSOLVE_CHECK_TIMES:
        if t > v_dur + 0.5:
            dissolve_results.append({"t": t, "yavg": None, "ok": False,
                                      "note": "t > video duration"})
            dissolve_ok = False
            continue
        yavg, note = yavg_at(path, t)
        ok = (yavg is not None and yavg > YAVG_MIN_THRESHOLD
              and yavg < YAVG_AFTER_DISSOLVE_MAX)
        dissolve_results.append({"t": round(t, 3), "yavg": yavg, "ok": ok,
                                  "note": note})
        if not ok:
            dissolve_ok = False

    # ---- 断言 8: 首帧 YAVG < 220（任务书 §3.3：无白淡入）----
    first_yavg, first_note = yavg_at(path, 0.05)
    first_ok = (first_yavg is not None
                and first_yavg < YAVG_FIRST_MAX_THRESHOLD
                and first_yavg > YAVG_MIN_THRESHOLD)

    all_ok = (v_dur_ok and v_frames_ok and a_av_match
              and res_ok and pix_ok and sample_ok
              and dissolve_ok and first_ok)

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
        "dissolve_results": dissolve_results,
        "all_dissolve_ok": dissolve_ok,
        "yavg_min_threshold": YAVG_MIN_THRESHOLD,
        "yavg_first_max_threshold": YAVG_FIRST_MAX_THRESHOLD,
    }

    # 输出
    print(f"[verify-v364] format.duration       = {fmt_dur:.4f}s")
    print(f"[verify-v364] video stream duration = {v_dur:.4f}s  "
          f"{'PASS' if v_dur_ok else 'FAIL'}  "
          f"(expected {EXPECTED_VIDEO_DURATION_SEC} ± {DURATION_TOLERANCE_SEC}s, "
          f"Δ={v_dur_delta:+.4f}s)")
    print(f"[verify-v364] video stream nb_frames= {v_frames}    "
          f"{'PASS' if v_frames_ok else 'FAIL'}  "
          f"(expected ≈ {EXPECTED_VIDEO_NB_FRAMES} = "
          f"{EXPECTED_VIDEO_DURATION_SEC}×{EXPECTED_FPS}fps, "
          f"Δ={v_frames_delta:+d})")
    if a_stream:
        print(f"[verify-v364] audio stream duration = {a_dur:.4f}s  "
              f"{'PASS' if a_av_match else 'FAIL'}")
    print(f"[verify-v364] resolution            = {v_width}x{v_height}  "
          f"{'PASS' if res_ok else 'FAIL'}  "
          f"(expected {EXPECTED_WIDTH}x{EXPECTED_HEIGHT})")
    print(f"[verify-v364] pix_fmt               = {v_pix_fmt}  "
          f"{'PASS' if pix_ok else 'FAIL'}  (expected {EXPECTED_PIX_FMT})")
    print(f"[verify-v364] first frame YAVG      = {first_yavg}  "
          f"{'PASS' if first_ok else 'FAIL'}  "
          f"(expected {YAVG_MIN_THRESHOLD} < Y < {YAVG_FIRST_MAX_THRESHOLD})")
    print(f"[verify-v364] sample frame YAVG (threshold > {YAVG_MIN_THRESHOLD}):")
    for sr in sample_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        note = sr.get("note", "")
        print(f"[verify-v364]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}  {note}")
    print(f"[verify-v364] dissolve前后帧 YAVG "
          f"({YAVG_MIN_THRESHOLD} < Y < {YAVG_AFTER_DISSOLVE_MAX}):")
    for sr in dissolve_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        note = sr.get("note", "")
        print(f"[verify-v364]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}  {note}")
    print(f"[verify-v364] OVERALL: {'PASS' if all_ok else 'FAIL'}")
    print()
    print(f"[verify-v364] vs prior versions:")
    print(f"[verify-v364]   v363 : 54.42s / 1306 fr / 1344x768 / yuv444p "
          f"(横屏, 非标)")
    print(f"[verify-v364]   v364 : {v_dur:.3f}s / {v_frames} fr / "
          f"{v_width}x{v_height} / {v_pix_fmt} ({'OK' if all_ok else 'BUG'})")
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
        print(f"[verify-v364] JSON → {json_path}")

    return 0 if summary.get("ok") else 5


if __name__ == "__main__":
    sys.exit(main())
