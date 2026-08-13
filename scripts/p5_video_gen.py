#!/usr/bin/env python3
"""P5 视频生成脚本：调宿主机 ComfyUI(127.0.0.1:8188) MiniMax H3 fl2va 首帧驱动，生成单镜头视频段。

CLI:
  python p5_video_gen.py --shot 1 --first D:\\ai-video-pipeline\\output\\frames\\shot1.png \
      --prompt "..." --length 120 --out D:\\ai-video-pipeline\\output\\clips\\shot1.mp4 \
      [--seed 12345] [--width 640] [--height 480]

流程（与任务书 H3 调用参考一致，勿改节点结构）：
  1. POST /upload/image  上传首帧 -> 取返回 name
  2. POST /prompt        排队 workflow -> 取 prompt_id
  3. GET  /history/{id}  轮询直到 completed / error
  4. GET  /view          下载 .mp4/.webm -> 落盘
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 5          # 秒
POLL_TIMEOUT = 3600        # 秒（60 分钟，多镜头排队时单镜头可能排到 20 分钟后）

# H3 权重名（ComfyUI 已加载，原样复用）
UNET_NAME = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"


def build_h3_workflow(prompt, first_frame_name, width, height, length, seed, prefix, last_frame_name=None, steps=20):
    """H3 双帧锚定 workflow（节点序号/连接关系与任务书原样，节点8 补 last_frame 输入）。"""
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": first_frame_name}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_NAME, "type": "minimax"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "6": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["2", 0]}},
        "7": {"class_type": "EasyCache", "inputs": {"model": ["6", 0], "reuse_threshold": 0.2, "start_percent": 0.15, "end_percent": 0.95, "verbose": False}},
        "8": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": prompt, "width": width, "height": height, "length": length, "first_frame": ["1", 0], **({"last_frame": ["18", 0]} if last_frame_name else {})}},
        "9": {"class_type": "BasicGuider", "inputs": {"model": ["7", 0], "conditioning": ["8", 0]}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {"class_type": "BasicScheduler", "inputs": {"model": ["7", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["10", 0], "guider": ["9", 0], "sampler": ["12", 0], "sigmas": ["11", 0], "latent_image": ["8", 1]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["4", 0]}},
        "15": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["13", 0], "vae": ["5", 0]}},
        "16": {"class_type": "CreateVideo", "inputs": {"images": ["14", 0], "audio": ["15", 0], "fps": 24}},
        "17": {"class_type": "SaveVideo", "inputs": {"video": ["16", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto"}},
    }
    if last_frame_name:
        wf["18"] = {"class_type": "LoadImage", "inputs": {"image": last_frame_name}}
    return wf


def upload_image(path: Path) -> str:
    """上传首帧，返回 ComfyUI input 目录下的文件名。"""
    with path.open("rb") as f:
        files = {"image": (path.name, f, "image/png")}
        data = {"overwrite": "true"}
        r = requests.post(f"{COMFY}/upload/image", files=files, data=data, timeout=60)
    r.raise_for_status()
    j = r.json()
    name = j.get("name")
    if not name:
        raise RuntimeError(f"upload 未返回 name: {j}")
    return name


def queue_workflow(wf: dict) -> str:
    """排队 workflow，返回 prompt_id。"""
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"prompt 排队失败 {r.status_code}: {r.text[:500]}")
    j = r.json()
    pid = j.get("prompt_id")
    if not pid:
        raise RuntimeError(f"未返回 prompt_id: {j}")
    return pid


def poll_history(pid: str) -> dict:
    """轮询 history 直到 completed/error，返回该 prompt 的 history 条目。"""
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
    """从 history 条目里找 .mp4/.webm，返回 (filename, subfolder, type)。"""
    for node_id, node in entry.get("outputs", {}).items():
        for kind in ("gifs", "videos", "images"):
            for item in node.get(kind, []):
                fn = item.get("filename", "")
                if fn.lower().endswith((".mp4", ".webm")):
                    return fn, item.get("subfolder", ""), item.get("type", "output")
    raise RuntimeError(f"history 未找到视频输出: {json.dumps(entry, ensure_ascii=False)[:800]}")


def download_video(filename: str, subfolder: str, vtype: str, out: Path) -> None:
    """下载视频文件到本地。"""
    params = {"filename": filename, "subfolder": subfolder, "type": vtype}
    r = requests.get(f"{COMFY}/view", params=params, timeout=600)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=int, required=True)
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", default=None, help="尾帧（双帧锚定）；不传则退化为首帧驱动")
    ap.add_argument("--prompt", default=None, help="prompt 文本（与 --prompt-file 二选一）")
    ap.add_argument("--prompt-file", default=None, help="从 UTF-8 文件读取 prompt（避免命令行编码问题）")
    ap.add_argument("--length", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--prefix", default=None, help="SaveVideo filename_prefix；默认 p5_shot{shot}")
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randint(0, int(1e9))
    first = Path(args.first)
    out = Path(args.out)
    prefix = args.prefix or f"p5_shot{args.shot}"

    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        print("错误：必须提供 --prompt 或 --prompt-file", file=sys.stderr)
        return 2

    t0 = time.time()
    print(f"[gen] shot{args.shot} 开始 seed={seed} length={args.length} size={args.width}x{args.height} steps={args.steps}", flush=True)

    name = upload_image(first)
    print(f"[gen] 首帧已上传: {name}", flush=True)

    last_name = None
    if args.last:
        last_name = upload_image(Path(args.last))
        print(f"[gen] 尾帧已上传: {last_name}", flush=True)

    wf = build_h3_workflow(prompt, name, args.width, args.height, args.length, seed, prefix,
                           last_frame_name=last_name, steps=args.steps)
    pid = queue_workflow(wf)
    print(f"[gen] 已排队 prompt_id={pid}", flush=True)

    entry = poll_history(pid)
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen] 输出文件: {fn} (subfolder={sub!r}, type={vtype!r})", flush=True)

    download_video(fn, sub, vtype, out)
    dt = time.time() - t0
    print(f"[gen] 完成 shot{args.shot} -> {out} 耗时 {dt:.1f}s seed={seed}", flush=True)

    # 落盘元信息供报告追溯
    meta = {"shot": args.shot, "seed": seed, "length": args.length,
            "width": args.width, "height": args.height, "steps": args.steps,
            "prompt_id": pid, "comfy_filename": fn, "subfolder": sub, "type": vtype,
            "elapsed_sec": round(dt, 1), "prompt": prompt,
            "dual_frame": bool(args.last), "first_frame": str(Path(args.first).name),
            "last_frame": str(Path(args.last).name) if args.last else None}
    meta_path = out.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
