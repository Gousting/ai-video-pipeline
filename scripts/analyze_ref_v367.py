#!/usr/bin/env python3
"""v3.6.7 Step 0: 抽取参考视频场景切换关键帧 + 合成 filmstrip, 供 VLM 视觉识别用。

任务书 oc_task_v367.txt §2.1:
- 抽 8-12 个关键帧 (覆盖场景切换点 + 首帧 + 中段 + 尾段)
- 用 minimax-m3 VLM 识别, 输出结构化视觉风格档案
- 场景切换点 (任务书 §1): 1.37s / 3.5s / 10.6s / 12.27s / 14.03s / 15.67s / 20.87s / 24.97s / 27.27s / 29.2s

CLI:
    python analyze_ref_v367.py
    python analyze_ref_v367.py --times 0.5,1.37,3.5,...
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

ROOT = Path(r"D:\ai-video-pipeline")
REF_VIDEO = ROOT / "input_h3_pv_ref.mp4"
OUT_DIR = ROOT / "ref_analysis_v367"
FRAMES_DIR = OUT_DIR / "frames"

# 任务书 §1 列出的场景切换点 + 头尾补帧, 共 12 帧
DEFAULT_TIMES = [
    0.20,   # 首帧 (用于参考视频锚定)
    1.37,   # 第1切点
    3.50,   # 第2切点
    8.00,   # 中段
    10.60,  # 第3切点
    12.27,  # 第4切点
    14.03,  # 第5切点
    15.67,  # 第6切点
    20.87,  # 第7切点
    24.97,  # 第8切点
    27.27,  # 第9切点
    30.50,  # 尾段
]


def extract_frame(video: Path, t_sec: float, out_path: Path,
                  scale_w: int = 1344, scale_h: int = 576) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", (f"scale={scale_w}:{scale_h}:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={scale_w}:{scale_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1:1"),
        "-q:v", "2",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(
            f"抽帧失败 t={t_sec}s: {r.stderr[-300:] or r.stdout[-300:]}")
    return out_path


def build_filmstrip(frame_paths: list[Path], out_path: Path,
                    cols: int = 4, cell_w: int = 480) -> Path:
    """合成 4 列 filmstrip (横屏, 视觉对比友好)。"""
    from PIL import Image, ImageDraw, ImageFont

    n = len(frame_paths)
    rows = (n + cols - 1) // cols
    cell_h = int(cell_w * 9 / 16)  # 16:9
    pad = 8
    border = 4
    label_h = 22

    canvas_w = cols * cell_w + (cols + 1) * pad + 2 * border
    canvas_h = rows * (cell_h + label_h) + (rows + 1) * pad + 2 * border
    canvas = Image.new("RGB", (canvas_w, canvas_h), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    for idx, fp in enumerate(frame_paths):
        row, col = idx // cols, idx % cols
        ox = border + pad + col * (cell_w + pad)
        oy = border + pad + row * (cell_h + label_h + pad)
        try:
            img = Image.open(fp).convert("RGB")
        except Exception as e:  # noqa: BLE001
            print(f"WARN: failed to open {fp}: {e}", file=sys.stderr)
            continue
        img.thumbnail((cell_w, cell_h), Image.LANCZOS)
        cw, ch = img.size
        px = ox + (cell_w - cw) // 2
        py = oy + (cell_h - ch) // 2
        canvas.paste(img, (px, py))
        # 标签
        label = f"#{idx+1:02d} {fp.stem.split('_')[-1]}"
        draw.text((ox + 4, oy + cell_h + 2), label,
                  fill=(220, 220, 220), font=font)

    canvas.save(out_path, "JPEG", quality=88)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(REF_VIDEO))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--times", default=None,
                    help="逗号分隔时间点 (秒), 默认按任务书 §1")
    args = ap.parse_args(argv)

    video = Path(args.video)
    out_dir = Path(args.out_dir)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    times = (DEFAULT_TIMES
             if not args.times
             else [float(t) for t in args.times.split(",")])
    print(f"[analyze-v367] 参考视频: {video}")
    print(f"[analyze-v367] 抽帧时间点: {times}")
    print(f"[analyze-v367] 输出目录: {out_dir}")

    frame_paths = []
    for i, t in enumerate(times):
        fp = frames_dir / f"frame_{i+1:02d}_t{t:.2f}s.jpg"
        extract_frame(video, t, fp)
        print(f"  frame {i+1:02d} @ t={t:.2f}s -> {fp.name}")
        frame_paths.append(fp)

    # 4 列 filmstrip
    fs_path = out_dir / "filmstrip_4x3.jpg"
    build_filmstrip(frame_paths, fs_path, cols=4, cell_w=480)
    print(f"[analyze-v367] filmstrip -> {fs_path}")

    # 写 manifest
    manifest = {
        "ref_video": str(video),
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_frames": len(frame_paths),
        "times_sec": times,
        "frames": [
            {"idx": i + 1, "t_sec": t, "path": str(fp),
             "size_bytes": fp.stat().st_size}
            for i, (t, fp) in enumerate(zip(times, frame_paths))
        ],
        "filmstrip_path": str(fs_path),
        "task_book_section": "oc_task_v367.txt §2.1",
    }
    mp = out_dir / "manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    print(f"[analyze-v367] manifest -> {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
