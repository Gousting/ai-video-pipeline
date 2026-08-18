#!/usr/bin/env python3
"""v3.1 段间引导工具：抽取视频尾帧（shot 末帧）作为下一段的 I2V first_frame。

基于官方"段间引导"工艺（H3 单段 15s 上限、60s+ 必分段）：
  shot01 末帧 → shot02 I2V first_frame
  shot02 末帧 → shot03 I2V first_frame
  ...

CLI:
  python v31_extract_tail.py --video shot01.mp4 --out links/shot01_last.png
  python v31_extract_tail.py --video shot02.mp4 --out links/shot02_last.png --offset 0.08
"""
import argparse
import subprocess
import sys
from pathlib import Path


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def extract_tail(video: Path, out: Path, offset_sec: float = 0.1,
                 width: int = 768, height: int = 1344) -> dict:
    """从视频末尾抽取一帧作为下一段引导。

    Args:
        video: 输入视频路径
        out: 输出 PNG 路径
        offset_sec: 距末尾多少秒取帧（默认 0.1s，避免黑边）
        width/height: 输出帧的目标分辨率（H3 默认 768x1344）

    Returns:
        {"video": str, "out": str, "duration": float, "offset": float}
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = ffprobe_duration(video)
    if dur <= 0:
        raise RuntimeError(f"无法获取视频时长: {video}")
    # 取倒数第二帧附近（最后一帧有时是淡出黑帧）
    seek_to = max(0.0, dur - offset_sec)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-sseof", f"-{offset_sec:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-update", "1",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg 抽帧失败 rc={r.returncode}")
    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError(f"抽帧失败: 输出文件无效 {out}")
    info = {
        "video": str(video),
        "out": str(out),
        "duration": round(dur, 3),
        "offset_sec": offset_sec,
        "size_bytes": out.stat().st_size,
    }
    print(f"[tail] {video.name} (dur={dur:.2f}s) -> {out} "
          f"(offset={offset_sec:.2f}s, size={out.stat().st_size//1024} KB)", flush=True)
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="输入视频")
    ap.add_argument("--out", required=True, help="输出 PNG 路径")
    ap.add_argument("--offset", type=float, default=0.1,
                    help="距末尾秒数（默认 0.1s，避免黑边）")
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1344)
    args = ap.parse_args(argv)

    info = extract_tail(Path(args.video), Path(args.out),
                        offset_sec=args.offset,
                        width=args.width, height=args.height)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import json
    sys.exit(main())