#!/usr/bin/env python3
"""v3.1 串行 T2V/I2V 生成：段间引导工艺。

核心差异 vs t2v_batch.py（v3）：
  - **串行**（v3 是顺序但每段独立，v3.1 在前段完成后立即抽尾帧喂下一段）
  - shot01 复用 v3 已生成的 shot01.mp4（任务决策"shot01 复用"）
  - shot02 起每段：抽上一段尾帧 → 上传 ComfyUI → first_frame = 该尾帧 → 生成
  - 每段 prompt 来自 shot{NN}_prompt_v31.txt（含 continuity 措辞）
  - 不使用 R2V ref2va 全链路锁角色——只做 I2V 首帧锚定（轻量）
  - 失败重试：换 seed 最多 2 次（任务要求）

执行流程：
  1. shot01.mp4 已存在 → 抽尾帧 -> links/shot01_last.png
  2. shot02: 上传 shot01_last.png → 排队 → 等待 → 下载 shot02.mp4
  3. 抽 shot02尾帧 -> links/shot02_last.png
  4. shot03: 上传 shot02_last.png → 排队 → ... 直至 shot08
  5. 每段生成后自动 filmstrip 自查（filmstrip.py 现有工具，复用）

CLI:
  python t2v_seq_v31.py                     # 完整串行 shot02-shot08（默认）
  python t2v_seq_v31.py --start 2 --end 5   # 指定段范围
  python t2v_seq_v31.py --start 5           # 从 5 开始，shot04 的尾帧必须已存在
  python t2v_seq_v31.py --no-skip           # 强制重跑（默认已存在则跳过）
  python t2v_seq_v31.py --retries 2         # 单段最大重试次数（默认 2）
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# ComfyUI 8188 端点（与 t2v_batch.py 完全一致）
# ---------------------------------------------------------------------------
COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 10
POLL_TIMEOUT = 2700  # 45 分钟（v3 单段实测 ~14 min，含排队余量）

UNET_NAME = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

ROOT = Path(r"D:\ai-video-pipeline")
CLIPS_DIR = ROOT / "output" / "pipeline_v3" / "clips"
LINKS_DIR = ROOT / "output" / "pipeline_v3" / "links"
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
    """H3 MiniMaxH3ImageToVideo 工作流（与 t2v_batch.py 完全一致）。"""
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
# 段间引导工具（用 v31_extract_tail.py）
# ---------------------------------------------------------------------------

def extract_tail(video: Path, out: Path, offset_sec: float = 0.1,
                 width: int = 768, height: int = 1344) -> dict:
    """抽视频末帧 → 下一段 I2V first_frame。"""
    import subprocess
    out.parent.mkdir(parents=True, exist_ok=True)
    dur_r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(dur_r.stdout.strip() or 0.0)
    if dur <= 0:
        raise RuntimeError(f"无法获取时长: {video}")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-sseof", f"-{offset_sec:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-update", "1",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg 抽帧失败 rc={r.returncode}")
    print(f"[tail] {video.name} (dur={dur:.2f}s) -> {out.name} "
          f"({out.stat().st_size//1024} KB)", flush=True)
    return {"video": str(video), "out": str(out), "duration": round(dur, 3)}


# ---------------------------------------------------------------------------
# 自查 filmstrip（复用 t2v_batch.selfcheck_filmstrip 逻辑）
# ---------------------------------------------------------------------------

def selfcheck_filmstrip(video_path: Path, out_path: Path, n_frames: int = 6) -> dict:
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
# 单段生成（含 I2V first_frame 锚定）
# ---------------------------------------------------------------------------

def gen_one_v31(shot_idx: int, prev_video: Path, prompt_path: Path,
                meta_path: Path, out_path: Path, *,
                width: int = 768, height: int = 1344, length: int = 192,
                steps: int = 20, seed_override: int | None = None) -> dict:
    """v3.1 单段生成：抽 prev 尾帧 → 上传 → I2V 排队 → 下载。"""
    # 1. 抽前段尾帧
    LINKS_DIR.mkdir(parents=True, exist_ok=True)
    tail_path = LINKS_DIR / f"shot{shot_idx - 1:02d}_last.png"
    extract_tail(prev_video, tail_path, offset_sec=0.1, width=width, height=height)

    # 2. 读 meta（取 seed）
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    seed = seed_override if seed_override is not None else meta["seed"]

    # 3. 读 prompt
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt 文件为空: {prompt_path}")

    # 4. 上传 tail 帧
    t0 = time.time()
    print(f"[gen] shot{shot_idx:02d} 开始 seed={seed} length={length} "
          f"size={width}x{height} steps={steps} first_frame=tail({shot_idx-1:02d})", flush=True)
    print(f"[gen] prompt 长度 {len(prompt)} chars", flush=True)

    first_frame_name = upload_image(tail_path)
    print(f"[gen] 尾帧已上传: {first_frame_name}", flush=True)

    # 5. 排队（与 t2v_batch.py 完全相同的 workflow）
    prefix = f"v31_i2v_shot{shot_idx:02d}"
    wf = build_h3_workflow(prompt, first_frame_name, width, height, length, seed, prefix, steps)
    pid = queue_workflow(wf)
    print(f"[gen] 已排队 prompt_id={pid}", flush=True)

    # 6. 轮询
    entry = poll_history(pid, log_prefix=f"shot{shot_idx:02d} ")
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen] 输出: {fn} (sub={sub!r}, type={vtype!r})", flush=True)

    # 7. 下载
    download_video(fn, sub, vtype, out_path)
    dt = time.time() - t0
    print(f"[gen] 完成 shot{shot_idx:02d} -> {out_path.name} 耗时 {dt:.1f}s", flush=True)

    # 8. filmstrip 自查
    qa_dir = out_path.parent / "qa" / "selfcheck"
    fc_path = qa_dir / f"shot{shot_idx:02d}_filmstrip_v31_6.jpg"
    fc_meta = selfcheck_filmstrip(out_path, fc_path)

    # 9. 写 meta（含 v3.1 段间引导证据）
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
        "method": "t2v_with_tail_frame_i2v_guidance",
        "first_frame_seed": str(tail_path),
        "prev_shot_video": str(prev_video),
        "selfcheck_filmstrip": fc_meta["path"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pipeline_version": "v3.1",
        "lora_enabled": False,
        "continuity_block": True,
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# ---------------------------------------------------------------------------
# 串行调度
# ---------------------------------------------------------------------------

def run_serial(start: int, end: int, *, skip_existing: bool = True,
               max_retries: int = 2) -> dict:
    """串行生成 shot[start..end]，每段依赖前一段的输出视频。"""
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    LINKS_DIR.mkdir(parents=True, exist_ok=True)

    results = {}
    t_total = time.time()

    for shot_idx in range(start, end + 1):
        prompt_path = CLIPS_DIR / f"shot{shot_idx:02d}_prompt_v31.txt"
        meta_path = CLIPS_DIR / f"shot{shot_idx:02d}_meta.json"
        out_path = CLIPS_DIR / f"shot{shot_idx:02d}.mp4"

        if not prompt_path.exists():
            print(f"[batch] shot{shot_idx:02d} prompt_v31 不存在 {prompt_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "prompt_v31 missing"}
            continue
        if not meta_path.exists():
            print(f"[batch] shot{shot_idx:02d} meta 不存在 {meta_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "meta missing"}
            continue

        # 找前段视频（shot_idx > 1 需要）
        if shot_idx == 1:
            prev_video = None  # shot01 复用，不在这里跑
        else:
            prev_video = CLIPS_DIR / f"shot{shot_idx - 1:02d}.mp4"
            if not prev_video.exists():
                print(f"[batch] shot{shot_idx:02d} 依赖前段 {prev_video.name} 不存在, 跳过", flush=True)
                results[shot_idx] = {"ok": False, "error": f"prev shot missing: {prev_video}"}
                continue

        # 跳过：已存在且大小足够
        if skip_existing and out_path.exists() and out_path.stat().st_size > 100_000:
            print(f"[batch] shot{shot_idx:02d} 已存在 {out_path.stat().st_size//1024} KB, 跳过", flush=True)
            results[shot_idx] = {"ok": True, "skipped": True, "path": str(out_path)}
            continue

        attempt = 0
        last_err = None
        while attempt <= max_retries:
            try:
                t0 = time.time()
                seed_override = None
                if attempt > 0:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    seed_override = meta["seed"] + attempt * 9999
                    print(f"[batch] shot{shot_idx:02d} 第 {attempt+1} 次尝试（换 seed={seed_override}）", flush=True)
                meta = gen_one_v31(shot_idx, prev_video, prompt_path, meta_path, out_path,
                                   seed_override=seed_override)
                dt = time.time() - t0
                results[shot_idx] = {"ok": True, "path": str(out_path),
                                     "elapsed_sec": round(dt, 1), "meta": meta}
                print(f"[batch] shot{shot_idx:02d} 完成 累计 {time.time()-t_total:.1f}s", flush=True)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[batch] shot{shot_idx:02d} 第 {attempt+1} 次失败: {e}", flush=True)
                attempt += 1
        if last_err is not None:
            results[shot_idx] = {"ok": False, "error": str(last_err), "attempts": attempt}

    summary = CLIPS_DIR / "seq_v31_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch] 全部完成 {ok_cnt}/{len(results)} 段 总耗时 {time.time()-t_total:.1f}s -> {summary}",
          flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2, help="起始段（默认 2；shot01 复用）")
    ap.add_argument("--end", type=int, default=8, help="结束段（默认 8）")
    ap.add_argument("--no-skip", action="store_true", help="强制重跑")
    ap.add_argument("--retries", type=int, default=2, help="单段最大重试次数（默认 2）")
    args = ap.parse_args(argv)

    run_serial(args.start, args.end,
               skip_existing=not args.no_skip,
               max_retries=args.retries)
    return 0


if __name__ == "__main__":
    sys.exit(main())