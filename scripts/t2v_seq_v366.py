#!/usr/bin/env python3
"""v3.6.6 链式 I2V 序列生成器。

任务书 oc_task_v366.txt §4 §6:
- 6 段 H3 I2V 串行生成, 每段 first_frame 锚定:
  - shot01: 参考视频 t=0.20s 帧 (1344x576, 缩过)
  - shot02..06: 上个 shot 倒数第 2 帧 (extractLastFrame)
- 横屏 1344x576 / yuv420p / 24fps
- 段长: 8/6/5/8/5/8s = 192/144/120/192/120/192 帧

vs t2v_seq_v365.py 关键差异:
- 不再传 blank seed frame, first_frame 是参考视频的实帧
- 段间自动 extractLastFrame (上一段完成 -> 抽尾帧 -> 喂给下一段)
- 不做 YAVG 严格白帧/黑帧阈值 (v365 是为防止白淡入; v366 锚定实帧, 不会有白帧)
- 但保留: YAVG 异常检测 (中段 Y > 240 仍判故障, 自动重试)
- 输出到 clips_v366/ (沿用 shots_v366 路径)

CLI:
    python t2v_seq_v366.py --start 1 --end 6
    python t2v_seq_v366.py --start 1 --end 1 --no-skip --retries 3
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
PROMPT_DIR = ROOT / "output" / "pipeline_v36" / "shots_v366"
CLIPS_DIR = ROOT / "output" / "pipeline_v36" / "clips_v366"
ANCHORS_DIR = ROOT / "output" / "pipeline_v36" / "ref_frames_v366"

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

# 任务书 §4 段时长 (秒)
SEGMENT_DURATIONS_SEC = {1: 8.0, 2: 6.0, 3: 5.0, 4: 8.0, 5: 5.0, 6: 8.0}

# 任务书 §1: H3 length 训练范围 124-362 帧, 5-15s 安全
# 我们的段时长都在 5-8s, 所以 length 都在安全区
H3_LENGTH_FRAMES = {
    i: int(round(SEGMENT_DURATIONS_SEC[i] * FPS))
    for i in range(1, 7)
}

# YAVG 异常检测阈值 (比 v365 宽松, 因为是 I2V 锚定实帧, 不会有白淡入)
YAVG_MIN_THRESHOLD = 5.0
YAVG_MAX_THRESHOLD = 245.0

# shot01 anchor 时间点 (任务书 §3: 参考视频 0.2s)
REF_ANCHOR_TIME_SEC = 0.20


def extract_ref_frame_at(t_sec: float, out_path: Path,
                         video: Path | None = None) -> Path:
    """从参考视频抽 1 帧, 缩到 H3 step=32 尺寸 (1344x576)."""
    src = video or REF_VIDEO
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-vf", (f"scale={RES_W}:{RES_H}:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1:1"),
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(
            f"抽帧失败 t={t_sec}s: {r.stderr[-500:] or r.stdout[-500:]}")
    return out_path


def extract_clip_last_frame(clip_path: Path, out_path: Path) -> Path:
    """从 clip 抽倒数第 2 帧 (避开最末帧可能抽风/黑), 缩到 1344x576."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration,nb_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    duration_sec = float(lines[0]) if lines else 5.0
    # 倒数第 2 帧位置 = duration - 0.10s (避开最后一帧)
    ss = max(0.1, duration_sec - 0.10)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{ss:.3f}",
        "-i", str(clip_path),
        "-frames:v", "1",
        "-vf", (f"scale={RES_W}:{RES_H}:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1:1"),
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(
            f"抽 clip 尾帧失败 {clip_path}: {r.stderr[-500:] or r.stdout[-500:]}")
    return out_path


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


def build_h3_i2v_workflow(prompt: str, first_frame_name: str,
                          width: int, height: int, length: int,
                          seed: int, prefix: str, steps: int = 20) -> dict:
    return {
        "1": {"class_type": "LoadImage",
              "inputs": {"image": first_frame_name}},
        "2": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP_NAME, "type": "minimax"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "6": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
              "inputs": {"model": ["2", 0]}},
        "7": {"class_type": "EasyCache", "inputs": {
            "model": ["6", 0], "reuse_threshold": 0.2,
            "start_percent": 0.15, "end_percent": 0.95, "verbose": False}},
        "8": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["3", 0], "vae": ["4", 0], "prompt": prompt,
            "width": width, "height": height, "length": length,
            "first_frame": ["1", 0]}},
        "9": {"class_type": "BasicGuider", "inputs": {
            "model": ["7", 0], "conditioning": ["8", 0]}},
        "10": {"class_type": "RandomNoise",
               "inputs": {"noise_seed": seed}},
        "11": {"class_type": "BasicScheduler", "inputs": {
            "model": ["7", 0], "scheduler": "simple",
            "steps": steps, "denoise": 1.0}},
        "12": {"class_type": "KSamplerSelect",
               "inputs": {"sampler_name": "res_multistep"}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["10", 0], "guider": ["9", 0], "sampler": ["12", 0],
            "sigmas": ["11", 0], "latent_image": ["8", 1]}},
        "14": {"class_type": "VAEDecode", "inputs": {
            "samples": ["13", 0], "vae": ["4", 0]}},
        "15": {"class_type": "VAEDecodeAudio", "inputs": {
            "samples": ["13", 0], "vae": ["5", 0]}},
        "16": {"class_type": "CreateVideo", "inputs": {
            "images": ["14", 0], "audio": ["15", 0], "fps": FPS}},
        "17": {"class_type": "SaveVideo", "inputs": {
            "video": ["16", 0], "filename_prefix": prefix,
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
    raise RuntimeError(f"history 未找到视频输出: {json.dumps(entry, ensure_ascii=False)[:800]}")


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
    """v366 YAVG 检查: 异常白帧 (>245) 或黑帧 (<5) 都判故障。"""
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


def get_anchor_for_shot(shot_idx: int) -> Path:
    """取 shot 的 first_frame 锚点。
    - shot01: 参考视频 t=0.20s 帧 (1344x576)
    - shot02..06: 上个 shot 尾帧 (已生成 clips_v366/shot{N-1}.mp4)
    """
    if shot_idx == 1:
        anchor = ANCHORS_DIR / "shot01_anchor.jpg"
        extract_ref_frame_at(REF_ANCHOR_TIME_SEC, anchor)
        return anchor
    prev_clip = CLIPS_DIR / f"shot{shot_idx-1:02d}.mp4"
    if not prev_clip.exists():
        raise FileNotFoundError(
            f"上一段不存在: {prev_clip}, 必须先串行生成")
    anchor = ANCHORS_DIR / f"shot{shot_idx:02d}_anchor_from_shot{shot_idx-1:02d}.jpg"
    extract_clip_last_frame(prev_clip, anchor)
    return anchor


def gen_one_v366(shot_idx: int, prompt_path: Path, meta_path: Path,
                 out_path: Path, *, seed: int | None = None,
                 steps: int = 20) -> dict:
    """生成单段 H3 I2V, first_frame 自动取锚。"""
    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    seed_used = seed if seed is not None else meta.get("seed", 33600 + shot_idx)
    if "seed" not in meta or seed is not None:
        meta["seed"] = seed_used

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt 文件为空: {prompt_path}")

    duration_sec = float(meta.get("duration_sec", SEGMENT_DURATIONS_SEC[shot_idx]))
    h3_length = H3_LENGTH_FRAMES[shot_idx]

    t0 = time.time()
    anchor = get_anchor_for_shot(shot_idx)
    print(f"[gen-v366] shot{shot_idx:02d} anchor: {anchor}", flush=True)

    first_frame_name = upload_image(anchor)
    print(f"[gen-v366] shot{shot_idx:02d} uploaded first_frame="
          f"{first_frame_name}", flush=True)

    prefix = f"v366_chain_shot{shot_idx:02d}"
    wf = build_h3_i2v_workflow(
        prompt=prompt, first_frame_name=first_frame_name,
        width=RES_W, height=RES_H,
        length=h3_length, seed=seed_used, prefix=prefix, steps=steps,
    )
    pid = queue_workflow(wf)
    print(f"[gen-v366] shot{shot_idx:02d} queued pid={pid}", flush=True)

    entry = poll_history(pid, log_prefix=f"shot{shot_idx:02d} ")
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen-v366] shot{shot_idx:02d} output: {fn}", flush=True)

    download_video(fn, sub, vtype, out_path)
    dt = time.time() - t0
    print(f"[gen-v366] shot{shot_idx:02d} done -> {out_path.name} "
          f"in {dt:.1f}s", flush=True)

    # YAVG 异常检测 (宽松: 实际白帧 >245 或黑帧 <5)
    ya = yavg_check(out_path, duration_sec)
    print(f"[gen-v366] YAVG check: first={ya['samples']['first']:.1f} "
          f"mid={ya['samples']['mid']:.1f} last={ya['samples']['last']:.1f} "
          f"ok={ya['ok']}", flush=True)

    qa_dir = CLIPS_DIR / "qa" / "selfcheck"
    fc_path = qa_dir / f"shot{shot_idx:02d}_filmstrip_v366_6.jpg"
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
        "method": "chain_first_frame_i2v_v366",
        "anchor_frame": str(anchor),
        "anchor_source": (
            f"ref_video_{REF_ANCHOR_TIME_SEC}s"
            if shot_idx == 1
            else f"shot{shot_idx-1:02d}_last_frame_extracted"
        ),
        "selfcheck_filmstrip": fc_meta["path"],
        "yavg_check": ya,
        "h3_length_frames": h3_length,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pipeline_version": "v3.6.6",
        "lora_enabled": False,
        "character_drift_accepted": False,
        "open_with_character_required": True,
        "no_fade_in_required": True,
        "no_abstract_transition_words": True,
        "horizontal_2_36_1_required": True,
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return meta


def run_serial(start: int, end: int, *, skip_existing: bool = False,
               max_retries: int = 3) -> dict:
    """v366 串行生成 (链式: 必须按 shot 顺序, 因为锚定上一段尾帧)。

    链式生成不可并行, 否则锚定不到上一段尾帧。
    """
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    (CLIPS_DIR / "qa" / "selfcheck").mkdir(parents=True, exist_ok=True)
    ANCHORS_DIR.mkdir(parents=True, exist_ok=True)

    results: dict = {}
    t_total = time.time()

    for shot_idx in range(start, end + 1):
        prompt_path = PROMPT_DIR / f"shot{shot_idx:02d}_prompt.txt"
        meta_path = PROMPT_DIR / f"shot{shot_idx:02d}_meta.json"
        out_path = CLIPS_DIR / f"shot{shot_idx:02d}.mp4"

        if not prompt_path.exists():
            print(f"[batch-v366] shot{shot_idx:02d} prompt 不存在 "
                  f"{prompt_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "prompt missing"}
            continue
        if not meta_path.exists():
            print(f"[batch-v366] shot{shot_idx:02d} meta 不存在 "
                  f"{meta_path}, 跳过", flush=True)
            results[shot_idx] = {"ok": False, "error": "meta missing"}
            continue

        if skip_existing and out_path.exists() and out_path.stat().st_size > 100_000:
            print(f"[batch-v366] shot{shot_idx:02d} 已存在 "
                  f"{out_path.stat().st_size//1024} KB, 跳过", flush=True)
            results[shot_idx] = {"ok": True, "skipped": True,
                                 "path": str(out_path)}
            continue

        attempt = 0
        last_err = None
        ok_meta = None
        while attempt <= max_retries:
            try:
                t0 = time.time()
                seed_override = None
                if attempt > 0:
                    seed_override = 33600 + shot_idx * 100 + attempt * 9999
                    print(f"[batch-v366] shot{shot_idx:02d} 第 {attempt+1} 次尝试"
                          f"（换 seed={seed_override}）", flush=True)
                m = gen_one_v366(shot_idx, prompt_path, meta_path, out_path,
                                 seed=seed_override)
                dt = time.time() - t0
                if not m["yavg_check"]["ok"]:
                    raise RuntimeError(
                        f"YAVG check failed: white={m['yavg_check']['has_white']} "
                        f"black={m['yavg_check']['has_black']}")
                results[shot_idx] = {"ok": True, "path": str(out_path),
                                     "elapsed_sec": round(dt, 1), "meta": m}
                print(f"[batch-v366] shot{shot_idx:02d} 完成 累计 "
                      f"{time.time()-t_total:.1f}s", flush=True)
                ok_meta = m
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[batch-v366] shot{shot_idx:02d} 第 {attempt+1} 次失败: {e}",
                      flush=True)
                attempt += 1
        if last_err is not None:
            results[shot_idx] = {"ok": False, "error": str(last_err),
                                 "attempts": attempt}

    summary = CLIPS_DIR / "seq_v366_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch-v366] 全部完成 {ok_cnt}/{len(results)} 段 总耗时 "
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
