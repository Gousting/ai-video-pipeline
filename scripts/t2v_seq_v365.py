#!/usr/bin/env python3
"""v3.6.5 T2V 串行生成：从 shots_v365 prompt 包读 prompts, 按段提交 H3 T2V 队列。

vs t2v_seq_v364.py 关键差异：

- 读 v365 prompt 包路径 `output/pipeline_v36/shots_v365/`
- 输出到 `output/pipeline_v36/clips_v365/`
- prompt 包含 [TRANSITION_RHYTHM] + [INCOMING_TRANSITION_CUE] 块
  （v365 §2.3：每段首 1-2s 渲染出该段唯一转场特效, 段内不堆砌）
- 默认 skip_existing=False（v365 必须重投生成, 转场是生成层）
- meta 写入新增 transition_effect / has_in_prompt_transition 字段
  （沿用 v364 prompt_pack_v365.py 的 meta 结构）
- YAVG 阈值、首帧非白非黑检查、3 次重试 全部沿用 v364

CLI:
  python t2v_seq_v365.py --start 1 --end 6
  python t2v_seq_v365.py --start 1 --end 1 --no-skip --retries 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

COMFY = "http://127.0.0.1:8188"
POLL_INTERVAL = 15
POLL_TIMEOUT = 3600

UNET_NAME = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

ROOT = Path(r"D:\ai-video-pipeline")
PROMPT_DIR = ROOT / "output" / "pipeline_v36" / "shots_v365"
CLIPS_DIR = ROOT / "output" / "pipeline_v36" / "clips_v365"
SEED_FRAME = CLIPS_DIR / "_seed_blank_768x1344.png"

# 任务书 §3.3 / Step 2 YAVG 阈值
# - 沿用 v364: 中帧 Y > 5 (非黑屏)
# - 首帧阈值在 v365 放宽:
#   * v364: 20 < Y < 220 (防 v3.2/v364 H3 白淡入)
#   * v365: 20 < Y < 245
#     原因: shot04 (color explosion) / shot06 (diagonal slash wipe) 的段首 1-2s
#     是高饱和 CMYK 墨爆/柠檬黄斜切划带, 这种"生成层转场特效"会让首帧 YAVG
#     短暂超过 220, 但这是任务书 §2.3 要求的"段内首 1-2s 渲染出对应效果"的
#     正确行为, 不是白淡入故障. 实测 shot04 first_yavg=231.6, shot06=234.8.
#     放宽到 245 给生成层转场留余量, 同时仍能拦截真正空白的白淡入.
# - 新增 AFTER_TRANSITION_SAMPLE_SEC = 1.5s 采样 (转场应在首 1-2s 内褪去):
#   * 若 1.5s 时 YAVG 仍 > 220, 判定为"转场特效未褪去"故障, 重投.
YAVG_FIRST_MIN = 20.0
YAVG_FIRST_MAX = 245.0
YAVG_MID_MIN = 5.0
AFTER_TRANSITION_SAMPLE_SEC = 1.5
AFTER_TRANSITION_MAX_YAVG = 220.0  # 1.5s 后转场应已褪去, 主场景 YAVG 应回到 < 220


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


def _yavg_at(video_path: Path, t_sec: float) -> tuple[float | None, str]:
    """抽 1 帧测 YAVG。"""
    import subprocess
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
    """任务书 §3.3 / Step 2 YAVG 检查 (v365: 首帧 + 中帧 + 转场后采样).

    - first_yavg (t=0.05s): 20 < Y < 245
      (放宽至 245 以容纳生成层转场特效, 任务书 §2.3)
    - mid_yavg (t=dur/2): Y > 5 (非黑屏)
    - after_transition_yavg (t=1.5s): Y < 220
      (1.5s 后转场应已褪去, 主场景必须回来; 若仍高 Y 说明转场未褪, 故障)
    """
    first_ya, first_note = _yavg_at(video_path, 0.05)
    mid_t = max(0.5, duration_sec * 0.5)
    mid_ya, mid_note = _yavg_at(video_path, mid_t)
    after_t = AFTER_TRANSITION_SAMPLE_SEC if duration_sec > 2.0 else max(0.5, duration_sec * 0.7)
    after_ya, after_note = _yavg_at(video_path, after_t)

    first_pass = (first_ya is not None
                  and YAVG_FIRST_MIN < first_ya < YAVG_FIRST_MAX)
    mid_pass = (mid_ya is not None and mid_ya > YAVG_MID_MIN)
    after_pass = (after_ya is not None
                  and after_ya < AFTER_TRANSITION_MAX_YAVG
                  and after_ya > YAVG_FIRST_MIN)
    ok = bool(first_pass and mid_pass and after_pass)
    note_parts = []
    if not first_pass:
        note_parts.append(
            f"first_yavg={first_ya} NOT in ({YAVG_FIRST_MIN}, "
            f"{YAVG_FIRST_MAX}); note={first_note}")
    if not mid_pass:
        note_parts.append(
            f"mid_yavg={mid_ya} <= {YAVG_MID_MIN}; note={mid_note}")
    if not after_pass:
        note_parts.append(
            f"after_transition_yavg({after_t:.2f}s)={after_ya} NOT in "
            f"({YAVG_FIRST_MIN}, {AFTER_TRANSITION_MAX_YAVG}); "
            f"transition may not have cleared; note={after_note}")
    note = "ok" if ok else "; ".join(note_parts)
    return {
        "first_yavg": first_ya,
        "mid_yavg": mid_ya,
        "after_transition_yavg": after_ya,
        "after_transition_t_sec": after_t,
        "first_pass": bool(first_pass),
        "mid_pass": bool(mid_pass),
        "after_transition_pass": bool(after_pass),
        "ok": ok,
        "note": note,
    }


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


def gen_one_v365(shot_idx: int, prompt_path: Path, meta_path: Path,
                 out_path: Path, *,
                 width: int = 768, height: int = 1344, length: int = 240,
                 steps: int = 20, seed_override: int | None = None) -> dict:
    seed_frame = make_seed_frame(width, height)

    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    seed = seed_override if seed_override is not None else meta.get("seed", 33000 + shot_idx * 1000)
    if "seed" not in meta:
        meta["seed"] = seed

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt 文件为空: {prompt_path}")

    duration_sec = float(meta.get("duration_sec", 10.0))
    h3_length = max(48, int(round(duration_sec * 24)))

    t0 = time.time()
    transition_effect = meta.get("transition_effect")
    tr_str = transition_effect if transition_effect else "(none)"
    print(f"[gen-v365] shot{shot_idx:02d} 开始 seed={seed} length={h3_length} "
          f"size={width}x{height} steps={steps} transition={tr_str}", flush=True)
    print(f"[gen-v365] prompt 长度 {len(prompt)} chars target_dur={duration_sec}s",
          flush=True)

    first_frame_name = upload_image(seed_frame)
    print(f"[gen-v365] blank 已上传: {first_frame_name}", flush=True)

    prefix = f"v365_t2v_shot{shot_idx:02d}"
    wf = build_h3_workflow(prompt, first_frame_name, width, height,
                           h3_length, seed, prefix, steps)
    pid = queue_workflow(wf)
    print(f"[gen-v365] 已排队 prompt_id={pid}", flush=True)

    entry = poll_history(pid, log_prefix=f"shot{shot_idx:02d} ")
    fn, sub, vtype = find_video_output(entry)
    print(f"[gen-v365] 输出: {fn} (sub={sub!r}, type={vtype!r})", flush=True)

    download_video(fn, sub, vtype, out_path)
    dt = time.time() - t0
    print(f"[gen-v365] 完成 shot{shot_idx:02d} -> {out_path.name} 耗时 {dt:.1f}s",
          flush=True)

    # 任务书 §3.3 / Step 2: 首帧 + 中帧 YAVG 检查
    yavg = yavg_check(out_path, duration_sec)
    print(f"[gen-v365] YAVG check first={yavg['first_yavg']} "
          f"mid={yavg['mid_yavg']} ok={yavg['ok']}", flush=True)

    qa_dir = CLIPS_DIR / "qa" / "selfcheck"
    fc_path = qa_dir / f"shot{shot_idx:02d}_filmstrip_v365_6.jpg"
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
        "method": "t2v_blank_first_frame_v365",
        "first_frame": "blank_white_768x1344",
        "selfcheck_filmstrip": fc_meta["path"],
        "yavg_check": yavg,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pipeline_version": "v3.6.5",
        "lora_enabled": False,
        "character_drift_accepted": True,
        "open_with_character_required": True,
        "no_fade_in_required": True,
        "in_prompt_transition_required": (transition_effect is not None),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return meta


def run_serial(start: int, end: int, *, skip_existing: bool = False,
               max_retries: int = 3) -> dict:
    """v365 默认 skip_existing=False: 必须重投生成 (转场是生成层)。

    若想跳过已存在 (调试), 显式传 --skip-existing。
    """
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    results: dict = {}
    t_total = time.time()

    for shot_idx in range(start, end + 1):
        prompt_path = PROMPT_DIR / f"shot{shot_idx:02d}_prompt.txt"
        meta_path = PROMPT_DIR / f"shot{shot_idx:02d}_meta.json"
        out_path = CLIPS_DIR / f"shot{shot_idx:02d}.mp4"

        if not prompt_path.exists():
            print(f"[batch-v365] shot{shot_idx:02d} prompt 不存在 {prompt_path}, 跳过",
                  flush=True)
            results[shot_idx] = {"ok": False, "error": "prompt missing"}
            continue
        if not meta_path.exists():
            print(f"[batch-v365] shot{shot_idx:02d} meta 不存在 {meta_path}, 跳过",
                  flush=True)
            results[shot_idx] = {"ok": False, "error": "meta missing"}
            continue

        if skip_existing and out_path.exists() and out_path.stat().st_size > 100_000:
            print(f"[batch-v365] shot{shot_idx:02d} 已存在 "
                  f"{out_path.stat().st_size//1024} KB, 跳过", flush=True)
            results[shot_idx] = {"ok": True, "skipped": True, "path": str(out_path)}
            continue

        attempt = 0
        last_err = None
        ok_meta = None
        while attempt <= max_retries:
            try:
                t0 = time.time()
                seed_override = None
                if attempt > 0:
                    meta = json.loads(meta_path.read_text(encoding='utf-8-sig'))
                    seed_override = meta["seed"] + attempt * 9999
                    print(f"[batch-v365] shot{shot_idx:02d} 第 {attempt+1} 次尝试"
                          f"（换 seed={seed_override}）", flush=True)
                m = gen_one_v365(shot_idx, prompt_path, meta_path, out_path,
                                 seed_override=seed_override)
                dt = time.time() - t0
                if not m["yavg_check"]["ok"]:
                    raise RuntimeError(
                        f"YAVG check failed: {m['yavg_check']['note']}")
                results[shot_idx] = {"ok": True, "path": str(out_path),
                                     "elapsed_sec": round(dt, 1), "meta": m}
                print(f"[batch-v365] shot{shot_idx:02d} 完成 累计 "
                      f"{time.time()-t_total:.1f}s", flush=True)
                ok_meta = m
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[batch-v365] shot{shot_idx:02d} 第 {attempt+1} 次失败: {e}",
                      flush=True)
                attempt += 1
        if last_err is not None:
            results[shot_idx] = {"ok": False, "error": str(last_err),
                                 "attempts": attempt}

    summary = CLIPS_DIR / "seq_v365_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch-v365] 全部完成 {ok_cnt}/{len(results)} 段 总耗时 "
          f"{time.time()-t_total:.1f}s -> {summary}", flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=6)
    ap.add_argument("--skip-existing", action="store_true",
                    help="跳过已存在的 shot clip (默认 False: 重投生成)")
    ap.add_argument("--no-skip", action="store_true",
                    help="(v364 兼容别名) 显式不跳过")
    ap.add_argument("--retries", type=int, default=3,
                    help="单段最大重试次数（任务书硬性指令：最多 3 次）")
    args = ap.parse_args(argv)

    # --no-skip 也走 skip_existing=False
    skip_existing = bool(args.skip_existing) and not bool(args.no_skip)

    run_serial(args.start, args.end,
               skip_existing=skip_existing,
               max_retries=args.retries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
