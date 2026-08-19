#!/usr/bin/env python3
"""Z-Image（Alibaba Tongyi）文本到角色定妆图生成器。

ComfyUI 工作流（用 Z-Image 专用 text encoder + 标准 KSampler）：
  UNETLoader(z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors, fp8_e4m3fn_fast)
  CLIPLoader(qwen_2.5_vl_7b_fp8_scaled, type=qwen_image)
  TextEncodeZImageOmni(clip, prompt)  → CONDITIONING  # 关键：用 Z-Image 专用 encoder 避免 shape 不匹配
  EmptyLatentImage(width, height)
  KSampler(model, seed, steps, cfg, sampler=euler, scheduler=simple,
           positive, negative, latent, denoise=1.0)
  VAELoader(ae.safetensors)
  VAEDecode
  SaveImage

注意：
- Z-Image 是 distilled turbo 模型，steps 8-9 推荐（max 9 是 ZSamplerTurbo 硬上限；KSampler 没限制）
- TextEncodeZImageOmni 输出的 CONDITIONING 维度正确（2560 隐藏），KSampler 可用
- 不用 ZSamplerTurbo 是因为它要求 ZIPN_DIVIDER 输入，而 ComfyUI 没有该类型节点

CLI:
    python scripts/z_image_gen.py --prompt "..." --out path.png [--seed N] [--steps 9]
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

UNET_NAME = "z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors"
UNET_WEIGHT_DTYPE = "default"
CLIP_NAME = "qwen3_4b_fp8_scaled.safetensors"
CLIP_TYPE = "lumina2"
CLIP_DTYPE = "default"
VAE_NAME = "ae.safetensors"


def build_workflow(prompt: str, seed: int, steps: int, width: int, height: int,
                   prefix: str, style: str = "none", intensity: float = 0.0) -> dict:
    """构造 Z-Image 文本到图像 workflow JSON。

    使用官方 Z-Image Power Nodes 链路：
      - CLIPLoader(qwen3_4b_fp8_scaled, type=lumina2)
      - UNETLoader(z-image-turbo_fp8_scaled_e4m3fn_KJ)
      - VAELoader(ae.safetensors)
      - StylePromptEncoder2(style, text) → CONDITIONING
      - EmptyLatentImage(width, height) → LATENT
      - ZSamplerTurbo2(model, positive, latent, seed, steps=8, ...)
      - VAEDecode
      - SaveImage
    """
    # EmptyZImageLatentImage 用 ratio/landscape/size 推导尺寸，不接受 width/height
    # 9:16 → ratio="16:9  (widescreen)" + landscape=False + size="small" → 768x1365
    # 与 720x1280 接近，足够参考图精度
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET_NAME, "weight_dtype": UNET_WEIGHT_DTYPE}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP_NAME, "type": CLIP_TYPE, "weight_dtype": CLIP_DTYPE}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": VAE_NAME}},
        "4": {"class_type": "StylePromptEncoder2 //ZImagePowerNodes",
              "inputs": {"clip": ["2", 0], "style": style,
                         "gallery": "", "spacer": "", "text": prompt}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "ZSamplerTurbo2 //ZImagePowerNodes",
              "inputs": {
                  "model": ["1", 0],
                  "positive": ["4", 0],
                  "latent_input": ["5", 0],
                  "seed": seed,
                  "divider": "",
                  "steps": steps,
                  "denoise": 1.0,
                  "divider2": "",
                  "initial_sample_size": "full_size",
                  "divider3": "",
                  "intensity": intensity,
                  "intensity_bias": 0.0,
                  "turbo_creativity": "off",
              }},
        "7": {"class_type": "VAEDecode",
              "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage",
              "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }


def queue_prompt(wf: dict) -> str:
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"prompt 排队失败 {r.status_code}: {r.text[:800]}")
    j = r.json()
    pid = j.get("prompt_id")
    if not pid:
        raise RuntimeError(f"未返回 prompt_id: {j}")
    return pid


def poll_history(pid: str, timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{COMFY}/history/{pid}", timeout=30)
        r.raise_for_status()
        j = r.json()
        if pid in j:
            status = j[pid].get("status", {})
            if status.get("completed"):
                return j[pid]
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI 执行出错: {json.dumps(status, ensure_ascii=False)[:1500]}")
        time.sleep(3)
    raise TimeoutError(f"轮询超时 {timeout}s (prompt_id={pid})")


def find_image_output(entry: dict) -> tuple[str, str, str]:
    for node_id, node in entry.get("outputs", {}).items():
        for item in node.get("images", []):
            fn = item.get("filename", "")
            if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                return fn, item.get("subfolder", ""), item.get("type", "output")
    raise RuntimeError(f"history 未找到图像输出: {json.dumps(entry, ensure_ascii=False)[:800]}")


def download_image(filename: str, subfolder: str, itype: str, out: Path) -> None:
    params = {"filename": filename, "subfolder": subfolder, "type": itype}
    r = requests.get(f"{COMFY}/view", params=params, timeout=300)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Z-Image 文本到定妆图生成")
    ap.add_argument("--prompt", required=True, help="主提示词")
    ap.add_argument("--out", required=True, help="输出 PNG 路径")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（不指定则随机）")
    ap.add_argument("--steps", type=int, default=8, help="采样步数（Z-Image 推荐 8）")
    ap.add_argument("--width", type=int, default=720, help="图像宽度")
    ap.add_argument("--height", type=int, default=1280, help="图像高度")
    ap.add_argument("--prefix", default="zimg", help="ComfyUI 文件名前缀")
    ap.add_argument("--style", default="none", help="Style preset (none / anime / etc.)")
    ap.add_argument("--intensity", type=float, default=0.0, help="intensity (饱和度/对比度)")
    ap.add_argument("--timeout", type=int, default=600, help="轮询超时秒")
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randint(0, int(1e9))
    out = Path(args.out)

    print(f"[zimg] seed={seed} steps={args.steps} {args.width}x{args.height} "
          f"style={args.style} -> {out}", flush=True)
    print(f"[zimg] prompt: {args.prompt[:120]}...", flush=True)

    wf = build_workflow(args.prompt, seed, args.steps, args.width, args.height,
                        args.prefix, args.style, args.intensity)
    pid = queue_prompt(wf)
    print(f"[zimg] queued prompt_id={pid}", flush=True)

    entry = poll_history(pid, timeout=args.timeout)
    fn, sub, vtype = find_image_output(entry)
    print(f"[zimg] output file: {fn} (subfolder={sub!r})", flush=True)

    download_image(fn, sub, vtype, out)

    meta = {
        "seed": seed, "steps": args.steps,
        "width": args.width, "height": args.height,
        "style": args.style, "intensity": args.intensity,
        "prompt": args.prompt,
        "prompt_id": pid, "comfy_filename": fn,
        "subfolder": sub, "type": vtype, "output": str(out),
    }
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[zimg] done -> {out} ({out.stat().st_size} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
