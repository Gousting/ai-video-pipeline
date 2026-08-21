#!/usr/bin/env python3
"""v3.6.7 连通性测试: 上传 1 张 ref_image + 提交最小 R2V workflow (length=17 帧).

任务书 §1: MiniMaxH3ReferenceToVideo 节点确认存在 + 工作流可执行.

CLI: python smoke_test_r2v_v367.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

ROOT = Path(r"D:\ai-video-pipeline")
sys.path.insert(0, str(ROOT / "scripts"))

import requests

from t2v_seq_v367 import (
    upload_image, build_h3_r2v_workflow, queue_workflow, poll_history,
    find_video_output, download_video, COMFY,
    UNET_NAME, CLIP_NAME, VIDEO_VAE, AUDIO_VAE, FPS,
)

REF_IMAGES_DIR = ROOT / "output" / "pipeline_v36" / "ref_images_v367"
OUT_TEST = ROOT / "output" / "pipeline_v36" / "smoke_test_r2v_v367.mp4"


def main() -> int:
    refs = sorted(REF_IMAGES_DIR.glob("ref_*.jpg"))
    if not refs:
        print(f"ERROR: no refs in {REF_IMAGES_DIR}", file=sys.stderr)
        return 2
    print(f"[smoke-v367] 上传 ref {refs[0].name}")
    uploaded = upload_image(refs[0])

    # 最小 prompt (只是确认节点能跑通, 不追求生成质量)
    prompt = (
        "integrated_multimodal_description:\n"
        "Anime cel-shading pop-art still life with vivid CMYK colors "
        "and a single character at center frame. Stylized artificial "
        "studio lighting. No voices, no music.\n\n"
        "overall_soundscape:\nsilent.\n\n"
        "non_diegetic_music:\nnone."
    )

    wf = build_h3_r2v_workflow(
        prompt=prompt, ref_image_names=[uploaded],
        width=512, height=288, length=17,
        seed=336700, prefix="smoke_v367_r2v",
        steps=8,
    )
    print(f"[smoke-v367] 提交 workflow ({len(wf)} 个节点)")
    pid = queue_workflow(wf)
    print(f"[smoke-v367] queued pid={pid}")
    entry = poll_history(pid, log_prefix="smoke ")
    fn, sub, vtype = find_video_output(entry)
    print(f"[smoke-v367] output: {fn}")
    download_video(fn, sub, vtype, OUT_TEST)
    size_kb = OUT_TEST.stat().st_size // 1024
    print(f"[smoke-v367] saved -> {OUT_TEST} ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
