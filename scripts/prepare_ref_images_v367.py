#!/usr/bin/env python3
"""v3.6.7 Step 1: 从参考视频选定 4 个代表性帧, 准备 ref_images 供 R2V 用。

任务书 oc_task_v367.txt §3.1:
- 从参考视频抽 2-4 个清晰帧作为 ref_images
- 选人物清晰/背景明确/色彩有代表性的帧
- 输出 ref_images/ 目录, 每张图一份 jpg

VLM 识别 (v367_style_profile.md):
- frame_01 (t=0.20s) "COLOR RIOT" 标题 + 角色正脸 + 彩虹泪滴
- frame_04 (t=8.00s) 分屏角色脸 + 多耳钉 + 三角几何
- frame_07 (t=14.03s) 全身 + 绿衣 + 抽象色带
- frame_11 (t=27.27s) 角色特写 + 黑发彩虹 + 戳手指姿势

CLI:
    python prepare_ref_images_v367.py
    python prepare_ref_images_v367.py --indices 1,4,7,11
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

ROOT = Path(r"D:\ai-video-pipeline")
REF_VIDEO = ROOT / "input_h3_pv_ref.mp4"
FRAMES_DIR = ROOT / "ref_analysis_v367" / "frames"
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v36" / "ref_images_v367"

# 任务书 §3.1 选 4 张代表性帧 (基于 VLM 视觉档案 §1 的 stable_frames + 角色清晰度)
DEFAULT_INDICES = [1, 4, 7, 11]

# 对应 frame_XX_tYY.YYs.jpg 文件名
INDEX_TO_TIME = {
    1: 0.20, 4: 8.00, 7: 14.03, 11: 27.27,
}


def extract_raw_frame(video: Path, t_sec: float, out_path: Path) -> Path:
    """原分辨率抽帧 (不缩, 让 ref_image_size=match 自动处理)。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(
            f"抽帧失败 t={t_sec}s: {r.stderr[-300:] or r.stdout[-300:]}")
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(REF_VIDEO))
    ap.add_argument("--frames-dir", default=str(FRAMES_DIR))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--indices", default=None,
                    help="逗号分隔 frame 序号 (默认 1,4,7,11)")
    args = ap.parse_args(argv)

    video = Path(args.video)
    frames_dir = Path(args.frames_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    indices = (DEFAULT_INDICES
               if not args.indices
               else [int(i) for i in args.indices.split(",")])

    print(f"[ref-v367] ref video    = {video}")
    print(f"[ref-v367] frames dir   = {frames_dir}")
    print(f"[ref-v367] out dir      = {out_dir}")
    print(f"[ref-v367] selected idx = {indices}")

    selected = []
    for idx in indices:
        t = INDEX_TO_TIME.get(idx)
        if t is None:
            print(f"WARN: 帧 #{idx} 不在 INDEX_TO_TIME, 跳过", file=sys.stderr)
            continue
        # 优先用 VLM 分析时抽的同帧 (避免重复抽帧质量差异)
        src = frames_dir / f"frame_{idx:02d}_t{t:.2f}s.jpg"
        if not src.exists():
            print(f"WARN: {src} 不存在, 重新抽", file=sys.stderr)
            src = out_dir / f"ref_{idx:02d}_t{t:.2f}s_raw.jpg"
            extract_raw_frame(video, t, src)
        # 拷贝为标准命名
        dst = out_dir / f"ref_{idx:02d}_t{t:.2f}s.jpg"
        dst.write_bytes(src.read_bytes())
        size_kb = dst.stat().st_size // 1024
        print(f"  ref #{idx} t={t:.2f}s -> {dst.name} ({size_kb} KB)")
        selected.append({
            "idx": idx, "t_sec": t,
            "source_path": str(src), "ref_path": str(dst),
            "size_bytes": dst.stat().st_size,
        })

    # 写 manifest
    manifest = {
        "ref_video": str(video),
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_ref_images": len(selected),
        "selection_rationale": (
            "Per VLM visual profile (v367_style_profile.md §1): "
            "frame_01 full face + rainbow tear-drip; "
            "frame_04 split-panel face with multi-piercings; "
            "frame_07 full figure with acid-lime green outfit; "
            "frame_11 close-up face with rainbow streaks + pointing finger"
        ),
        "ref_images": selected,
        "vlm_profile_ref": "ref_analysis_v367/v367_style_profile.md",
        "task_book_section": "oc_task_v367.txt §3.1",
    }
    mp = out_dir / "manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    print(f"[ref-v367] manifest -> {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
