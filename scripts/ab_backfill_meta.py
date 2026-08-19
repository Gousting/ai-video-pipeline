#!/usr/bin/env python3
"""为 shot01 和 shot06 补 metadata JSON（这两个分别早于 batch 和后于 batch 生成，metadata 缺失）。

原日志可证独立生成：
- shot01: opencode_abtest.log 中的 gen_one 调用 + 实际生成耗时 565.4s（poll_shot06 之前那次）
- shot06: poll_shot06.log 完整记录 1294.5s 完成，prompt_id 7935b1f6-6d97-4203-b125-f1694647573f
"""
import json
import time
from pathlib import Path

OUT_DIR = Path(r"D:\ai-video-pipeline\output\abtest")

# shot01 实际是 2026-08-18 上午通过 gen_one 第一次跑的，时间戳用 batch_run.log 推断
# shot06 是 2026-08-18 中午通过 poll_prompt.py 跑完的
for shot, meta in [
    (1, {
        "shot": 1, "seed": 10001, "length": 192,
        "width": 768, "height": 1344, "steps": 20,
        "prompt_id": "76d1f073-dbb2-4ac5-84b0-a46847bc210c",
        "comfy_filename": "ab_t2v_shot01_00001_.mp4",
        "subfolder": "", "type": "output",
        "elapsed_sec": 565.4,
        "prompt_file": str(OUT_DIR / "a_shot01.txt"),
        "prompt_chars": 2548,
        "method": "pure_t2v_blank_first_frame",
        "first_frame_seed": str(OUT_DIR / "_seed_blank_768x1344.png"),
        "generated_at": "2026-08-18T09:55:00",
        "note": "shot01 首次生成走单点 gen_one（早于 batch），完整 9.4 分钟日志见 opencode_abtest.log",
    }),
    (6, {
        "shot": 6, "seed": 10006, "length": 192,
        "width": 768, "height": 1344, "steps": 20,
        "prompt_id": "7935b1f6-6d97-4203-b125-f1694647573f",
        "comfy_filename": "ab_t2v_shot06_00001_.mp4",
        "subfolder": "", "type": "output",
        "elapsed_sec": 1294.5,
        "prompt_file": str(OUT_DIR / "a_shot06.txt"),
        "prompt_chars": 1740,
        "method": "pure_t2v_blank_first_frame",
        "first_frame_seed": str(OUT_DIR / "_seed_blank_768x1344.png"),
        "generated_at": "2026-08-18T11:43:00",
        "note": "shot06 走 poll_prompt.py pollling 跨会话完成，含 13 分钟等其他会话任务排队时间",
    }),
]:
    path = OUT_DIR / f"a_shot{shot:02d}.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"补 metadata: {path}")
