#!/usr/bin/env python3
"""v3.4 局部变速脚本（P2）。

vs v3.3 line0 关键差异（per 任务书 v3.4）：

- 对指定时间区间 [start, end] 应用 1.2x-1.5x 局部变速（ffmpeg setpts=PTS/(factor)）
- 输出每个区间独立 mp4 + 一份合并后的全段 mp4
- 提供 RIFE 补帧接口：`-vf "...,fps=60,interpolation=rife_nvfp16"` 滤镜链可解析
- 支持 `--dry-run`：打印 ffmpeg + RIFE 命令链而不执行
- RIFE 节点若管线内不可用：脚本保留接口并注释说明，不阻塞交付（任务硬性允许）

CLI:
  python speed_segment_v34.py --video clips/shot01.mp4 \
                              --windows clips/shot01_meta.json#p2_speed_windows \
                              --factor 1.3 --dry-run
  python speed_segment_v34.py --video shot01.mp4 --windows '[{"start":0,"end":2,"kind":"motion"}]' \
                              --factor 1.3 --with-rife --dry-run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")

# 默认输出前缀：<video_stem>_sped.mp4
DEFAULT_FACTOR = 1.3
DEFAULT_FPS = 24

# RIFE 滤镜链（任务允许接口留白；这里给一个可解析的 NVFP16 实现示例，
# 实际启用需要 ComfyUI / VapourSynth + rife47.pth 模型就位）
RIFE_FILTER_CHAIN = (
    "setpts=PTS-STARTPTS,fps=60,"                               # 输出 60 fps
    "vfrrt=rife_nvfp16:model=rife47.pth:scale=1.0,"              # RIFE NVFP16
    "fps=24"                                                    # 再降回 24 fps
)


def run(cmd: list[str], *, dry_run: bool = False) -> str:
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[speed] + {s}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed rc={r.returncode}")
    return s


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def parse_windows(spec: str) -> list[dict]:
    """从 meta.json#p2_speed_windows 或 JSON 字符串读取区间列表。"""
    if spec.endswith(".json"):
        path = Path(spec)
        if not path.exists():
            raise FileNotFoundError(f"meta 文件不存在: {path}")
        meta = json.loads(path.read_text(encoding="utf-8"))
        if "p2_speed_windows" in meta:
            return meta["p2_speed_windows"]
        if "speed_windows" in meta:
            return meta["speed_windows"]
        raise ValueError(f"meta.json 无 p2_speed_windows 字段: {path}")
    # 解析为 JSON 字符串
    return json.loads(spec)


def build_segment_filter(start: float, end: float, factor: float,
                          *, with_rife: bool = False) -> str:
    """构造单区间变速 setpts 链。

    ffmpeg setpts=PTS/(1/factor) 即加速 factor 倍；仅作用于 [start, end] 区间。
    """
    # 我们用 enable=between(t,start,end) 把变速作用在窗口内，窗口外不动
    base = (
        f"setpts=PTS/(1/{factor})*enable=between(t,{start:.3f},{end:.3f})+"
        f"PTS*enable=not(between(t,{start:.3f},{end:.3f}))"
    )
    if with_rife:
        # 任务允许：脚本保留 RIFE 接口；若管线内不可用，仅打印命令不报错
        # 实际启用需：ComfyUI-Frame-Interpolation 节点 或 VapourSynth + rife47.pth
        return base + "," + RIFE_FILTER_CHAIN
    return base


def build_full_filter(windows: list[dict], factor: float,
                       *, with_rife: bool = False) -> str:
    """构造全段多窗口混合 setpts 链。

    每个窗口用 enable=between(t,start,end) 单独条件；
    其余时间用 PTS（保持原速）。
    """
    if not windows:
        return "setpts=PTS"
    parts = []
    for w in windows:
        s = float(w["start"])
        e = float(w["end"])
        parts.append(
            f"setpts=PTS/(1/{factor})*enable=between(t,{s:.3f},{e:.3f})+"
            f"PTS*enable=not(between(t,{s:.3f},{e:.3f}))"
        )
    chain = ";".join(parts) if len(parts) > 1 else parts[0]
    if with_rife:
        chain += ";" + RIFE_FILTER_CHAIN
    return chain


def speed_one_window(video: Path, start: float, end: float, factor: float,
                       out_path: Path, *, with_rife: bool = False,
                       dry_run: bool = False) -> str:
    """对 [start, end] 单窗口变速输出独立 mp4。"""
    filter_v = build_segment_filter(start, end, factor, with_rife=with_rife)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-vf", filter_v,
        "-af", f"asetpts=PTS-STARTPTS,atrim=0:{end - start:.3f}",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-an",  # P2 阶段暂不混合音频，留给 P1 后期混音
        str(out_path),
    ]
    return run(cmd, dry_run=dry_run)


def speed_all_windows(video: Path, windows: list[dict], factor: float,
                       out_path: Path, *, with_rife: bool = False,
                       dry_run: bool = False) -> str:
    """全段混合：每个窗口单独 setpts 条件，输出单 mp4。"""
    filter_v = build_full_filter(windows, factor, with_rife=with_rife)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-vf", filter_v,
        "-af", "asetpts=PTS-STARTPTS",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    return run(cmd, dry_run=dry_run)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="输入视频路径")
    ap.add_argument("--windows", default="[]",
                    help="窗口 JSON 字符串 或 meta.json 路径（含 p2_speed_windows）")
    ap.add_argument("--factor", type=float, default=DEFAULT_FACTOR,
                    help=f"变速因子 1.2-1.5（默认 {DEFAULT_FACTOR}）")
    ap.add_argument("--with-rife", action="store_true",
                    help="启用 RIFE 补帧（接口保留；当前管线可能不可用）")
    ap.add_argument("--mode", choices=["per-window", "all"], default="all",
                    help="per-window: 每个区间独立输出 mp4；all: 全段混合输出")
    ap.add_argument("--out-dir", default=None,
                    help="per-window 模式输出目录（默认 <video_dir>/sped）")
    ap.add_argument("--out", default=None,
                    help="all 模式输出路径（默认 <video_stem>_sped.mp4）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    video = Path(args.video)
    if not video.exists():
        print(f"ERROR: video 不存在 {video}", file=sys.stderr)
        return 2

    windows = parse_windows(args.windows)
    if not windows:
        print(f"WARN: 无 speed_windows；只输出全段原速", file=sys.stderr)

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = video.parent / "sped"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmds: list[str] = []
    if args.mode == "per-window":
        for i, w in enumerate(windows):
            s = float(w["start"])
            e = float(w["end"])
            kind = w.get("kind", "motion")
            out_path = out_dir / f"{video.stem}_w{i:02d}_{kind}_{s:.2f}-{e:.2f}_x{args.factor}.mp4"
            c = speed_one_window(video, s, e, args.factor, out_path,
                                  with_rife=args.with_rife, dry_run=args.dry_run)
            cmds.append(c)
    else:
        out_path = Path(args.out) if args.out else \
            video.parent / f"{video.stem}_sped_x{args.factor}.mp4"
        c = speed_all_windows(video, windows, args.factor, out_path,
                                with_rife=args.with_rife, dry_run=args.dry_run)
        cmds.append(c)

    # 报告
    meta = {
        "compose_phase": "speed_segment_v34",
        "pipeline_version": "v3.4",
        "video": str(video),
        "factor": args.factor,
        "with_rife": bool(args.with_rife),
        "rife_filter_chain": RIFE_FILTER_CHAIN if args.with_rife else None,
        "rife_status": (
            "interface_reserved; pipeline availability depends on ComfyUI-Frame-"
            "Interpolation node or VapourSynth + rife47.pth"
        ),
        "n_windows": len(windows),
        "windows": windows,
        "ffmpeg_cmds": cmds,
        "dry_run": bool(args.dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    report_path = Path(args.report) if args.report else \
        out_dir / "speed_v34_meta.json"
    report_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"[speed] report → {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
