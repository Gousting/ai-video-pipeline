#!/usr/bin/env python3
"""P5-v2 关键帧重生成：Z-Image Turbo（Qwen-Image + Lightning 8step LoRA）文本生图，1344x768 16:9。

CLI:
  python p5_v2_frames.py --prompt-file <txt> --out <png> [--seed N] [--width 1344] [--height 768] [--prefix p5v2]

流程：POST /prompt -> 轮询 /history -> 从 outputs images 取 .png -> /view 下载。
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
POLL_TIMEOUT = 600         # 秒（Z-Image Turbo 8 步很快）

# Z-Image Turbo 权重（z-image-power-nodes 官方工作流，Kijai fp8_scaled）
UNET_NAME = "z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors"
CLIP_NAME = "qwen3_4b_fp8_scaled.safetensors"
VAE_NAME = "ae.safetensors"
CLIP_TYPE = "lumina2"

WIDTH = 1344
HEIGHT = 768


def build_zimage_workflow(prompt: str, width: int, height: int, seed: int, prefix: str, steps: int = 8) -> dict:
    """Z-Image Turbo 文本生图 workflow（z-image-power-nodes 官方节点，16:9 无负面依赖）。

    EmptyZImageLatentImage(landscape=True, ratio='16:9  (widescreen)', size='small')
    -> 精确 1344x768（32 倍数）。StylePromptEncoder2(style='none') 不做风格模板改写。
    """
    return {
        "60": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_NAME, "type": CLIP_TYPE}},
        "59": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"}},
        "61": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_NAME}},
        "32": {"class_type": "EmptyZImageLatentImage //ZImagePowerNodes",
               "inputs": {"landscape": True, "ratio": "16:9  (widescreen)", "size": "small", "batch_size": 1}},
        "43": {"class_type": "StylePromptEncoder2 //ZImagePowerNodes",
               "inputs": {"clip": ["60", 0], "style": "none", "text": prompt,
                          "gallery": None, "spacer": None}},
        "68": {"class_type": "ZSamplerTurbo2 //ZImagePowerNodes",
               "inputs": {"latent_input": ["32", 0], "model": ["59", 0], "positive": ["43", 0],
                          "seed": seed, "steps": steps, "denoise": 1.0,
                          "divider": None, "initial_sample_size": "full_size", "divider2": None,
                          "intensity": 0.0, "intensity_bias": 0.0, "turbo_creativity": "off"}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["68", 0], "vae": ["61", 0]}},
        "31": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
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


def find_image_output(entry: dict) -> tuple[str, str, str]:
    """从 history 条目里找 .png，返回 (filename, subfolder, type)。"""
    for node_id, node in entry.get("outputs", {}).items():
        for item in node.get("images", []):
            fn = item.get("filename", "")
            if fn.lower().endswith(".png"):
                return fn, item.get("subfolder", ""), item.get("type", "output")
    raise RuntimeError(f"history 未找到图片输出: {json.dumps(entry, ensure_ascii=False)[:800]}")


def download_image(filename: str, subfolder: str, vtype: str, out: Path) -> None:
    params = {"filename": filename, "subfolder": subfolder, "type": vtype}
    r = requests.get(f"{COMFY}/view", params=params, timeout=300)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", required=True, help="UTF-8 正向 prompt 文本")
    ap.add_argument("--out", required=True, help="输出 PNG 路径")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--height", type=int, default=HEIGHT)
    ap.add_argument("--prefix", default="p5v2_frame")
    ap.add_argument("--steps", type=int, default=8)
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randint(0, int(1e9))
    out = Path(args.out)
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()

    t0 = time.time()
    print(f"[frame] 开始 seed={seed} size={args.width}x{args.height} steps={args.steps}", flush=True)

    wf = build_zimage_workflow(prompt, args.width, args.height, seed, args.prefix, args.steps)
    pid = queue_workflow(wf)
    print(f"[frame] 已排队 prompt_id={pid}", flush=True)

    entry = poll_history(pid)
    fn, sub, vtype = find_image_output(entry)
    print(f"[frame] 输出文件: {fn} (subfolder={sub!r}, type={vtype!r})", flush=True)

    download_image(fn, sub, vtype, out)
    dt = time.time() - t0
    print(f"[frame] 完成 -> {out} 耗时 {dt:.1f}s seed={seed}", flush=True)

    meta = {"seed": seed, "width": args.width, "height": args.height, "steps": args.steps,
            "prompt_id": pid, "comfy_filename": fn, "elapsed_sec": round(dt, 1), "prompt": prompt}
    meta_path = out.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
