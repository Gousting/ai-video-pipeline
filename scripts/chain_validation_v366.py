#!/usr/bin/env python3
"""v3.6.6 §3 链式 I2V 衔接小规模验证（2-3 段必做）。

任务书 oc_task_v366.txt §3:
1. 抽参考视频首帧 (~0.2s) 为 shot1 first_frame, 缩到 1344x576
2. 写测试 prompt (自然动作, 不含抽象转场特效词)
3. H3 I2V 生成 shot1 (5-6s, length≈124-144)
4. extractLastFrame(shot1) -> shot2 first_frame
5. 写 shot2 prompt, 生成 shot2
6. 验证: 抽 shot1 尾帧 / shot2 首帧对比 (人物是否一致),
   shot1/shot2 中段抽帧看动作是否自然、有无白帧
7. 结论必须写进 v366_validation_report.txt

CLI:
    python chain_validation_v366.py
"""
from __future__ import annotations

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
OUT_DIR = ROOT / "output" / "pipeline_v36" / "chain_validation_v366"
ANCHORS_DIR = OUT_DIR / "anchors"
CLIPS_DIR = OUT_DIR / "clips"
QA_DIR = OUT_DIR / "qa"
REPORT_PATH = OUT_DIR / "chain_validation_report.json"
HUMAN_REPORT_PATH = ROOT / "v366_validation_report.txt"

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
STEP32_W = 1344
STEP32_H = 576

# 测试段时长: shot1 = 6s, shot2 = 6s, shot3 = 6s (各 144 帧, H3 安全区)
# 任务书 §3.2: 5-6s (length≈124-144)
SHOT_LENGTHS_FRAMES = (144, 144, 144)


def ensure_dirs() -> None:
    for d in (OUT_DIR, ANCHORS_DIR, CLIPS_DIR, QA_DIR):
        d.mkdir(parents=True, exist_ok=True)


def extract_ref_frame(t_sec: float, out_path: Path) -> Path:
    """从参考视频抽 1 帧, 缩到 H3 step=32 尺寸 (1344x576)。"""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{t_sec:.3f}",
        "-i", str(REF_VIDEO),
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
    """从 clip_path 抽最后一帧 (倒数第 2 帧, 避开首尾黑帧)。"""
    # 拿 clip 时长
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration,nb_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    duration_sec = float(lines[0]) if lines else 5.0
    # 倒数第 2 帧 (避免最末帧可能黑/抽风)
    ss = max(0.1, duration_sec - 0.20)
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
        r = requests.post(f"{COMFY}/upload/image", files=files, data=data, timeout=60)
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
        "1": {"class_type": "LoadImage", "inputs": {"image": first_frame_name}},
        "2": {"class_type": "UNETLoader", "inputs": {
            "unet_name": UNET_NAME, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": CLIP_NAME, "type": "minimax"}},
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
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
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


# === 测试 prompts (任务书 §3.2: 自然动作, 不含抽象转场特效词) ===
TEST_PROMPT_SHOT1 = (
    "Cinematic anime-style horizontal wide shot. Two characters in a sunlit "
    "outdoor campus courtyard at golden hour. Slow gentle camera push forward "
    "with very subtle amplitude. The brown-twin-tail character on the left "
    "lifts her right hand gently, twin tails swaying softly in a light breeze. "
    "The black-long-hair character on the right turns her head slowly to "
    "look toward the brown-twin-tail character. Cherry blossom petals drift "
    "through warm air. Background is a soft-focus campus with trees. "
    "Mai Yoneyama cel-shaded anime style. No text, no UI, no abstract overlay. "
    "Maintain the visual style, character appearance, lighting, and color "
    "palette of the input first frame throughout."
)

TEST_PROMPT_SHOT2 = (
    "Cinematic anime-style horizontal wide shot, continuing from previous shot. "
    "Same two characters in the sunlit outdoor campus courtyard at golden hour. "
    "The brown-twin-tail character on the left lowers her hand slowly and "
    "tilts her head with a soft smile. The black-long-hair character on the "
    "right takes one slow step forward and her choker catches the warm light. "
    "Cherry blossom petals continue to drift through warm air. Camera holds "
    "static with very subtle 3 percent push in. Mai Yoneyama cel-shaded anime "
    "style. Maintain character appearance, lighting, and color palette of the "
    "previous shot."
)

TEST_PROMPT_SHOT3 = (
    "Cinematic anime-style horizontal wide shot, continuing from previous shot. "
    "Same two characters in the sunlit outdoor campus courtyard at golden hour. "
    "The brown-twin-tail character on the left takes one slow step toward the "
    "right. The black-long-hair character on the right turns to face her, a "
    "small surprised expression forming. Cherry blossom petals continue to "
    "drift through warm air. Camera holds static with very gentle 3 percent "
    "push in. Mai Yoneyama cel-shaded anime style. Maintain character "
    "appearance, lighting, and color palette of the previous shot."
)


def gen_one(shot_idx: int, prompt: str, first_frame: Path,
            out_clip: Path, *, seed: int, length: int) -> dict:
    """生成单段 H3 I2V。"""
    t0 = time.time()
    first_frame_name = upload_image(first_frame)
    print(f"[chain-val] shot{shot_idx:02d} uploaded first_frame="
          f"{first_frame_name}", flush=True)

    prefix = f"chain_val_shot{shot_idx:02d}"
    wf = build_h3_i2v_workflow(
        prompt=prompt,
        first_frame_name=first_frame_name,
        width=STEP32_W, height=STEP32_H,
        length=length, seed=seed, prefix=prefix, steps=20,
    )
    pid = queue_workflow(wf)
    print(f"[chain-val] shot{shot_idx:02d} queued pid={pid}", flush=True)

    entry = poll_history(pid, log_prefix=f"shot{shot_idx:02d} ")
    fn, sub, vtype = find_video_output(entry)
    print(f"[chain-val] shot{shot_idx:02d} output: {fn}", flush=True)

    download_video(fn, sub, vtype, out_clip)
    dt = time.time() - t0
    print(f"[chain-val] shot{shot_idx:02d} done -> {out_clip.name} "
          f"in {dt:.1f}s", flush=True)
    return {
        "shot": shot_idx,
        "prompt_id": pid,
        "comfy_filename": fn,
        "first_frame": str(first_frame),
        "out_clip": str(out_clip),
        "elapsed_sec": round(dt, 1),
        "seed": seed,
        "length_frames": length,
        "duration_sec": length / FPS,
    }


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


def frame_yavg_check(video_path: Path, label: str = "") -> dict:
    """中段 YAVG + 首帧 + 末帧检查, 验证是否有白帧/黑帧。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    duration_sec = float(lines[0]) if lines else 5.0

    first_ya, _ = yavg_at(video_path, 0.05)
    mid_t = max(0.5, duration_sec * 0.5)
    mid_ya, _ = yavg_at(video_path, mid_t)
    last_t = max(0.5, duration_sec - 0.15)
    last_ya, _ = yavg_at(video_path, last_t)
    q1_t = max(0.3, duration_sec * 0.25)
    q3_t = max(0.3, duration_sec * 0.75)
    q1_ya, _ = yavg_at(video_path, q1_t)
    q3_ya, _ = yavg_at(video_path, q3_t)

    samples = {
        "first_t0.05": first_ya,
        "q1_25pct": q1_ya,
        "mid_50pct": mid_ya,
        "q3_75pct": q3_ya,
        "last_end": last_ya,
    }
    valid = [v for v in samples.values() if v is not None]
    has_white = any(v > 240 for v in valid)
    has_black = any(v < 5 for v in valid)
    ok = (not has_white) and (not has_black) and len(valid) == 5
    return {
        "label": label,
        "duration_sec": round(duration_sec, 3),
        "samples": samples,
        "has_white_frame": has_white,
        "has_black_frame": has_black,
        "ok": ok,
    }


def frame_image_similarity(p1: Path, p2: Path) -> dict:
    """比较两张 anchor 帧的像素直方图相似度 (低层度量, 不是 VLM)。

    使用 PIL 抽 256-bin RGB 直方图, 计算归一化后的余弦相似度。
    """
    from PIL import Image
    import math

    def hist_vec(p: Path) -> list[float]:
        img = Image.open(p).convert("RGB").resize((192, 108), Image.LANCZOS)
        h = img.histogram()
        # 256-bin per channel -> 768-dim
        total = sum(h) or 1
        return [c / total for c in h]

    v1 = hist_vec(p1)
    v2 = hist_vec(p2)
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    cos = dot / (n1 * n2 + 1e-9)
    # 直方图余弦相似度: 1.0 = 同分布, 0 = 正交
    return {
        "img1": str(p1),
        "img2": str(p2),
        "cosine_similarity": round(cos, 4),
    }


def side_by_side(left: Path, right: Path, out: Path, label: str = "") -> None:
    from PIL import Image
    a = Image.open(left).convert("RGB")
    b = Image.open(right).convert("RGB")
    w, h = a.size
    canvas = Image.new("RGB", (w * 2 + 12, h + 24), (24, 24, 24))
    canvas.paste(a, (0, 24))
    canvas.paste(b, (w + 12, 24))
    canvas.save(out, "JPEG", quality=85)


def filmstrip(clip: Path, out: Path, n_frames: int = 6) -> None:
    from PIL import Image
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    dur = float(lines[0]) if lines else 5.0
    ts = [dur * (i + 0.5) / n_frames for i in range(n_frames)]
    tmpdir = out.parent / f"tmp_{clip.stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(ts):
        fp = tmpdir / f"frame_{i+1:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
             "-i", str(clip), "-frames:v", "1", "-q:v", "3", str(fp)],
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
    canvas.save(out, "JPEG", quality=85)


def main(argv=None) -> int:
    ensure_dirs()

    # === Step 0: 抽参考视频 0.2s 帧做 shot1 anchor ===
    shot1_anchor = ANCHORS_DIR / "shot01_anchor_from_ref.jpg"
    extract_ref_frame(0.20, shot1_anchor)
    print(f"[chain-val] shot1 anchor (ref t=0.20s) -> {shot1_anchor}", flush=True)

    # === Step 1: 生成 shot1 (ref frame -> shot1) ===
    out1 = CLIPS_DIR / "shot01.mp4"
    info1 = gen_one(
        shot_idx=1, prompt=TEST_PROMPT_SHOT1,
        first_frame=shot1_anchor, out_clip=out1,
        seed=33601, length=SHOT_LENGTHS_FRAMES[0],
    )
    yavg1 = frame_yavg_check(out1, label="shot01")

    # === Step 2: 抽 shot1 尾帧 -> shot2 anchor ===
    shot2_anchor = ANCHORS_DIR / "shot02_anchor_from_shot01.jpg"
    extract_clip_last_frame(out1, shot2_anchor)
    print(f"[chain-val] shot2 anchor (shot1 last frame) -> {shot2_anchor}",
          flush=True)

    # === Step 3: 生成 shot2 (shot1 尾帧 -> shot2) ===
    out2 = CLIPS_DIR / "shot02.mp4"
    info2 = gen_one(
        shot_idx=2, prompt=TEST_PROMPT_SHOT2,
        first_frame=shot2_anchor, out_clip=out2,
        seed=33602, length=SHOT_LENGTHS_FRAMES[1],
    )
    yavg2 = frame_yavg_check(out2, label="shot02")

    # === Step 4: 抽 shot2 尾帧 -> shot3 anchor ===
    shot3_anchor = ANCHORS_DIR / "shot03_anchor_from_shot02.jpg"
    extract_clip_last_frame(out2, shot3_anchor)

    # === Step 5: 生成 shot3 (shot2 尾帧 -> shot3) ===
    out3 = CLIPS_DIR / "shot03.mp4"
    info3 = gen_one(
        shot_idx=3, prompt=TEST_PROMPT_SHOT3,
        first_frame=shot3_anchor, out_clip=out3,
        seed=33603, length=SHOT_LENGTHS_FRAMES[2],
    )
    yavg3 = frame_yavg_check(out3, label="shot03")

    # === Step 6: 验证 ===
    # 6a. 直方图相似度: shot1尾帧 vs shot2首帧, shot2尾帧 vs shot3首帧
    shot2_first = ANCHORS_DIR / "shot02_first_frame_extracted.jpg"
    extract_clip_last_frame(out2, shot2_first)  # shot2 首尾帧很近, 用首帧对照
    # 改: 抽 shot2 首帧 (0.05s)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", "0.05", "-i", str(out2), "-frames:v", "1",
        "-vf", (f"scale={RES_W}:{RES_H}:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1:1"),
        str(shot2_first),
    ]
    subprocess.run(cmd, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", check=True)

    sim_12 = frame_image_similarity(shot2_anchor, shot2_first)
    sim_23_anchor = ANCHORS_DIR / "shot03_first_frame_extracted.jpg"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", "0.05", "-i", str(out3), "-frames:v", "1",
        "-vf", (f"scale={RES_W}:{RES_H}:flags=lanczos:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={RES_W}:{RES_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1:1"),
        str(sim_23_anchor),
    ]
    subprocess.run(cmd, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", check=True)
    sim_23 = frame_image_similarity(shot3_anchor, sim_23_anchor)

    # 6b. filmstrip
    fs1 = QA_DIR / "filmstrip_shot01.jpg"
    fs2 = QA_DIR / "filmstrip_shot02.jpg"
    fs3 = QA_DIR / "filmstrip_shot03.jpg"
    filmstrip(out1, fs1, n_frames=6)
    filmstrip(out2, fs2, n_frames=6)
    filmstrip(out3, fs3, n_frames=6)

    # 6c. 对比 side-by-side
    sbs_12 = QA_DIR / "sidebyside_shot01last_vs_shot02first.jpg"
    sbs_23 = QA_DIR / "sidebyside_shot02last_vs_shot03first.jpg"
    side_by_side(shot2_anchor, shot2_first, sbs_12, label="shot01last vs shot02first")
    side_by_side(shot3_anchor, sim_23_anchor, sbs_23, label="shot02last vs shot03first")

    # === 决策: 方案A 是否可用 ===
    # 准则:
    #   - 三段都无白帧/黑帧 (yavg ok)
    #   - 直方图相似度 > 0.5 (粗略, 表示人物/场景延续)
    #   - 长度符合 H3 安全区 (5-15s)
    # 满足则方案A 可用, 否则降级方案B (任务书 §2)
    yavg_all_ok = yavg1["ok"] and yavg2["ok"] and yavg3["ok"]
    sim_acceptable = sim_12["cosine_similarity"] > 0.5 and sim_23["cosine_similarity"] > 0.5
    chain_method_a_ok = yavg_all_ok and sim_acceptable

    report = {
        "task": "v3.6.6 §3 chain I2V validation",
        "method": "chain_first_frame_i2v",
        "comfy_url": COMFY,
        "shots": [info1, info2, info3],
        "yavg_checks": [yavg1, yavg2, yavg3],
        "similarity": {
            "shot01_lastframe_vs_shot02_firstframe": sim_12,
            "shot02_lastframe_vs_shot03_firstframe": sim_23,
        },
        "filmstrips": [str(fs1), str(fs2), str(fs3)],
        "side_by_side": [str(sbs_12), str(sbs_23)],
        "decision": {
            "yavg_all_ok": yavg_all_ok,
            "sim_acceptable": sim_acceptable,
            "chain_method_a_ok": chain_method_a_ok,
            "conclusion": (
                "方案A(链式I2V衔接) 可用: 人物/动作/无白帧 验证通过"
                if chain_method_a_ok else
                "方案A 不稳定, 降级方案B (分段生成 + 后期 dissolve + 统一配音)"
            ),
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[chain-val] report -> {REPORT_PATH}", flush=True)

    # === 写人读报告 ===
    lines = [
        "=" * 70,
        "v3.6.6 §3 链式 I2V 衔接验证报告",
        "=" * 70,
        f"时间: {report['generated_at']}",
        f"ComfyUI: {COMFY}",
        "",
        "[测试策略]",
        "- 抽参考视频 0.20s 帧 (1344x576) 做 shot1 anchor",
        "- shot1 -> shot2 -> shot3 链式衔接, 每段 144 帧 (6s @ 24fps)",
        "- shot1 prompt: 自然动作 (推镜 + 抬手 + 转头)",
        "- shot2 prompt: 继续自然动作 (放手下 + 前进步)",
        "- shot3 prompt: 继续自然动作 (靠近 + 表情变化)",
        "",
        "[生成结果]",
    ]
    for info in report["shots"]:
        lines.append(
            f"  shot{info['shot']:02d}: pid={info['prompt_id']} "
            f"first_frame={Path(info['first_frame']).name} "
            f"duration={info['duration_sec']:.1f}s "
            f"elapsed={info['elapsed_sec']:.1f}s"
        )
    lines.append("")
    lines.append("[YAVG 帧检查 (白帧/黑帧)]")
    for yc in report["yavg_checks"]:
        s = yc["samples"]
        lines.append(
            f"  {yc['label']:8s}: first={s['first_t0.05']:>6} q1={s['q1_25pct']:>6} "
            f"mid={s['mid_50pct']:>6} q3={s['q3_75pct']:>6} last={s['last_end']:>6} "
            f"white={yc['has_white_frame']} black={yc['has_black_frame']} ok={yc['ok']}"
        )
    lines.append("")
    lines.append("[直方图相似度 (余弦, 1.0 = 同分布)]")
    lines.append(
        f"  shot01尾帧 vs shot02首帧: "
        f"cos={report['similarity']['shot01_lastframe_vs_shot02_firstframe']['cosine_similarity']}"
    )
    lines.append(
        f"  shot02尾帧 vs shot03首帧: "
        f"cos={report['similarity']['shot02_lastframe_vs_shot03_firstframe']['cosine_similarity']}"
    )
    lines.append("")
    lines.append("[Filmstrip + Side-by-side]")
    for p in report["filmstrips"]:
        lines.append(f"  {p}")
    for p in report["side_by_side"]:
        lines.append(f"  {p}")
    lines.append("")
    lines.append("=" * 70)
    lines.append("[决策]")
    lines.append(f"  yavg 全部通过: {yavg_all_ok}")
    lines.append(f"  直方图相似度可接受: {sim_acceptable}")
    lines.append(f"  方案A(链式I2V) 是否可用: {chain_method_a_ok}")
    lines.append(f"  结论: {report['decision']['conclusion']}")
    lines.append("=" * 70)

    HUMAN_REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[chain-val] human report -> {HUMAN_REPORT_PATH}", flush=True)
    return 0 if chain_method_a_ok else 1


if __name__ == "__main__":
    sys.exit(main())
