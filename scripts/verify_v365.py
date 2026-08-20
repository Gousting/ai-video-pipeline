#!/usr/bin/env python3
"""v3.6.5 验证脚本：stream-level 严格断言 + 转场特效元数据对账。

vs verify_v364.py 关键差异：

- 默认视频路径改为 `output/pipeline_v36/final_v36_60s_v365.mp4`
- 输出 JSON 路径改为 `output/pipeline_v36/verify_v365_summary.json`
- 新增 `transition_metadata_check`：从 `clips_v365/shot{NN}_meta.json`
  读 `transition_effect` + `has_in_prompt_transition`，断言：
    * shot02-06 每段都有非空 transition_effect
    * shot01 transition_effect 必须为 None (净开场)
    * 5 个 transition_effect 全片去重
    * 5 个 transition_effect 都属于 TRANSITIONS_BLOCK 词库
- 断言分辨率 / pix_fmt / 首帧 YAVG / 6 采样点 / dissolve 前后帧（沿用 v364）

CLI:
  python verify_v365.py
  python verify_v365.py --video output/pipeline_v36/final_v36_60s_v365.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_VIDEO = ROOT / "output" / "pipeline_v36" / "final_v36_60s_v365.mp4"
DEFAULT_CLIPS_DIR = ROOT / "output" / "pipeline_v36" / "clips_v365"
DEFAULT_CHAR_BLOCKS = ROOT / "output" / "pipeline_v36" / "shots_v365" / "char_blocks_v365.json"

EXPECTED_VIDEO_DURATION_SEC = 45.7
EXPECTED_VIDEO_NB_FRAMES = 1097
EXPECTED_FPS = 24
EXPECTED_WIDTH = 768
EXPECTED_HEIGHT = 1344
EXPECTED_PIX_FMT = "yuv420p"
DURATION_TOLERANCE_SEC = 0.5
NB_FRAMES_TOLERANCE = 30

SAMPLE_TIMES_SEC = (3.0, 10.0, 15.0, 25.0, 34.0, 41.0)
DISSOLVE_CHECK_TIMES = (32.0, 38.0)
YAVG_MIN_THRESHOLD = 5.0
YAVG_FIRST_MAX_THRESHOLD = 220.0
YAVG_AFTER_DISSOLVE_MAX = 220.0


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


def load_transitions_block() -> list[str]:
    """从 char_blocks_v365.json 抽 TRANSITIONS_BLOCK 词库 (7 个效果, 逗号切分)。"""
    if not DEFAULT_CHAR_BLOCKS.exists():
        return []
    j = json.loads(DEFAULT_CHAR_BLOCKS.read_text(encoding="utf-8"))
    block = j.get("TRANSITIONS_BLOCK", "")
    parts = [p.strip() for p in block.split(",") if p.strip()]
    return parts


def check_transition_metadata(clips_dir: Path) -> dict:
    """从每段 shot meta 抽 transition_effect，做全片去重 + 词库对账。

    clips_dir 参数实际不被使用：meta 文件在 shots_v365/ 下, 不在 clips_v365/.
    保留参数仅为接口一致性 (CLI --clips-dir).
    """
    report = {
        "clips_dir": str(clips_dir),
        "shots_dir": str(ROOT / "output" / "pipeline_v36" / "shots_v365"),
        "n_clips": 0,
        "per_shot": {},
        "shot01_no_transition_ok": False,
        "shot02_to_06_have_transition_ok": False,
        "transitions_unique_ok": False,
        "transitions_in_block_ok": False,
        "transitions_used": [],
        "transitions_block": load_transitions_block(),
        "errors": [],
    }
    shots_dir = ROOT / "output" / "pipeline_v36" / "shots_v365"
    if not shots_dir.exists():
        report["errors"].append(f"shots dir not found: {shots_dir}")
        return report

    seen: list[str] = []
    for cp in sorted(shots_dir.glob("shot*_meta.json"),
                     key=lambda p: int(p.stem.split("_")[0].replace("shot", ""))):
        # shot01_meta.json 这种格式
        shot_idx = int(cp.stem.split("_")[0].replace("shot", ""))
        report["n_clips"] += 1
        try:
            m = json.loads(cp.read_text(encoding="utf-8-sig"))
        except Exception as e:
            report["errors"].append(f"{cp.name}: parse failed: {e}")
            continue
        te = m.get("transition_effect")
        report["per_shot"][shot_idx] = {
            "transition_effect": te,
            "has_in_prompt_transition": m.get("has_in_prompt_transition"),
        }
        if te and te not in seen:
            seen.append(te)
    report["transitions_used"] = seen

    # shot01 must be None
    s1 = report["per_shot"].get(1, {})
    report["shot01_no_transition_ok"] = (s1.get("transition_effect") is None)

    # shot02-06 must each have a non-empty transition
    have_all = all(
        report["per_shot"].get(i, {}).get("transition_effect")
        for i in range(2, 7)
    )
    report["shot02_to_06_have_transition_ok"] = have_all

    # 全片去重
    report["transitions_unique_ok"] = (
        len(seen) == 5 and len(set(seen)) == 5
    )

    # 词库对账
    block = report["transitions_block"]
    in_block = all(any(e.lower() in b.lower() for b in block) for e in seen)
    report["transitions_in_block_ok"] = in_block

    report["ok"] = (
        report["shot01_no_transition_ok"]
        and report["shot02_to_06_have_transition_ok"]
        and report["transitions_unique_ok"]
        and report["transitions_in_block_ok"]
        and not report["errors"]
    )
    return report


def verify_video(path: Path) -> dict:
    print(f"[verify-v365] ============================================")
    print(f"[verify-v365] target = {path}")
    print(f"[verify-v365] ============================================")

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
        ok = (yavg is not None and yavg > YAVG_MIN_THRESHOLD)
        sample_results.append({"t": round(t, 3), "yavg": yavg, "ok": ok,
                                "note": note})
        if not ok:
            sample_ok = False

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

    first_yavg, first_note = yavg_at(path, 0.05)
    first_ok = (first_yavg is not None
                and first_yavg < YAVG_FIRST_MAX_THRESHOLD
                and first_yavg > YAVG_MIN_THRESHOLD)

    # 转场元数据检查
    tr_check = check_transition_metadata(DEFAULT_CLIPS_DIR)

    all_ok = (v_dur_ok and v_frames_ok and a_av_match
              and res_ok and pix_ok and sample_ok
              and dissolve_ok and first_ok
              and tr_check.get("ok", False))

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
        "transition_metadata_check": tr_check,
        "vs_v364_changes": [
            "transition_metadata_check added (shot01 None / shot02-06 unique / in block)",
            "clips_v365/shot{NN}_meta.json expected to have transition_effect",
        ],
    }

    print(f"[verify-v365] format.duration       = {fmt_dur:.4f}s")
    print(f"[verify-v365] video stream duration = {v_dur:.4f}s  "
          f"{'PASS' if v_dur_ok else 'FAIL'}  "
          f"(expected {EXPECTED_VIDEO_DURATION_SEC} ± {DURATION_TOLERANCE_SEC}s, "
          f"Δ={v_dur_delta:+.4f}s)")
    print(f"[verify-v365] video stream nb_frames= {v_frames}    "
          f"{'PASS' if v_frames_ok else 'FAIL'}  "
          f"(expected ≈ {EXPECTED_VIDEO_NB_FRAMES} = "
          f"{EXPECTED_VIDEO_DURATION_SEC}×{EXPECTED_FPS}fps, "
          f"Δ={v_frames_delta:+d})")
    if a_stream:
        print(f"[verify-v365] audio stream duration = {a_dur:.4f}s  "
              f"{'PASS' if a_av_match else 'FAIL'}")
    print(f"[verify-v365] resolution            = {v_width}x{v_height}  "
          f"{'PASS' if res_ok else 'FAIL'}  "
          f"(expected {EXPECTED_WIDTH}x{EXPECTED_HEIGHT})")
    print(f"[verify-v365] pix_fmt               = {v_pix_fmt}  "
          f"{'PASS' if pix_ok else 'FAIL'}  (expected {EXPECTED_PIX_FMT})")
    print(f"[verify-v365] first frame YAVG      = {first_yavg}  "
          f"{'PASS' if first_ok else 'FAIL'}  "
          f"(expected {YAVG_MIN_THRESHOLD} < Y < {YAVG_FIRST_MAX_THRESHOLD})")
    print(f"[verify-v365] sample frame YAVG (threshold > {YAVG_MIN_THRESHOLD}):")
    for sr in sample_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        note = sr.get("note", "")
        print(f"[verify-v365]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}  {note}")
    print(f"[verify-v365] dissolve前后帧 YAVG "
          f"({YAVG_MIN_THRESHOLD} < Y < {YAVG_AFTER_DISSOLVE_MAX}):")
    for sr in dissolve_results:
        ya = sr["yavg"]
        ya_str = f"{ya:.2f}" if ya is not None else "N/A"
        note = sr.get("note", "")
        print(f"[verify-v365]   t={sr['t']:5.1f}s  YAVG={ya_str:>7}  "
              f"{'PASS' if sr['ok'] else 'FAIL'}  {note}")
    print(f"[verify-v365] transition metadata check:")
    print(f"[verify-v365]   shot01 no transition       = "
          f"{'PASS' if tr_check['shot01_no_transition_ok'] else 'FAIL'}")
    print(f"[verify-v365]   shot02-06 have transition  = "
          f"{'PASS' if tr_check['shot02_to_06_have_transition_ok'] else 'FAIL'}")
    print(f"[verify-v365]   transitions unique (5)     = "
          f"{'PASS' if tr_check['transitions_unique_ok'] else 'FAIL'}")
    print(f"[verify-v365]   transitions in block       = "
          f"{'PASS' if tr_check['transitions_in_block_ok'] else 'FAIL'}")
    print(f"[verify-v365]   used ({len(tr_check['transitions_used'])}): "
          f"{tr_check['transitions_used']}")
    if tr_check.get("errors"):
        print(f"[verify-v365]   errors: {tr_check['errors']}")
    print(f"[verify-v365] OVERALL: {'PASS' if all_ok else 'FAIL'}")
    print()
    print(f"[verify-v365] vs prior versions:")
    print(f"[verify-v365]   v364 : 45.58s / 1094 fr / 768x1344 / yuv420p (no in-prompt transition)")
    print(f"[verify-v365]   v365 : {v_dur:.3f}s / {v_frames} fr / "
          f"{v_width}x{v_height} / {v_pix_fmt} ({'OK' if all_ok else 'BUG'}) "
          f"+ 5 unique in-prompt transitions")
    return summary


def main(argv=None) -> int:
    global DEFAULT_CLIPS_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--clips-dir", default=str(DEFAULT_CLIPS_DIR))
    ap.add_argument("--json", default=None,
                    help="写出验证结果 JSON 到指定路径")
    args = ap.parse_args(argv)

    DEFAULT_CLIPS_DIR = Path(args.clips_dir)

    video_path = Path(args.video)
    summary = verify_video(video_path)

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[verify-v365] JSON → {json_path}")

    return 0 if summary.get("ok") else 5


if __name__ == "__main__":
    sys.exit(main())
