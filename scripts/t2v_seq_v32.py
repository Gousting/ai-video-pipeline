#!/usr/bin/env python3
"""v3.2 串行 T2V 生成：6 段 × 10s 纯直出。

vs v3.1 (t2v_seq_v31.py) 关键差异：
  - **无段间引导**：所有段用 blank white first_frame → T2V 模式，不抽尾帧
    （v3.1 的段间引导方向错了 → 任务明确否定）
  - **接受角色漂移**：跨段角色不连续是预期行为，靠快切掩盖
  - **length: 240**（10s @ 24fps），v3.1 是 192（8s）
  - **每段 1-2 in-prompt 转场**：transition 通过 prompt 描述实现，非后期 xfade
  - **失败重试**：换 seed 最多 2 次

执行流程：
  1. 准备 blank white first_frame (768x1344)
  2. shot01-shot06 顺序排队
  3. 每段 prompt 来自 clips_v32/shot{NN}_prompt.txt
  4. 每段生成后自动 filmstrip 自查
  5. 失败换 seed 重跑

CLI:
  python t2v_seq_v32.py
  python t2v_seq_v32.py --start 3 --end 5  # 指定段范围
  python t2v_seq_v32.py --no-skip           # 强制重跑
  python t2v_seq_v32.py --retries 2         # 单段最大重试次数
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 15
POLL_TIMEOUT = 3600  # 60 min/段（含排队余量）

UNET_NAME = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

ROOT = Path(r"D:\ai-video-pipeline")
CLIPS_DIR = ROOT / "output" / "pipeline_v3" / "clips_v32"
SEED_FRAME = CLIPS_DIR / "_seed_blank_768x1344.png"


def make_seed_frame(width: int = 768, height: int = 1344) -> Path:
    from PIL import Image
    SEED_FRAME.parent.mkdir(parents=True, exist_ok=True)
    if not SEED_FRAME.exists():
        img = Image.new("RGB", (width, height), (255, 255, 255))
        img.save(SEED_FRAME, "PNG")
    return SEED_FRAME


def build_h3_workflow(prompt: str, first_frame_name: str,
                      width: int, height: int, length: int, seed: int,
                      prefix: str, steps: int = 20) -> dict:
    """H3 MiniMaxH3ImageToVideo 工作流（与 t2v_seq_v31.py 一致；blank first_frame 走 T2V 模式）。"""
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
    try:
        r = requests.get(f"{COMFY}/queue", timeout=10)
        r.raise_for_status()
        j = r.json()
        for i, item in enumerate(j.get("queue_pending", [])):
            if len(item) >= 2 and item[1] == pid:
                return i + 1
        for i, item in enumerate(j.get("queue_running", [])):
            if len(item) >= 2 and item[1] == pid:
                return 0
        return -1
    except Exception:
        return -2


def poll_history(pid: str, log_prefix: str = "") -> dict:
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


def selfcheck_filmstrip(video_path: Path, out_path: Path, n_frames: int = 6) -> dict:
    import subprocess
    from PIL import Image
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(r.stdout.strip() or 10.0)
    ts = [dur * (i + 0.5) / n_frames for i in range(n_frames)]
    tmpdir = out_path.parent / f"tmp_fc_{video_path.stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(ts):
        fp = tmpdir / f"frame_{i+1:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "3", str(fp)],
            capture_output=True, encoding="utf-8", errors="replace", check=True,
        )
        frames.append(Image.open(fp).convert("RGB"))
    cols = len(frames)
    cell_w = 360
    cell_h = int(cell_w * 16 / 9)
    pad = 6
    border = 4
    canvas_w = cols * cell_w + (cols + 1) * pad + 2 * border
    canvas_h = cell_h + 2 * pad + 2 * border
    canvas = Image.new("RGB", (canvas_w, canvas_h), (24, 24, 24))
    for idx, fr in enumerate(frames):
        cell = fr.copy()
        cell.thumbnail((cell_w, cell_h), Image.LANCZOS)
        cw, ch = cell.size
        ox = border + pad + idx * (cell_w + pad)
        oy = border + pad
        px = ox + (cell_w - cw) // 2
        py = oy + (cell_h - ch) // 2
        canvas.paste(cell, (px, py))
    canvas.save(out_path, "JPEG", quality=85)
    return {"ok": True, "path": str(out_path), "n_frames": n_frames}


def gen_one_v32(shot_idx: int, prompt_path: Path, meta_path: Path,
                out_path: Path, *,
                width: int = 768, height: int = 1344, length: int = 240,
                steps: int = 20, seed_override: int | None = None) -> dict:
    """v3.2 单段生成：blank first_frame → 上传 → 排队 → 下载。"""
    seed_frame = make_seed_frame(width, height)

    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    seed = seed_override if seed_override is not None else meta.get("seed", 32000 + shot_idx * 1000)
    if "seed" not in meta:
        meta["seed"] = seed

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt 文件为空: {prompt_path}")

    t0 = time.time()
    print(f"[gen] shot{shot_idx:02d} 开始 seed={seed} length={length} "
          f"size={width}x{height} steps={steps} first_frame=blank",
          flush=True)
    print(f"[gen] prompt 长度 {len(prompt)} chars", flush=True)

    first_frame_name = upload_image(seed_frame)
    print(f"[gen] blank 已上传: {first_frame_name}", flush=True)

    prefix = f"v32_t2v_shot{shot_idx:02d}"
    wf = build_h3_workflow(prompt, first_frame_name, width, height, length, seed, prefix, steps)
    pid = queue_workflow(wf)
    print(f"[gen] 已排队 prompt_id={pid}", flush=True)

    entry = poll_history(pid, log_prefix=f"shot{shot_idx:02d} ")
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen] 输出: {fn} (sub={sub!r}, type={vtype!r})", flush=True)

    download_video(fn, sub, vtype, out_path)
    dt = time.time() - t0
    print(f"[gen] 完成 shot{shot_idx:02d} -> {out_path.name} 耗时 {dt:.1f}s", flush=True)

    qa_dir = CLIPS_DIR / "qa" / "selfcheck"
    fc_path = qa_dir / f"shot{shot_idx:02d}_filmstrip_v32_6.jpg"
    fc_meta = selfcheck_filmstrip(out_path, fc_path)

    meta.update({
        "shot": shot_idx,
        "actual_seed_used": seed,
        "prompt_id": pid,
        "comfy_filename": fn,
        "subfolder": sub,
        "type": vtype,
        "elapsed_sec": round(dt, 1),
        "prompt_file": str(prompt_path),
        "prompt_chars": len(prompt),
        "method": "t2v_blank_first_frame",
        "first_frame": "blank_white_768x1344",
        "selfcheck_filmstrip": fc_meta["path"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pipeline_version": "v3.2",
        "lora_enabled": False,
        "character_drift_accepted": True,
        "in_prompt_transitions": True,
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def run_serial(start: int, end: int, *, skip_existing: bool = True,
               max_retries: int = 2) -> dict:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    results = {}
    t_total = time.time()

    for shot_idx in range(start, end + 1):
        prompt_path = CLIPS_DIR / f"shot{shot_idx:02d}_prompt.txt"
        meta_path = CLIPS_DIR / f"shot{shot_idx:02d}_meta.json"
        out_path = CLIPS_DIR / f"shot{shot_idx:02d}.mp4"

        if not prompt_path.exists():
            print(f"[batch] shot{shot_idx:02d} prompt 不存在 {prompt_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "prompt missing"}
            continue
        if not meta_path.exists():
            print(f"[batch] shot{shot_idx:02d} meta 不存在 {meta_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "meta missing"}
            continue

        if skip_existing and out_path.exists() and out_path.stat().st_size > 100_000:
            print(f"[batch] shot{shot_idx:02d} 已存在 {out_path.stat().st_size//1024} KB, 跳过",
                  flush=True)
            results[shot_idx] = {"ok": True, "skipped": True, "path": str(out_path)}
            continue

        attempt = 0
        last_err = None
        while attempt <= max_retries:
            try:
                t0 = time.time()
                seed_override = None
                if attempt > 0:
                    meta = json.loads(meta_path.read_text(encoding='utf-8-sig'))
                    seed_override = meta["seed"] + attempt * 9999
                    print(f"[batch] shot{shot_idx:02d} 第 {attempt+1} 次尝试（换 seed={seed_override}）",
                          flush=True)
                meta = gen_one_v32(shot_idx, prompt_path, meta_path, out_path,
                                   seed_override=seed_override)
                dt = time.time() - t0
                results[shot_idx] = {"ok": True, "path": str(out_path),
                                     "elapsed_sec": round(dt, 1), "meta": meta}
                print(f"[batch] shot{shot_idx:02d} 完成 累计 {time.time()-t_total:.1f}s",
                      flush=True)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[batch] shot{shot_idx:02d} 第 {attempt+1} 次失败: {e}", flush=True)
                attempt += 1
        if last_err is not None:
            results[shot_idx] = {"ok": False, "error": str(last_err), "attempts": attempt}

    summary = CLIPS_DIR / "seq_v32_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch] 全部完成 {ok_cnt}/{len(results)} 段 总耗时 {time.time()-t_total:.1f}s -> {summary}",
          flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1, help="起始段（默认 1）")
    ap.add_argument("--end", type=int, default=6, help="结束段（默认 6）")
    ap.add_argument("--no-skip", action="store_true", help="强制重跑")
    ap.add_argument("--retries", type=int, default=2, help="单段最大重试次数（默认 2）")
    args = ap.parse_args(argv)

    run_serial(args.start, args.end,
               skip_existing=not args.no_skip,
               max_retries=args.retries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
