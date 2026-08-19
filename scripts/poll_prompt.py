#!/usr/bin/env python3
"""轮询单个 prompt_id 直到完成，每 30s 报一次进度。

CLI:
  python poll_prompt.py <prompt_id> [--timeout 3600]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 15


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt_id")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--output", default=None, help="下载视频到本地路径（如果完成）")
    ap.add_argument("--out-dir", default=None, help="下载到该目录（默认用 comfy_filename）")
    args = ap.parse_args()

    pid = args.prompt_id
    deadline = time.time() + args.timeout
    last_log = 0
    start = time.time()

    while time.time() < deadline:
        try:
            r = requests.get(f"{COMFY}/history/{pid}", timeout=15)
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            print(f"[poll] history 失败: {e}", flush=True)
            time.sleep(POLL_INTERVAL)
            continue

        if pid in j:
            status = j[pid].get("status", {})
            if status.get("completed"):
                print(f"[poll] 完成! 耗时 {time.time()-start:.1f}s", flush=True)
                # 找视频
                for node_id, node in j[pid].get("outputs", {}).items():
                    for kind in ("gifs", "videos", "images"):
                        for item in node.get(kind, []):
                            fn = item.get("filename", "")
                            if fn.lower().endswith((".mp4", ".webm")):
                                print(f"[poll] 输出: {fn} sub={item.get('subfolder','')!r} type={item.get('type','')!r}", flush=True)
                                if args.output:
                                    out_path = Path(args.output)
                                elif args.out_dir:
                                    out_dir = Path(args.out_dir)
                                    out_dir.mkdir(parents=True, exist_ok=True)
                                    out_path = out_dir / fn
                                else:
                                    out_path = Path(fn)
                                params = {"filename": fn, "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")}
                                r2 = requests.get(f"{COMFY}/view", params=params, timeout=600)
                                r2.raise_for_status()
                                out_path.parent.mkdir(parents=True, exist_ok=True)
                                out_path.write_bytes(r2.content)
                                print(f"[poll] 已下载 -> {out_path} ({out_path.stat().st_size} bytes)", flush=True)
                # 打印完整 status
                print(f"[poll] status: {json.dumps(status, ensure_ascii=False)[:500]}", flush=True)
                return 0
            if status.get("status_str") == "error":
                print(f"[poll] 执行出错: {json.dumps(status, ensure_ascii=False)[:500]}", flush=True)
                return 1

        # 查队列位置
        try:
            qr = requests.get(f"{COMFY}/queue", timeout=10).json()
            pos = None
            for i, item in enumerate(qr.get("queue_pending", [])):
                if len(item) >= 2 and item[1] == pid:
                    pos = i + 1
                    break
            for i, item in enumerate(qr.get("queue_running", [])):
                if len(item) >= 2 and item[1] == pid:
                    pos = "执行中"
                    break
        except Exception:
            pos = "?"

        now = time.time()
        if now - last_log > 30:
            print(f"[poll] {time.time()-start:.0f}s 状态: {pos}", flush=True)
            last_log = now

        time.sleep(POLL_INTERVAL)

    print(f"[poll] 超时 {args.timeout}s", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
