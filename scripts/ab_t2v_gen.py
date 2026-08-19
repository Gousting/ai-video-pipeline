#!/usr/bin/env python3
"""A 组（纯提示词直出）T2V 生成脚本。

策略：
- H3 MiniMaxH3ImageToVideo 节点强制 first_frame 输入 → 用一张纯白 768x1344 占位图
  作为 first_frame（模型实际上由 prompt 驱动，符合 T2V 语义）。
- 每段独立 seed（10001-10008），独立 prompt，无参考图无 R2V。
- 复用 p5_video_gen.py 的 workflow 构造，但 T2V 路径不传 last_frame，省掉末尾帧。

CLI:
  python ab_t2v_gen.py --shot 1 --prompt-file <txt> --out <mp4> --seed 10001
  python ab_t2v_gen.py --batch           # 批量生成 8 段，按 prompts_a.md 元数据自动填 seed
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
POLL_INTERVAL = 5
POLL_TIMEOUT = 1800  # 30 分钟；8s 单镜头可能 5-8 分钟

# H3 权重（与 p5_video_gen.py 一致）
UNET_NAME = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

ROOT = Path(r"D:\ai-video-pipeline")
OUT_DIR = ROOT / "output" / "abtest"
SEED_FRAME = OUT_DIR / "_seed_blank_768x1344.png"

# 段元数据（与 prompts_a.md §9 一致）
SEGMENT_META = {
    1: {"seed": 10001, "camera": "push in (small, slow)"},
    2: {"seed": 10002, "camera": "pull back (small, slow)"},
    3: {"seed": 10003, "camera": "pan right (small, slow)"},
    4: {"seed": 10004, "camera": "static medium"},
    5: {"seed": 10005, "camera": "tilt up (small, slow)"},
    6: {"seed": 10006, "camera": "push in (small, slow)"},
    7: {"seed": 10007, "camera": "push in (small, slow)"},
    8: {"seed": 10008, "camera": "pull back (medium, slow)"},
}


def make_seed_frame(width: int = 768, height: int = 1344) -> Path:
    """生成纯白 first_frame 占位图（H3 fl2va T2V 模式）。"""
    from PIL import Image
    SEED_FRAME.parent.mkdir(parents=True, exist_ok=True)
    if not SEED_FRAME.exists():
        img = Image.new("RGB", (width, height), (255, 255, 255))
        img.save(SEED_FRAME, "PNG")
    return SEED_FRAME


def build_h3_workflow(prompt: str, first_frame_name: str, width: int, height: int,
                      length: int, seed: int, prefix: str, steps: int = 20) -> dict:
    """H3 T2V workflow（与 p5_video_gen.py 一致，不传 last_frame）。"""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": first_frame_name}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_NAME, "type": "minimax"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "6": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["2", 0]}},
        "7": {"class_type": "EasyCache", "inputs": {
            "model": ["6", 0], "reuse_threshold": 0.2,
            "start_percent": 0.15, "end_percent": 0.95, "verbose": False}},
        "8": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["3", 0], "vae": ["4", 0], "prompt": prompt,
            "width": width, "height": height, "length": length,
            "first_frame": ["1", 0]}},
        "9": {"class_type": "BasicGuider", "inputs": {"model": ["7", 0], "conditioning": ["8", 0]}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {"class_type": "BasicScheduler", "inputs": {
            "model": ["7", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["10", 0], "guider": ["9", 0], "sampler": ["12", 0],
            "sigmas": ["11", 0], "latent_image": ["8", 1]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["4", 0]}},
        "15": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["13", 0], "vae": ["5", 0]}},
        "16": {"class_type": "CreateVideo", "inputs": {"images": ["14", 0], "audio": ["15", 0], "fps": 24}},
        "17": {"class_type": "SaveVideo", "inputs": {
            "video": ["16", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto"}},
    }


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


def queue_workflow(wf: dict) -> str:
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"prompt 排队失败 {r.status_code}: {r.text[:500]}")
    j = r.json()
    pid = j.get("prompt_id")
    if not pid:
        raise RuntimeError(f"未返回 prompt_id: {j}")
    return pid


def get_queue_position(pid: str) -> int:
    """查 /queue 拿当前 prompt 在队列中的位置（0 表示正在执行）。"""
    try:
        r = requests.get(f"{COMFY}/queue", timeout=10)
        r.raise_for_status()
        j = r.json()
        # pending 列表里找 prompt_id
        for i, item in enumerate(j.get("queue_pending", [])):
            if len(item) >= 2 and item[1] == pid:
                return i + 1
        # 正在执行（queue_running）
        for i, item in enumerate(j.get("queue_running", [])):
            if len(item) >= 2 and item[1] == pid:
                return 0
        return -1  # 找不到，可能已完成
    except Exception:
        return -2


def poll_history(pid: str, log_prefix: str = "") -> dict:
    """轮询 history 每 30s 报一次进度。"""
    deadline = time.time() + POLL_TIMEOUT
    last_log = 0
    while time.time() < deadline:
        r = requests.get(f"{COMFY}/history/{pid}", timeout=30)
        r.raise_for_status()
        j = r.json()
        if pid in j:
            status = j[pid].get("status", {})
            if status.get("completed"):
                return j[pid]
            if status.get("status_str") == "error":
                raise RuntimeError(
                    f"ComfyUI 执行出错: {json.dumps(status, ensure_ascii=False)[:800]}")
        now = time.time()
        if now - last_log > 30:
            pos = get_queue_position(pid)
            elapsed = round(POLL_TIMEOUT - (deadline - now), 1)
            if pos > 0:
                print(f"  {log_prefix}[排队中] 位置={pos} 已等 {elapsed}s", flush=True)
            elif pos == 0:
                print(f"  {log_prefix}[执行中] 已等 {elapsed}s", flush=True)
            last_log = now
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


def gen_one(shot: int, prompt_file: Path, out: Path, *,
            width: int = 768, height: int = 1344, length: int = 192,
            seed: int | None = None, steps: int = 20) -> dict:
    """生成单段 T2V 视频。"""
    seed_frame = make_seed_frame(width, height)
    seed_to_use = seed if seed is not None else SEGMENT_META.get(shot, {}).get("seed", random.randint(10001, 99999))
    prefix = f"ab_t2v_shot{shot:02d}"
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt 文件为空: {prompt_file}")

    t0 = time.time()
    print(f"[gen] shot{shot:02d} 开始 seed={seed_to_use} length={length} "
          f"size={width}x{height} steps={steps}", flush=True)
    print(f"[gen] prompt 长度 {len(prompt)} chars", flush=True)
    print(f"[gen] prompt 文件: {prompt_file}", flush=True)

    name = upload_image(seed_frame)
    print(f"[gen] 占位首帧已上传: {name}", flush=True)

    wf = build_h3_workflow(prompt, name, width, height, length, seed_to_use, prefix, steps=steps)
    pid = queue_workflow(wf)
    print(f"[gen] 已排队 prompt_id={pid}", flush=True)

    log_prefix = f"shot{shot:02d} "
    entry = poll_history(pid, log_prefix=log_prefix)
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen] 输出: {fn} (sub={sub!r}, type={vtype!r})", flush=True)

    download_video(fn, sub, vtype, out)
    dt = time.time() - t0
    print(f"[gen] 完成 shot{shot:02d} -> {out} 耗时 {dt:.1f}s", flush=True)

    meta = {
        "shot": shot, "seed": seed_to_use, "length": length,
        "width": width, "height": height, "steps": steps,
        "prompt_id": pid, "comfy_filename": fn, "subfolder": sub, "type": vtype,
        "elapsed_sec": round(dt, 1), "prompt_file": str(prompt_file),
        "prompt_chars": len(prompt),
        "method": "pure_t2v_blank_first_frame",
        "first_frame_seed": str(seed_frame),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    meta_path = out.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def gen_batch(prompts_dir: Path, only: list[int] | None = None) -> dict:
    """批量生成 8 段。prompts_dir/a_shot{n:02d}.txt 命名。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    shots = sorted(SEGMENT_META.keys())
    if only:
        shots = [s for s in shots if s in only]
    for shot in shots:
        pf = prompts_dir / f"a_shot{shot:02d}.txt"
        if not pf.exists():
            print(f"[batch] 跳过 shot{shot:02d}：prompt 文件 {pf} 不存在", flush=True)
            continue
        out = OUT_DIR / f"a_shot{shot:02d}.mp4"
        try:
            meta = gen_one(shot, pf, out)
            results[shot] = {"ok": True, "path": str(out), "meta": meta}
        except Exception as e:  # noqa: BLE001
            print(f"[batch] shot{shot:02d} 失败: {e}", flush=True)
            results[shot] = {"ok": False, "error": str(e)}
    summary = OUT_DIR / "batch_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch] 完成 {ok_cnt}/{len(results)} 段 → {summary}", flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=int, default=None)
    ap.add_argument("--prompt-file", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1344)
    ap.add_argument("--length", type=int, default=192)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--prompts-dir", default=str(OUT_DIR))
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--only", default=None, help="batch 模式下指定段号，逗号分隔，如 1,3,5")
    args = ap.parse_args(argv)

    if args.batch:
        only = None
        if args.only:
            only = [int(x) for x in args.only.split(",") if x.strip()]
        gen_batch(Path(args.prompts_dir), only=only)
        return 0

    if not (args.shot and args.prompt_file and args.out):
        print("单段模式要求 --shot / --prompt-file / --out 全部指定", file=sys.stderr)
        return 2

    gen_one(args.shot, Path(args.prompt_file), Path(args.out),
            width=args.width, height=args.height, length=args.length,
            seed=args.seed, steps=args.steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
