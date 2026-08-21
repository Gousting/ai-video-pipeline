#!/usr/bin/env python3
"""v3.6.7 Reference-to-Video 序列生成器。

任务书 oc_task_v367.txt §1 §3 §6:
- 用 MiniMaxH3ReferenceToVideo 节点 (参考 token 贯穿每个采样步, 不是首帧续写)
- 必填: clip + vae + audio_vae + prompt + width + height + length + ref_image_size
- ref_images: 多张参考图 (4 张, 来自 input_h3_pv_ref.mp4 t=0.20/8.00/14.03/27.27s)
- ref_image_size: "max" (身份保真最好, 慢几倍但 v367 是身份优先)
- length 训练范围 ~124-362帧(24fps): 124≈5s, 5-15s 安全
- 6 段节奏: 8/6/5/8/5/8s = 192/144/120/192/120/192 帧

vs t2v_seq_v366.py 关键差异:
- 用 ReferenceToVideo 节点 (替代 ImageToVideo 首帧续写)
- 4 张 ref_images (替代 v366 单 first_frame)
- 不依赖段间首尾帧链式 (r2v 的参考 token 永久贯穿, 段间无需级联)
- 每段独立生成, 失败重试不影响其他段
- ref_image_size="max" (慢但身份保真)
- 输出到 clips_v367/ (沿用 shots_v367 路径)

CLI:
    python t2v_seq_v367.py --start 1 --end 6
    python t2v_seq_v367.py --start 3 --end 4 --retries 2
    python t2v_seq_v367.py --start 1 --end 6 --no-skip
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

import requests

ROOT = Path(r"D:\ai-video-pipeline")
REF_VIDEO = ROOT / "input_h3_pv_ref.mp4"
PROMPT_DIR = ROOT / "output" / "pipeline_v36" / "shots_v367"
CLIPS_DIR = ROOT / "output" / "pipeline_v36" / "clips_v367"
REF_IMAGES_DIR = ROOT / "output" / "pipeline_v36" / "ref_images_v367"

COMFY = "http://192.168.0.105:8188"
POLL_INTERVAL = 10
POLL_TIMEOUT = 1800

UNET_NAME = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

FPS = 24
RES_W = 1344
RES_H = 576
REF_IMAGE_SIZE = "max"  # 身份保真优先 (任务书 §1)

# 4 张 ref_images (任务书 §3.1, VLM 选定的 stable_frames)
REF_IMAGE_INDICES = [1, 4, 7, 11]
REF_IMAGE_TIMES = {1: 0.20, 4: 8.00, 7: 14.03, 11: 27.27}

# 任务书 §3 段时长 (秒), 沿用 v366 节奏
SEGMENT_DURATIONS_SEC = {1: 8.0, 2: 6.0, 3: 5.0, 4: 8.0, 5: 5.0, 6: 8.0}
H3_LENGTH_FRAMES = {
    i: int(round(SEGMENT_DURATIONS_SEC[i] * FPS))
    for i in range(1, 7)
}

# YAVG 异常检测阈值 (沿用 v366 阈值)
YAVG_MIN_THRESHOLD = 5.0
YAVG_MAX_THRESHOLD = 245.0


def upload_image(path: Path) -> str:
    with path.open("rb") as f:
        files = {"image": (path.name, f, "image/jpeg")}
        data = {"overwrite": "true"}
        r = requests.post(f"{COMFY}/upload/image", files=files, data=data,
                         timeout=60)
    r.raise_for_status()
    j = r.json()
    name = j.get("name")
    if not name:
        raise RuntimeError(f"upload 未返回 name: {j}")
    return name


def upload_ref_images(manifest: dict) -> list[str]:
    """上传 4 张 ref_images 到 ComfyUI, 返回 upload 后的文件名列表。"""
    names = []
    for entry in manifest["ref_images"]:
        path = Path(entry["ref_path"])
        if not path.exists():
            raise FileNotFoundError(f"ref_image 缺失: {path}")
        name = upload_image(path)
        names.append(name)
        print(f"  uploaded ref #{entry['idx']} t={entry['t_sec']:.2f}s "
              f"-> {name} ({path.stat().st_size//1024} KB)", flush=True)
    return names


def build_h3_r2v_workflow(prompt: str, ref_image_names: list[str],
                          width: int, height: int, length: int,
                          seed: int, prefix: str, steps: int = 20) -> dict:
    """构造 MiniMaxH3ReferenceToVideo workflow。

    COMFY_AUTOGROW_V3 类型的 ref_images 必须传 LIST of [node, output]
    连接 (每个连接成一个 ref_image_N 槽). 不能传 batched IMAGE tensor.

    节点布局 (R2V 节点 ID 从 1 起):
      1..N: LoadImage (N 张 ref_images)
      N+1: UNETLoader
      N+2: CLIPLoader
      N+3: VAELoader (video)
      N+4: VAELoader (audio)
      N+5: SageAttnPatch
      N+6: EasyCache
      N+7: MiniMaxH3ReferenceToVideo (ref_images = list of [i, 0])
      N+8: BasicGuider
      N+9: RandomNoise
      N+10: BasicScheduler
      N+11: KSamplerSelect
      N+12: SamplerCustomAdvanced
      N+13: VAEDecode (video)
      N+14: VAEDecodeAudio
      N+15: CreateVideo
      N+16: SaveVideo
    """
    n_refs = len(ref_image_names)
    if n_refs < 1:
        raise ValueError("at least 1 ref_image required")

    wf: dict = {}
    # 1..n_refs: LoadImage
    for i, name in enumerate(ref_image_names, start=1):
        wf[str(i)] = {
            "class_type": "LoadImage",
            "inputs": {"image": name},
        }

    next_id = n_refs + 1

    # UNET/CLIP/VAE loaders
    nid_unet = next_id; next_id += 1
    nid_clip = next_id; next_id += 1
    nid_vae_v = next_id; next_id += 1
    nid_vae_a = next_id; next_id += 1
    wf[str(nid_unet)] = {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"},
    }
    wf[str(nid_clip)] = {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": CLIP_NAME, "type": "minimax"},
    }
    wf[str(nid_vae_v)] = {
        "class_type": "VAELoader",
        "inputs": {"vae_name": VIDEO_VAE},
    }
    wf[str(nid_vae_a)] = {
        "class_type": "VAELoader",
        "inputs": {"vae_name": AUDIO_VAE},
    }

    # SageAttn + EasyCache
    nid_sage = next_id; next_id += 1
    nid_cache = next_id; next_id += 1
    wf[str(nid_sage)] = {
        "class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
        "inputs": {"model": [str(nid_unet), 0]},
    }
    wf[str(nid_cache)] = {
        "class_type": "EasyCache",
        "inputs": {
            "model": [str(nid_sage), 0],
            "reuse_threshold": 0.2,
            "start_percent": 0.15,
            "end_percent": 0.95,
            "verbose": False,
        },
    }

    # MiniMaxH3ReferenceToVideo
    # ref_images 必须是 LIST of [node_id, output_idx] (COMFY_AUTOGROW_V3)
    ref_images_conns = [[str(i), 0] for i in range(1, n_refs + 1)]
    nid_r2v = next_id; next_id += 1
    wf[str(nid_r2v)] = {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": {
            "clip": [str(nid_clip), 0],
            "vae": [str(nid_vae_v), 0],
            "audio_vae": [str(nid_vae_a), 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": length,
            "ref_image_size": REF_IMAGE_SIZE,
            "ref_images": ref_images_conns,
        },
    }

    # BasicGuider
    nid_guider = next_id; next_id += 1
    wf[str(nid_guider)] = {
        "class_type": "BasicGuider",
        "inputs": {"model": [str(nid_cache), 0],
                   "conditioning": [str(nid_r2v), 0]},
    }

    # RandomNoise + Scheduler
    nid_noise = next_id; next_id += 1
    nid_sched = next_id; next_id += 1
    nid_ksampler = next_id; next_id += 1
    nid_sample = next_id; next_id += 1
    wf[str(nid_noise)] = {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": seed},
    }
    wf[str(nid_sched)] = {
        "class_type": "BasicScheduler",
        "inputs": {"model": [str(nid_cache), 0], "scheduler": "simple",
                   "steps": steps, "denoise": 1.0},
    }
    wf[str(nid_ksampler)] = {
        "class_type": "KSamplerSelect",
        "inputs": {"sampler_name": "res_multistep"},
    }
    wf[str(nid_sample)] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": [str(nid_noise), 0],
            "guider": [str(nid_guider), 0],
            "sampler": [str(nid_ksampler), 0],
            "sigmas": [str(nid_sched), 0],
            "latent_image": [str(nid_r2v), 1],
        },
    }

    # VAE decode video + audio
    nid_vae_dec_v = next_id; next_id += 1
    nid_vae_dec_a = next_id; next_id += 1
    wf[str(nid_vae_dec_v)] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": [str(nid_sample), 0], "vae": [str(nid_vae_v), 0]},
    }
    wf[str(nid_vae_dec_a)] = {
        "class_type": "VAEDecodeAudio",
        "inputs": {"samples": [str(nid_sample), 0], "vae": [str(nid_vae_a), 0]},
    }

    # CreateVideo + SaveVideo
    nid_create = next_id; next_id += 1
    nid_save = next_id; next_id += 1
    wf[str(nid_create)] = {
        "class_type": "CreateVideo",
        "inputs": {"images": [str(nid_vae_dec_v), 0],
                   "audio": [str(nid_vae_dec_a), 0],
                   "fps": FPS},
    }
    wf[str(nid_save)] = {
        "class_type": "SaveVideo",
        "inputs": {"video": [str(nid_create), 0],
                   "filename_prefix": prefix,
                   "format": "auto", "codec": "auto"},
    }

    return wf


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
            elapsed = round(now - (deadline - POLL_TIMEOUT), 1)
            if pos > 0:
                print(f"  {log_prefix}[排队中] 位置={pos} 已等 {elapsed}s",
                      flush=True)
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
    raise RuntimeError(
        f"history 未找到视频输出: {json.dumps(entry, ensure_ascii=False)[:800]}")


def download_video(filename: str, subfolder: str, vtype: str,
                   out: Path) -> None:
    params = {"filename": filename, "subfolder": subfolder, "type": vtype}
    r = requests.get(f"{COMFY}/view", params=params, timeout=600)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)


def yavg_at(video_path: Path, t_sec: float) -> tuple[float | None, str]:
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=320:-1,signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.splitlines():
        line_l = line.lower()
        if "yavg=" in line_l:
            try:
                val = line_l.split("yavg=")[1].split()[0].rstrip(",")
                return float(val), "ok"
            except (ValueError, IndexError):
                continue
    return None, f"yavg not found: {out[-200:]}"


def yavg_check(video_path: Path, duration_sec: float) -> dict:
    samples = {}
    notes = {}
    for label, t in (
        ("first", 0.05),
        ("q1", max(0.3, duration_sec * 0.25)),
        ("mid", max(0.5, duration_sec * 0.5)),
        ("q3", max(0.3, duration_sec * 0.75)),
        ("last", max(0.1, duration_sec - 0.15)),
    ):
        ya, note = yavg_at(video_path, t)
        samples[label] = ya
        notes[label] = note
    valid = [v for v in samples.values() if v is not None]
    has_white = any(v > YAVG_MAX_THRESHOLD for v in valid)
    has_black = any(v < YAVG_MIN_THRESHOLD for v in valid)
    ok = (not has_white) and (not has_black) and len(valid) == 5
    return {
        "samples": samples,
        "notes": notes,
        "has_white": has_white,
        "has_black": has_black,
        "ok": ok,
    }


def selfcheck_filmstrip(video_path: Path, out_path: Path,
                        n_frames: int = 6) -> dict:
    from PIL import Image
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    dur = float(lines[0]) if lines else 5.0
    ts = [dur * (i + 0.5) / n_frames for i in range(n_frames)]
    tmpdir = out_path.parent / f"tmp_fc_{video_path.stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(ts):
        fp = tmpdir / f"frame_{i+1:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
             "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(fp)],
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


def gen_one_v367(shot_idx: int, prompt_path: Path, meta_path: Path,
                 out_path: Path, ref_image_names: list[str], *,
                 seed: int | None = None,
                 steps: int = 20) -> dict:
    """生成单段 H3 R2V, ref_images 已在 ComfyUI 服务端, 直接喂 workflow。"""
    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    seed_used = seed if seed is not None else meta.get("seed", 33670 + shot_idx)
    if "seed" not in meta or seed is not None:
        meta["seed"] = seed_used

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt 文件为空: {prompt_path}")

    duration_sec = float(meta.get("duration_sec", SEGMENT_DURATIONS_SEC[shot_idx]))
    h3_length = H3_LENGTH_FRAMES[shot_idx]

    t0 = time.time()
    prefix = f"v367_r2v_shot{shot_idx:02d}"
    wf = build_h3_r2v_workflow(
        prompt=prompt, ref_image_names=ref_image_names,
        width=RES_W, height=RES_H,
        length=h3_length, seed=seed_used, prefix=prefix, steps=steps,
    )
    pid = queue_workflow(wf)
    print(f"[gen-v367] shot{shot_idx:02d} queued pid={pid} "
          f"length={h3_length}fr ref_imgs={len(ref_image_names)}",
          flush=True)

    entry = poll_history(pid, log_prefix=f"shot{shot_idx:02d} ")
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen-v367] shot{shot_idx:02d} output: {fn}", flush=True)

    download_video(fn, sub, vtype, out_path)
    dt = time.time() - t0
    print(f"[gen-v367] shot{shot_idx:02d} done -> {out_path.name} "
          f"in {dt:.1f}s", flush=True)

    ya = yavg_check(out_path, duration_sec)
    print(f"[gen-v367] YAVG check: first={ya['samples']['first']:.1f} "
          f"mid={ya['samples']['mid']:.1f} last={ya['samples']['last']:.1f} "
          f"ok={ya['ok']}", flush=True)

    qa_dir = CLIPS_DIR / "qa" / "selfcheck"
    fc_path = qa_dir / f"shot{shot_idx:02d}_filmstrip_v367_6.jpg"
    fc_meta = selfcheck_filmstrip(out_path, fc_path)

    meta.update({
        "shot": shot_idx,
        "actual_seed_used": seed_used,
        "prompt_id": pid,
        "comfy_filename": fn,
        "subfolder": sub,
        "type": vtype,
        "elapsed_sec": round(dt, 1),
        "prompt_file": str(prompt_path),
        "prompt_chars": len(prompt),
        "method": "reference_to_video_v367",
        "node_class": "MiniMaxH3ReferenceToVideo",
        "ref_image_size": REF_IMAGE_SIZE,
        "ref_images_count": len(ref_image_names),
        "ref_images_indices": REF_IMAGE_INDICES,
        "selfcheck_filmstrip": fc_meta["path"],
        "yavg_check": ya,
        "h3_length_frames": h3_length,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pipeline_version": "v3.6.7",
        "lora_enabled": False,
        "character_drift_accepted": False,
        "open_with_character_required": True,
        "no_fade_in_required": True,
        "no_abstract_transition_words": True,
        "horizontal_2_36_1_required": True,
        "r2v_method": True,
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return meta


def run_serial(start: int, end: int, *, skip_existing: bool = False,
               max_retries: int = 3) -> dict:
    """v367 生成 (r2v: 段间无依赖, 但为简化串行处理保持顺序)."""
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    (CLIPS_DIR / "qa" / "selfcheck").mkdir(parents=True, exist_ok=True)

    # 一次性上传所有 ref_images
    manifest_path = REF_IMAGES_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"ref_images manifest 缺失: {manifest_path}; "
            "请先运行: python scripts/prepare_ref_images_v367.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"[batch-v367] 上传 {len(manifest['ref_images'])} 张 ref_images "
          f"to ComfyUI", flush=True)
    ref_image_names = upload_ref_images(manifest)

    results: dict = {}
    t_total = time.time()

    for shot_idx in range(start, end + 1):
        prompt_path = PROMPT_DIR / f"shot{shot_idx:02d}_prompt.txt"
        meta_path = PROMPT_DIR / f"shot{shot_idx:02d}_meta.json"
        out_path = CLIPS_DIR / f"shot{shot_idx:02d}.mp4"

        if not prompt_path.exists():
            print(f"[batch-v367] shot{shot_idx:02d} prompt 不存在 "
                  f"{prompt_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "prompt missing"}
            continue
        if not meta_path.exists():
            print(f"[batch-v367] shot{shot_idx:02d} meta 不存在 "
                  f"{meta_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "meta missing"}
            continue

        if skip_existing and out_path.exists() and out_path.stat().st_size > 100_000:
            print(f"[batch-v367] shot{shot_idx:02d} 已存在 "
                  f"{out_path.stat().st_size//1024} KB, 跳过", flush=True)
            results[shot_idx] = {"ok": True, "skipped": True,
                                 "path": str(out_path)}
            continue

        attempt = 0
        last_err = None
        while attempt <= max_retries:
            try:
                t0 = time.time()
                seed_override = None
                if attempt > 0:
                    seed_override = 33670 + shot_idx * 100 + attempt * 9999
                    print(f"[batch-v367] shot{shot_idx:02d} 第 {attempt+1} 次尝试"
                          f"（换 seed={seed_override}）", flush=True)
                m = gen_one_v367(shot_idx, prompt_path, meta_path, out_path,
                                 ref_image_names, seed=seed_override)
                dt = time.time() - t0
                if not m["yavg_check"]["ok"]:
                    raise RuntimeError(
                        f"YAVG check failed: white={m['yavg_check']['has_white']} "
                        f"black={m['yavg_check']['has_black']}")
                results[shot_idx] = {"ok": True, "path": str(out_path),
                                     "elapsed_sec": round(dt, 1), "meta": m}
                print(f"[batch-v367] shot{shot_idx:02d} 完成 累计 "
                      f"{time.time()-t_total:.1f}s", flush=True)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[batch-v367] shot{shot_idx:02d} 第 {attempt+1} 次失败: {e}",
                      flush=True)
                attempt += 1
        if last_err is not None:
            results[shot_idx] = {"ok": False, "error": str(last_err),
                                 "attempts": attempt}

    summary = CLIPS_DIR / "seq_v367_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch-v367] 全部完成 {ok_cnt}/{len(results)} 段 总耗时 "
          f"{time.time()-t_total:.1f}s -> {summary}", flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=6)
    ap.add_argument("--skip-existing", action="store_true",
                    help="跳过已存在的 shot clip")
    ap.add_argument("--no-skip", action="store_true",
                    help="(显式不跳过)")
    ap.add_argument("--retries", type=int, default=3,
                    help="单段最大重试次数")
    args = ap.parse_args(argv)

    skip_existing = bool(args.skip_existing) and not bool(args.no_skip)
    run_serial(args.start, args.end,
               skip_existing=skip_existing,
               max_retries=args.retries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
