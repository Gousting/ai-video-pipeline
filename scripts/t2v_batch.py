#!/usr/bin/env python3
"""v3.0 T2V 批量生成：H3 MiniMax-Hailuo-03 纯 T2V 直出。

基于 ab_t2v_gen.py + ab_batch.py 重构：
  - 输出路径改为 output/pipeline_v3/clips/
  - 输入改为 v3 prompt-pack 的 shot{NN}_prompt.txt + shot{NN}_meta.json
  - 移除 LoRA 相关代码（v3-lora-verdict.md：H3 二次元 LoRA 不存在）
  - 保留 blank-first-frame T2V 模式（与 ab_t2v_gen.py 一致）
  - 保留 selfcheck filmstrip 自动生成（换 seed 重跑 1 次）

CLI:
  python t2v_batch.py                        # 跑全部 v3 段（v3 路径）
  python t2v_batch.py --only 2,3,4          # 只跑指定段
  python t2v_batch.py --no-skip             # 强制重跑（不跳过已存在）
  python t2v_batch.py --retries 2           # 失败重试次数（默认 1）
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

# ComfyUI 配置
COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 5
POLL_TIMEOUT = 1800  # 30 分钟（abtest 实测单段最长 ~30 分钟）

# H3 权重（与 ab_t2v_gen.py / pipeline.yaml 一致）
UNET_NAME = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

# v3 路径（pipeline.yaml stage 3 artifact_dir）
ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_OUT_DIR = ROOT / "output" / "pipeline_v3" / "clips"
SEED_FRAME = DEFAULT_OUT_DIR / "_seed_blank_768x1344.png"


def make_seed_frame(width: int = 768, height: int = 1344) -> Path:
    """纯白 first_frame 占位图（H3 fl2va T2V 模式）。"""
    from PIL import Image
    SEED_FRAME.parent.mkdir(parents=True, exist_ok=True)
    if not SEED_FRAME.exists():
        img = Image.new("RGB", (width, height), (255, 255, 255))
        img.save(SEED_FRAME, "PNG")
    return SEED_FRAME


def build_h3_workflow(prompt: str, first_frame_name: str,
                      width: int, height: int, length: int, seed: int,
                      prefix: str, steps: int = 20) -> dict:
    """H3 T2V workflow（无 LoRA 节点，Plan B 替代）。

    与 ab_t2v_gen.py.build_h3_workflow 一致，但显式标注 v3 标志。
    """
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


# ---------------------------------------------------------------------------
# 自查（filmstrip 6 帧）
# ---------------------------------------------------------------------------

def selfcheck_filmstrip(video_path: Path, out_path: Path, n_frames: int = 6) -> dict:
    """抽 n_frames 帧合成 1xN filmstrip，肉眼 / VLM 自查。"""
    import subprocess
    from PIL import Image

    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(r.stdout.strip() or 8.0)
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

    # 1xN filmstrip
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


# ---------------------------------------------------------------------------
# 单段 / 批量
# ---------------------------------------------------------------------------

def gen_one(shot_idx: int, prompt_file: Path, meta_file: Path, out: Path,
            width: int = 768, height: int = 1344, length: int = 192,
            steps: int = 20, seed_override: int | None = None) -> dict:
    """生成单段 T2V 视频 + 写 meta.json。"""
    seed_frame = make_seed_frame(width, height)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    seed = seed_override if seed_override is not None else meta["seed"]
    prefix = f"v3_t2v_shot{shot_idx:02d}"
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt 文件为空: {prompt_file}")

    t0 = time.time()
    print(f"[gen] shot{shot_idx:02d} 开始 seed={seed} length={length} "
          f"size={width}x{height} steps={steps}", flush=True)
    print(f"[gen] prompt 长度 {len(prompt)} chars", flush=True)

    name = upload_image(seed_frame)
    print(f"[gen] 占位首帧已上传: {name}", flush=True)

    wf = build_h3_workflow(prompt, name, width, height, length, seed, prefix, steps=steps)
    pid = queue_workflow(wf)
    print(f"[gen] 已排队 prompt_id={pid}", flush=True)

    log_prefix = f"shot{shot_idx:02d} "
    entry = poll_history(pid, log_prefix=log_prefix)
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen] 输出: {fn} (sub={sub!r}, type={vtype!r})", flush=True)

    download_video(fn, sub, vtype, out)
    dt = time.time() - t0
    print(f"[gen] 完成 shot{shot_idx:02d} -> {out} 耗时 {dt:.1f}s", flush=True)

    # filmstrip 自查
    qa_dir = out.parent / "qa" / "selfcheck"
    fc_path = qa_dir / f"shot{shot_idx:02d}_filmstrip_6.jpg"
    fc_meta = selfcheck_filmstrip(out, fc_path)

    # 更新 meta
    meta.update({
        "shot": shot_idx,
        "actual_seed_used": seed,
        "prompt_id": pid,
        "comfy_filename": fn,
        "subfolder": sub,
        "type": vtype,
        "elapsed_sec": round(dt, 1),
        "prompt_file": str(prompt_file),
        "prompt_chars": len(prompt),
        "method": "pure_t2v_blank_first_frame",
        "first_frame_seed": str(seed_frame),
        "selfcheck_filmstrip": fc_meta["path"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pipeline_version": "v3.0",
        "lora_enabled": False,
    })
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def gen_batch(out_dir: Path, only: list[int] | None = None,
              skip_existing: bool = True, max_retries: int = 1) -> dict:
    """批量顺序执行 v3 段。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 找所有 shot{NN}_prompt.txt
    prompt_files = sorted(out_dir.glob("shot*_prompt.txt"))
    if not prompt_files:
        raise RuntimeError(f"{out_dir} 下无 shot*_prompt.txt（请先跑 prompt_pack.py）")

    results = {}
    shots = []
    for pf in prompt_files:
        m = pf.stem.replace("shot", "").replace("_prompt", "")
        idx = int(m)
        shots.append(idx)
    shots = sorted(shots)
    if only:
        shots = [s for s in shots if s in only]

    t_total = time.time()
    for shot in shots:
        pf = out_dir / f"shot{shot:02d}_prompt.txt"
        mf = out_dir / f"shot{shot:02d}_meta.json"
        out = out_dir / f"shot{shot:02d}.mp4"
        if skip_existing and out.exists() and out.stat().st_size > 100_000:
            print(f"[batch] shot{shot:02d} 已存在 {out.stat().st_size//1024} KB，跳过", flush=True)
            results[shot] = {"ok": True, "skipped": True, "path": str(out)}
            continue
        if not pf.exists():
            print(f"[batch] shot{shot:02d} prompt 文件 {pf} 不存在，跳过", flush=True)
            results[shot] = {"ok": False, "error": "prompt file missing"}
            continue
        if not mf.exists():
            print(f"[batch] shot{shot:02d} meta 文件 {mf} 不存在，跳过", flush=True)
            results[shot] = {"ok": False, "error": "meta file missing"}
            continue

        attempt = 0
        last_err = None
        while attempt <= max_retries:
            try:
                t0 = time.time()
                # 重试时换 seed（+9999 偏移，避免再撞同样的失败模式）
                seed_override = None
                if attempt > 0:
                    meta = json.loads(mf.read_text(encoding="utf-8"))
                    seed_override = meta["seed"] + attempt * 9999
                    print(f"[batch] shot{shot:02d} 第 {attempt+1} 次尝试（换 seed={seed_override}）", flush=True)
                meta = gen_one(shot, pf, mf, out, seed_override=seed_override)
                dt = time.time() - t0
                results[shot] = {"ok": True, "path": str(out), "elapsed_sec": round(dt, 1), "meta": meta}
                print(f"[batch] shot{shot:02d} 完成 累计 {time.time()-t_total:.1f}s", flush=True)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[batch] shot{shot:02d} 第 {attempt+1} 次尝试失败: {e}", flush=True)
                attempt += 1
        if last_err is not None:
            results[shot] = {"ok": False, "error": str(last_err), "attempts": attempt}

    summary = out_dir / "batch_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch] 全部完成 {ok_cnt}/{len(results)} 段 总耗时 {time.time()-t_total:.1f}s -> {summary}", flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--only", default=None, help="逗号分隔段号，如 2,3,4")
    ap.add_argument("--no-skip", action="store_true", help="强制重跑")
    ap.add_argument("--retries", type=int, default=1, help="单段最大重试次数（默认 1）")
    args = ap.parse_args(argv)

    only = [int(x) for x in args.only.split(",") if x.strip()] if args.only else None
    gen_batch(Path(args.out_dir), only=only, skip_existing=not args.no_skip,
              max_retries=args.retries)
    return 0


if __name__ == "__main__":
    sys.exit(main())