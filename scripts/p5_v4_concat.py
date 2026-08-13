#!/usr/bin/env python3
"""P5-v4 拼接：shot1+shot2+shot3 concat + 音频 acrossfade 0.25s -> video_silent_v4.mp4。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
CLIPS = ROOT / "output" / "clips_v4"
OUT = ROOT / "output" / "out"
META = ROOT / "output" / "tmp" / "p5v4_concat_meta.json"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise RuntimeError(f"命令失败 rc={r.returncode}")


def ffprobe_json(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,codec_name,width,height,duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return json.loads(r.stdout)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    src = [CLIPS / f"shot{i}.mp4" for i in (1, 2, 3)]
    dst = OUT / "video_silent_v4.mp4"

    run([
        "ffmpeg", "-y",
        "-i", str(src[0]), "-i", str(src[1]), "-i", str(src[2]),
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[vout];"
        "[0:a][1:a]acrossfade=d=0.25[a01];"
        "[a01][2:a]acrossfade=d=0.25[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(dst),
    ])

    info = ffprobe_json(dst)
    META.write_text(json.dumps({"output": str(dst), "probe": info}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[concat] 完成 -> {dst} 时长 {info['format']['duration']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
