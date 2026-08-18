#!/usr/bin/env python3
"""v3.1 final orchestrator：编排"concat + overlay + audio + VLM 评分"四步流程。

v3.1 vs v3 编排差异：
  - concat：复用 v3_final.py 的 concat_via_xfade（无改动）
  - overlay：复用 v3_final.py 的 burn_overlays（无改动）
  - audio：改用 v31_audio.py（统一 BGM + edge-tts + 混音）
  - VLM 评分：复用 v3_final.py 的 score_v3（无改动）

CLI:
  python v31_concat.py                           # 完整流程
  python v31_concat.py --skip-score              # 只拼片+混音，不评分
  python v31_concat.py --start 2 --end 5         # 只用部分 shot
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
CLIPS_DIR = ROOT / "output" / "pipeline_v3" / "clips"
SB_PATH = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard.json"
OVERLAYS_DIR = ROOT / "output" / "pipeline_v3" / "overlays"
TMP_DIR = ROOT / "output" / "pipeline_v3" / "tmp"
QA_DIR = ROOT / "output" / "pipeline_v3" / "qa"
FINAL_OUT = ROOT / "output" / "pipeline_v3" / "final_v3.mp4"
REF_VIDEO = ROOT / "input_douyin_ref.mp4"


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise RuntimeError(f"命令失败 rc={r.returncode}")
    return r


def concat_via_xfade(shots: list[Path], out: Path, tmp_dir: Path,
                     xfade_sec: float = 0.4) -> None:
    """v3_final.py.concat_via_xfade 的复刻 + 段间引导工艺修复版。

    v3 用 0.25s；v3.1 用 0.4s（验证：0.6s 会因混合帧糊化拉低画面质量）。

    xfade offset 累积算法：
      - 段 i（i >= 1）的 xfade offset = i × (seg_dur - xfade_sec)
        即每段起点位于上一段终点前 xfade_sec 处（产生 0.4s 重叠区）
      - 总输出时长 = n × seg_dur - (n-1) × xfade_sec
        8 段 × 8s - 7 × 0.4s = 64 - 2.8 = 61.2s
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    scaled = []
    for s in shots:
        sout = tmp_dir / f"scaled_{s.stem}.mp4"
        if not sout.exists():
            # 缩放到 720x1280 + 24fps 统一规格
            run([
                "ffmpeg", "-y", "-v", "error",
                "-i", str(s),
                "-vf", "scale=720:1280:flags=lanczos,fps=24",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-ar", "32000",
                str(sout),
            ])
        scaled.append(sout)
        print(f"[concat] scaled {s.name} -> {sout.name}", flush=True)

    n = len(scaled)
    if n == 1:
        shutil.copy(scaled[0], out)
        return

    seg_dur = 8.0  # 每段 8s（与 shot*.mp4 实际时长一致）

    # 构造 xfade filter_complex
    parts = []
    inputs = []
    for i, s in enumerate(scaled):
        inputs.extend(["-i", str(s)])
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")

    # 累积 offset 链式拼接（v3.1 修复版）
    last_v = "v0"
    last_a = "a0"
    for i in range(1, n):
        # 第 i 段在累积时间线上的起点 = i × (seg_dur - xfade_sec)
        offset = i * (seg_dur - xfade_sec)
        new_v = f"vx{i}"
        new_a = f"ax{i}"
        parts.append(
            f"[{last_v}][v{i}]xfade=duration={xfade_sec}:offset={offset:.3f}[{new_v}]"
        )
        parts.append(
            f"[{last_a}][a{i}]acrossfade=d={xfade_sec}[{new_a}]"
        )
        last_v = new_v
        last_a = new_a

    filter_complex = ";\n".join(parts)
    # 显式 -fflags +genpts 修复 container duration metadata（v3 已知 bug）
    cmd = ["ffmpeg", "-y", "-v", "error", "-fflags", "+genpts"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", f"[{last_v}]", "-map", f"[{last_a}]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-ar", "32000",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)
    print(f"[concat] done -> {out} ({out.stat().st_size / 1e6:.2f} MB)", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1, help="起始段（1-based）")
    ap.add_argument("--end", type=int, default=8, help="结束段（1-based）")
    ap.add_argument("--skip-concat", action="store_true")
    ap.add_argument("--skip-overlay", action="store_true")
    ap.add_argument("--skip-audio", action="store_true")
    ap.add_argument("--skip-score", action="store_true")
    ap.add_argument("--ref", default=str(REF_VIDEO))
    args = ap.parse_args(argv)

    # 1. 收集 shot 列表
    shots = []
    for idx in range(args.start, args.end + 1):
        sp = CLIPS_DIR / f"shot{idx:02d}.mp4"
        if not sp.exists() or sp.stat().st_size < 100_000:
            print(f"[main] WARN: {sp} 不存在或太小，跳过", flush=True)
            continue
        shots.append(sp)
    if not shots:
        print(f"ERROR: 无可用 shot 视频（{args.start}-{args.end}）", file=sys.stderr)
        return 2
    print(f"[main] 段列表: {[s.name for s in shots]}", flush=True)

    # 2. 拼接
    concat_video = CLIPS_DIR / "concat_no_overlay.mp4"
    if not args.skip_concat:
        concat_via_xfade(shots, concat_video, TMP_DIR / "concat_v31")
    if not concat_video.exists():
        print(f"ERROR: concat 视频 {concat_video} 不存在", file=sys.stderr)
        return 2
    print(f"[main] concat 视频: {concat_video} ({ffprobe_duration(concat_video):.2f}s)", flush=True)

    # 3. overlay（用 v31 burn_overlays；v3_final 版有 -shortest 截断 bug）
    video_with_overlay = CLIPS_DIR / "video_with_overlay.mp4"
    if not args.skip_overlay:
        sb = json.loads(SB_PATH.read_text(encoding="utf-8"))
        sys.path.insert(0, str(ROOT / "scripts"))
        from v31_burn_overlays import burn_overlays_v31
        burn_overlays_v31(concat_video, OVERLAYS_DIR, sb, video_with_overlay)
    if not video_with_overlay.exists():
        print(f"ERROR: overlay 视频 {video_with_overlay} 不存在", file=sys.stderr)
        return 2
    print(f"[main] overlay 视频: {video_with_overlay}", flush=True)

    # 4. 音频（v3.1 关键：统一 BGM）
    if not args.skip_audio:
        sys.path.insert(0, str(ROOT / "scripts"))
        from v31_audio import main as audio_main
        rc = audio_main([
            "--video", str(video_with_overlay),
            "--storyboard", str(SB_PATH),
            "--out", str(FINAL_OUT),
        ])
        if rc != 0:
            print(f"ERROR: audio 阶段失败 rc={rc}", file=sys.stderr)
            return rc

    if not FINAL_OUT.exists():
        print(f"ERROR: final_v3.mp4 不存在", file=sys.stderr)
        return 2
    final_dur = ffprobe_duration(FINAL_OUT)
    # Windows GBK 编码兼容：不使用 emoji
    print(f"[main] OK: final_v3.mp4 = {FINAL_OUT} ({final_dur:.2f}s, "
          f"{FINAL_OUT.stat().st_size/1e6:.2f} MB)", flush=True)

    # 5. VLM 评分（复用 v3_final.py）
    if not args.skip_score and REF_VIDEO.exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        from v3_final import score_v3
        score_v3(Path(args.ref), FINAL_OUT, QA_DIR)

    return 0


if __name__ == "__main__":
    sys.exit(main())