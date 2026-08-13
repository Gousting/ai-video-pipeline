#!/usr/bin/env python3
"""P5-v5 后处理：
  1. RIFE 补帧 24->48fps：ComfyUI RIFE VFI（rife49.pth, multiplier=2, fast_mode=True）
  2. 放大 1344x768 -> 1920x1080：ffmpeg lanczos（B 站横屏规格）
  3. 音频回混：final_v5.mp4 音轨 copy 回 1080p 成片 -> output/out/final_v5_1080p.mp4

CLI:
  python p5_v5_post.py rife --video <mp4> --out <mp4>
  python p5_v5_post.py finalize --rife <mp4> --audio <mp4> --out <mp4>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 5
POLL_TIMEOUT = 1800        # RIFE 全程可能较久

RIFE_CKPT = "rife49.pth"


def upload_video(path: Path) -> str:
    with path.open("rb") as f:
        r = requests.post(f"{COMFY}/upload/image",
                          files={"image": (path.name, f, "video/mp4")},
                          data={"overwrite": "true"}, timeout=120)
    r.raise_for_status()
    j = r.json()
    name = j.get("name")
    if not name:
        raise RuntimeError(f"upload 未返回 name: {j}")
    return name


def build_rife_workflow(video_name: str, prefix: str, multiplier: int = 2) -> dict:
    return {
        "1": {"class_type": "LoadVideo", "inputs": {"file": video_name}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "RIFE VFI", "inputs": {
            "ckpt_name": RIFE_CKPT, "frames": ["2", 0],
            "clear_cache_after_n_frames": 10, "multiplier": multiplier,
            "fast_mode": True, "ensemble": False, "scale_factor": 1.0,
            "dtype": "float16", "torch_compile": False, "batch_size": 1}},
        "4": {"class_type": "CreateVideo", "inputs": {"images": ["3", 0], "fps": 48.0}},
        "5": {"class_type": "SaveVideo", "inputs": {"video": ["4", 0], "filename_prefix": prefix,
                                                    "format": "auto", "codec": "auto"}},
    }


def queue_workflow(wf: dict) -> str:
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"prompt 排队失败 {r.status_code}: {r.text[:500]}")
    j = r.json()
    pid = j.get("prompt_id")
    if not pid:
        raise RuntimeError(f"未返回 prompt_id: {j}")
    return pid


def poll_history(pid: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = requests.get(f"{COMFY}/history/{pid}", timeout=30)
        r.raise_for_status()
        j = r.json()
        if pid in j:
            status = j[pid].get("status", {})
            if status.get("completed"):
                return j[pid]
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI 执行出错: {json.dumps(status, ensure_ascii=False)[:800]}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"轮询超时 {POLL_TIMEOUT}s (prompt_id={pid})")


def find_video_output(entry: dict) -> tuple[str, str, str]:
    for node_id, node in entry.get("outputs", {}).items():
        for kind in ("gifs", "videos", "images"):
            for item in node.get(kind, []):
                fn = item.get("filename", "")
                if fn.lower().endswith((".mp4", ".webm")):
                    return fn, item.get("subfolder", ""), item.get("type", "output")
    raise RuntimeError(f"history 未找到视频输出: {json.dumps(entry, ensure_ascii=False)[:800]}")


def download_video(filename: str, subfolder: str, vtype: str, out: Path) -> None:
    params = {"filename": filename, "subfolder": subfolder, "type": vtype}
    r = requests.get(f"{COMFY}/view", params=params, timeout=600)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise RuntimeError(f"命令失败 rc={r.returncode}")


def cmd_rife(video: Path, out: Path, prefix: str, multiplier: int) -> int:
    t0 = time.time()
    print(f"[rife] 上传 {video.name}", flush=True)
    name = upload_video(video)
    wf = build_rife_workflow(name, prefix, multiplier)
    pid = queue_workflow(wf)
    print(f"[rife] 已排队 prompt_id={pid}", flush=True)
    entry = poll_history(pid)
    fn, sub, vtype = find_video_output(entry)
    print(f"[rife] 输出 {fn}", flush=True)
    download_video(fn, sub, vtype, out)
    print(f"[rife] 完成 -> {out} 耗时 {time.time()-t0:.1f}s", flush=True)
    meta = {"input": str(video), "output": str(out), "ckpt": RIFE_CKPT,
            "multiplier": multiplier, "fps_out": 48.0, "prompt_id": pid,
            "elapsed_sec": round(time.time() - t0, 1)}
    (out.with_suffix(".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_finalize(rife: Path, audio_src: Path, out: Path) -> int:
    """lanczos 放大到 1920x1080 + 从 audio_src 回混音轨。"""
    run([
        "ffmpeg", "-y",
        "-i", str(rife),
        "-i", str(audio_src),
        "-filter_complex", "[0:v]scale=1920:1080:flags=lanczos,format=yuv420p[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ])
    print(f"[finalize] 完成 -> {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("rife")
    p1.add_argument("--video", required=True)
    p1.add_argument("--out", required=True)
    p1.add_argument("--prefix", default="p5v5_rife")
    p1.add_argument("--multiplier", type=int, default=2)

    p2 = sub.add_parser("finalize")
    p2.add_argument("--rife", required=True)
    p2.add_argument("--audio", required=True)
    p2.add_argument("--out", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "rife":
        return cmd_rife(Path(args.video), Path(args.out), args.prefix, args.multiplier)
    if args.cmd == "finalize":
        return cmd_finalize(Path(args.rife), Path(args.audio), Path(args.out))
    return 2


if __name__ == "__main__":
    sys.exit(main())
