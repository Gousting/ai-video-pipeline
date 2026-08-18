#!/usr/bin/env python3
"""v3.2 拼接 v2：concat demuxer（无 xfade）→ 再叠加 xfade 处理。

策略（更稳定）：
  - Pass 1: concat demuxer 直接拼接 6 段（无 xfade，硬切）
  - Pass 2: 不需要单独 xfade — 因为 prompt 内转场已生成视觉转场帧
  - 输出 concat_no_overlay.mp4

CLI:
  python concat_v32.py
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
CLIPS_DIR = ROOT / "output" / "pipeline_v3" / "clips_v32"
SB_PATH = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard_v32.json"
OUT = ROOT / "output" / "pipeline_v3" / "clips_v32" / "concat_no_overlay.mp4"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg 失败 rc={r.returncode}")
    return r


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=str(SB_PATH))
    ap.add_argument("--clips-dir", default=str(CLIPS_DIR))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    sb_path = Path(args.storyboard)
    clips_dir = Path(args.clips_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not sb_path.exists():
        print(f"ERROR: storyboard 不存在 {sb_path}", file=sys.stderr)
        return 2

    sb = json.loads(sb_path.read_text(encoding="utf-8"))
    shots = sb["shots"]

    # 验证 + 写 concat list
    list_file = clips_dir / "concat_list.txt"
    durations = []
    with list_file.open("w", encoding="utf-8") as f:
        for shot in shots:
            idx = shot["index"]
            clip = clips_dir / f"shot{idx:02d}.mp4"
            if not clip.exists():
                print(f"ERROR: shot{idx:02d} 视频不存在 {clip}", file=sys.stderr)
                return 2
            d = ffprobe_duration(clip)
            durations.append(d)
            f.write(f"file '{clip.as_posix()}'\n")
            print(f"  shot{idx:02d}: {d:.3f}s -> {clip.name}", flush=True)

    # 用 concat demuxer 拼接（drop 原始 audio — 后面会替换成 v32 BGM+ambient+whoosh）
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-vf", "scale=720:1280:flags=lanczos,setsar=1:1",
        "-an",  # 不要原音轨（v32 BGM 全片统一）
        "-r", "24",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run(cmd)

    final_dur = ffprobe_duration(out_path)
    expected = sum(durations)
    print(f"[concat] final -> {out_path} ({final_dur:.3f}s)", flush=True)
    print(f"[concat] expected={expected:.3f}s actual={final_dur:.3f}s "
          f"diff={final_dur - expected:.3f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
