#!/usr/bin/env python3
"""R2V 最小验证：MiniMaxH3ReferenceToVideo（ref2va）——参考图锁角色 + 参考视频锁动作。

CLI:
  python r2v_video_gen.py --char <character_ref.png> --motion <motion_template.mp4> \
      --prompt-file <r2v_prompt.txt> --out <r2v_result.mp4> [--seed N]

流程：
  1. ffmpeg 把 motion 视频拆成 24fps 帧序列 -> 帧文件夹
  2. POST /upload/image 上传角色参考图
  3. 构建 workflow（UNETLoader ref2va + MiniMaxH3ReferenceToVideo，ref_images/ref_videos 用
     扁平点号键 "ref_images.ref_image_0" / "ref_videos.ref_video_0" 传入 autogrow 输入）
  4. POST /prompt 排队 -> 轮询 /history -> 下载 .mp4

节点结构沿用 P5 已验证的 H3 采样链路（SageAttention patch + EasyCache + SamplerCustomAdvanced），
仅把 I2V 节点换成 R2V 节点并补 ref_images / ref_videos 输入。
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import requests

COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 10
POLL_TIMEOUT = 7200  # 2 小时（ref2va 首次加载权重 + 采样较慢）

UNET_NAME = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"


def extract_frames(video: Path, folder: Path) -> int:
    """把 motion 视频拆成 24fps PNG 帧序列，返回帧数。"""
    folder.mkdir(parents=True, exist_ok=True)
    for p in folder.glob("*.png"):
        p.unlink()
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vf", "fps=24", "-q:v", "1",
         str(folder / "frame_%04d.png")],
        check=True, capture_output=True,
    )
    n = len(list(folder.glob("*.png")))
    if n == 0:
        raise RuntimeError(f"拆帧失败：{video}")
    return n


def upload_image(path: Path) -> str:
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


def build_workflow(prompt, char_name, frames_folder, seed, prefix, steps=25, length=124,
                   width=1344, height=768, ref_image_size="match"):
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": char_name}},
        "2": {"class_type": "LoadImagesFromFolderKJ",
              "inputs": {"folder": str(frames_folder).replace("\\", "/"),
                         "width": -1, "height": -1, "keep_aspect_ratio": "stretch",
                         "image_load_cap": 0, "start_index": 0, "include_subfolders": False}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_NAME, "type": "minimax"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "7": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["3", 0]}},
        "8": {"class_type": "EasyCache", "inputs": {"model": ["7", 0], "reuse_threshold": 0.2,
                                                      "start_percent": 0.15, "end_percent": 0.95,
                                                      "verbose": False}},
        "9": {"class_type": "MiniMaxH3ReferenceToVideo",
              "inputs": {"clip": ["4", 0], "vae": ["5", 0], "audio_vae": ["6", 0],
                         "prompt": prompt, "width": width, "height": height, "length": length,
                         "ref_image_size": ref_image_size,
                         "ref_images.ref_image_0": ["1", 0],
                         "ref_videos.ref_video_0": ["2", 0]}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["8", 0], "conditioning": ["9", 0]}},
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "12": {"class_type": "BasicScheduler", "inputs": {"model": ["8", 0], "scheduler": "simple",
                                                            "steps": steps, "denoise": 1.0}},
        "13": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["10", 0],
                                                                  "sampler": ["13", 0], "sigmas": ["12", 0],
                                                                  "latent_image": ["9", 1]}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["5", 0]}},
        "16": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": ["6", 0]}},
        "17": {"class_type": "CreateVideo", "inputs": {"images": ["15", 0], "audio": ["16", 0], "fps": 24}},
        "18": {"class_type": "SaveVideo", "inputs": {"video": ["17", 0], "filename_prefix": prefix,
                                                       "format": "auto", "codec": "auto"}},
    }
    return wf


def queue_workflow(wf: dict) -> str:
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"prompt 排队失败 {r.status_code}: {r.text[:800]}")
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
                raise RuntimeError(f"ComfyUI 执行出错: {json.dumps(status, ensure_ascii=False)[:1500]}")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", required=True)
    ap.add_argument("--motion", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--length", type=int, default=124)
    ap.add_argument("--width", type=int, default=1344)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--prefix", default="r2v_test")
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randint(0, int(1e9))
    char = Path(args.char)
    motion = Path(args.motion)
    out = Path(args.out)
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()

    t0 = time.time()
    print(f"[r2v] seed={seed} steps={args.steps} length={args.length} size={args.width}x{args.height}", flush=True)

    frames_folder = out.parent / "motion_frames"
    nframes = extract_frames(motion, frames_folder)
    print(f"[r2v] 拆帧 {nframes} 张 -> {frames_folder}", flush=True)

    char_name = upload_image(char)
    print(f"[r2v] 角色参考图已上传: {char_name}", flush=True)

    wf = build_workflow(prompt, char_name, frames_folder, seed, args.prefix,
                        steps=args.steps, length=args.length, width=args.width, height=args.height)
    pid = queue_workflow(wf)
    print(f"[r2v] 已排队 prompt_id={pid}", flush=True)

    entry = poll_history(pid)
    fn, sub, vtype = find_video_output(entry)
    print(f"[r2v] 输出文件: {fn} (subfolder={sub!r})", flush=True)

    download_video(fn, sub, vtype, out)
    dt = time.time() - t0
    print(f"[r2v] 完成 -> {out} 耗时 {dt:.1f}s seed={seed}", flush=True)

    meta = {"seed": seed, "steps": args.steps, "length": args.length,
            "width": args.width, "height": args.height, "ref_image_size": "match",
            "prompt_id": pid, "comfy_filename": fn, "subfolder": sub, "type": vtype,
            "elapsed_sec": round(dt, 1), "char": char.name, "motion": motion.name,
            "motion_frames": nframes, "prompt": prompt}
    (out.with_suffix(".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
