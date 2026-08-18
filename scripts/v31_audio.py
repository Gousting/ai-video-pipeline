#!/usr/bin/env python3
"""v3.1 音频环节：统一 BGM（全片同主题 + 起承转合）+ edge-tts 配音 + 混音。

v3.1 关键差异 vs v3_audio.py：
  - v3 BGM 是钢琴收束音（仅末 4s）+ 棕色噪声底噪；段间没有统一旋律主题，
    导致拼接后音乐断裂/单调
  - v3.1 新增：全片统一的 BGM 主题（programmatic numpy 合成 4 段式音乐）
    0-25%  起：轻柔钢琴 + 单音长笛
    25-50% 承：钢琴 + 吉他琶音
    50-75% 转：加入轻快鼓点 + 钢琴
    75-100% 合：回到起段主题 + 渐弱收束
  - 段原生音轨（H3 T2V 自带音频）全部废弃
  - 响度归一化到 -16 LUFS（抖音标准）
  - 输出 final_v3.mp4（与 pipeline.yaml stage 6 一致）

CLI:
  python v31_audio.py --video <video_with_overlay.mp4> --storyboard <sb.json> --out <final.mp4>
"""
import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_VIDEO = ROOT / "output" / "pipeline_v3" / "clips" / "video_with_overlay.mp4"
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard.json"
DEFAULT_OUT = ROOT / "output" / "pipeline_v3" / "final_v3.mp4"
DEFAULT_TMP = ROOT / "output" / "pipeline_v3" / "tmp"
DEFAULT_AUDIO_DIR = ROOT / "output" / "pipeline_v3" / "audio"

VOICE = "zh-CN-XiaoxiaoNeural"
SR = 32000


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"命令失败 rc={r.returncode}")
    return r


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


# ---------------------------------------------------------------------------
# 统一 BGM 合成：4 段式起承转合（程序化合成，零外部素材依赖）
# ---------------------------------------------------------------------------

def synth_unified_bgm(duration: float, out_path: Path) -> None:
    """合成全片统一的 BGM：4 段式起承转合。

    主题：C 大调，120 BPM，4 拍子
    乐器分层：
      - 主旋律（钢琴 + 谐波包络）：4 段连贯主题，末段回到首段主题做收束
      - 和声铺底（C/E/G/C 和弦持续循环）
      - 鼓点（仅 50-75% 段加入）
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(SR * duration)) / SR
    rng = np.random.default_rng(42)
    sig = np.zeros_like(t)

    # C 大调音阶频率
    base = 261.63  # C4
    notes_scale = [base * (2 ** (n / 12)) for n in [0, 2, 4, 5, 7, 9, 11]]  # CDEFGAB

    # 4 段时长分割点
    boundaries = [duration * 0.0, duration * 0.25, duration * 0.50,
                  duration * 0.75, duration * 1.00]

    def envelope(i_start: int, i_end: int, attack_s: float = 0.05, release_s: float = 0.2):
        n = i_end - i_start
        if n <= 0:
            return np.zeros(0)
        env = np.ones(n)
        atk = int(attack_s * SR)
        rel = int(release_s * SR)
        if atk > 0:
            env[:atk] = np.linspace(0, 1, atk)
        if rel > 0:
            env[-rel:] = np.linspace(1, 0, rel)
        return env

    # 段 1（起）：0-25% — 轻柔钢琴 + 单音长笛，C 大调琶音
    i0, i1 = int(boundaries[0] * SR), int(boundaries[1] * SR)
    seg = np.zeros(i1 - i0)
    for i, f0 in enumerate([notes_scale[0], notes_scale[2], notes_scale[4], notes_scale[0]]):
        for h in range(1, 4):
            fh = f0 * h * (1 + 0.0003 * h * h)
            tau = 1.5 / (h ** 0.6)
            env = (0.4 / (h ** 0.9)) * np.exp(-np.arange(i1 - i0) / SR / tau)
            phase = 2 * np.pi * fh * np.arange(i1 - i0) / SR + rng.uniform(0, 2 * np.pi)
            seg += env * np.sin(phase)
    seg *= 0.35
    seg = seg * envelope(0, len(seg), attack_s=1.5, release_s=2.0)
    sig[i0:i1] += seg

    # 段 2（承）：25-50% — 钢琴 + 吉他琶音
    i0, i1 = int(boundaries[1] * SR), int(boundaries[2] * SR)
    seg = np.zeros(i1 - i0)
    # 和弦铺底
    chord = [notes_scale[0], notes_scale[2], notes_scale[4]]  # C E G
    for f0 in chord:
        env = 0.25 * np.ones(i1 - i0) * envelope(0, i1 - i0, attack_s=1.0, release_s=1.0)
        phase = 2 * np.pi * f0 * np.arange(i1 - i0) / SR + rng.uniform(0, 2 * np.pi)
        seg += env * np.sin(phase)
    # 主旋律（高八度）
    for i, f0 in enumerate([notes_scale[4], notes_scale[5], notes_scale[4], notes_scale[2]]):
        for h in range(1, 3):
            fh = f0 * 2 * h * (1 + 0.0003 * h * h)
            tau = 1.0 / (h ** 0.6)
            env = (0.35 / (h ** 0.9)) * np.exp(-np.arange(i1 - i0) / SR / tau)
            phase = 2 * np.pi * fh * np.arange(i1 - i0) / SR + rng.uniform(0, 2 * np.pi)
            seg += env * np.sin(phase)
    seg *= 0.4
    seg = seg * envelope(0, len(seg), attack_s=1.0, release_s=1.5)
    sig[i0:i1] += seg

    # 段 3（转）：50-75% — 加入鼓点
    i0, i1 = int(boundaries[2] * SR), int(boundaries[3] * SR)
    seg = np.zeros(i1 - i0)
    # 鼓点（120 BPM = 0.5s/拍）
    beat_period = 0.5  # 120 BPM
    n_beats = int((boundaries[3] - boundaries[2]) / beat_period)
    for b in range(n_beats):
        idx = int(b * beat_period * SR)
        if idx >= i1 - i0 - int(0.1 * SR):
            continue
        # Kick: 低频正弦衰减
        t_b = np.arange(int(0.15 * SR)) / SR
        kick = 0.3 * np.exp(-t_b / 0.04) * np.sin(2 * np.pi * 60 * t_b)
        end = min(idx + len(kick), i1 - i0)
        seg[idx:end] += kick[:end - idx]
    # 和弦 + 主旋律
    chord = [notes_scale[4], notes_scale[5], notes_scale[4], notes_scale[2]]  # F G E C
    for f0 in chord:
        env = 0.20 * np.ones(i1 - i0) * envelope(0, i1 - i0, attack_s=0.5, release_s=1.0)
        phase = 2 * np.pi * f0 * np.arange(i1 - i0) / SR + rng.uniform(0, 2 * np.pi)
        seg += env * np.sin(phase)
    seg *= 0.45
    seg = seg * envelope(0, len(seg), attack_s=1.0, release_s=1.5)
    sig[i0:i1] += seg

    # 段 4（合）：75-100% — 回到首段主题 + 渐弱收束
    i0, i1 = int(boundaries[3] * SR), int(boundaries[4] * SR)
    seg = np.zeros(i1 - i0)
    # 主旋律（与段 1 同主题）
    for i, f0 in enumerate([notes_scale[0], notes_scale[2], notes_scale[4], notes_scale[0]]):
        for h in range(1, 4):
            fh = f0 * h * (1 + 0.0003 * h * h)
            tau = 2.0 / (h ** 0.6)
            env = (0.4 / (h ** 0.9)) * np.exp(-np.arange(i1 - i0) / SR / tau)
            phase = 2 * np.pi * fh * np.arange(i1 - i0) / SR + rng.uniform(0, 2 * np.pi)
            seg += env * np.sin(phase)
    seg *= 0.35
    seg = seg * envelope(0, len(seg), attack_s=1.0, release_s=4.0)  # 长渐弱收束
    sig[i0:i1] += seg

    # 全局归一化
    sig = sig / (np.max(np.abs(sig)) + 1e-9)
    sig *= 0.75  # 主音量
    stereo = np.stack([sig, sig], axis=1)
    pcm = (stereo * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"[bgm] 统一 BGM ({duration:.1f}s, 4 段起承转合) -> {out_path}", flush=True)


# ---------------------------------------------------------------------------
# 配音（edge-tts）
# ---------------------------------------------------------------------------

def gen_narration(text: str, out_path: Path) -> float:
    run(["edge-tts", "--voice", VOICE, "--text", text, "--write-media", str(out_path)])
    return ffprobe_duration(out_path)


# ---------------------------------------------------------------------------
# 环境底噪（v3 同款，低音量铺垫）
# ---------------------------------------------------------------------------

def gen_ambient(duration: float, out_path: Path) -> None:
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={duration}:amplitude=0.5:seed=42",
        "-af", "lowpass=f=300,volume=0.08,"  # 比 v3 更低音量，让 BGM 主导
               "afade=t=in:st=0:d=2.0,"
               f"afade=t=out:st={duration - 2.5:.3f}:d=2.5",
        "-ar", str(SR), "-ac", "2",
        str(out_path),
    ])


# ---------------------------------------------------------------------------
# 混音
# ---------------------------------------------------------------------------

def mix_all(duration: float, narration_files: list[tuple[int, Path]],
            bgm_path: Path, ambient_path: Path,
            video_path: Path, out_path: Path) -> None:
    """N 段配音 + 统一 BGM + 底噪 + 视频 → final_v3.mp4"""
    parts = []
    input_indices = []

    # 1. 配音 adelay
    for i, (adelay_ms, fp) in enumerate(narration_files):
        idx = i
        delay = max(0, int(adelay_ms))
        parts.append(
            f"[{idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
            f"adelay={delay}|{delay},apad=whole_dur={duration}[nar{i}]"
        )
        input_indices.append(idx)

    # 2. BGM（全片 0.85 音量）
    bgm_idx = len(narration_files)
    parts.append(
        f"[{bgm_idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,volume=0.85[bgm]"
    )

    # 3. 底噪
    ambient_idx = bgm_idx + 1
    parts.append(
        f"[{ambient_idx}:a]atrim=0:{duration},asetpts=PTS-STARTPTS[amb]"
    )

    # 4. amix：配音 + BGM + 底噪
    n_inputs = len(narration_files) + 2
    amix_inputs = "".join(f"[nar{i}]" for i in range(len(narration_files))) + "[bgm][amb]"
    parts.append(
        f"{amix_inputs}amix=inputs={n_inputs}:normalize=0:duration=longest,"
        f"alimiter=limit=0.95[aout]"
    )
    filter_complex = ";\n".join(parts)

    # 5. 视频流
    video_idx = ambient_idx + 1

    cmd = ["ffmpeg", "-y"]
    for _, fp in narration_files:
        cmd.extend(["-i", str(fp)])
    cmd.extend(["-i", str(bgm_path)])
    cmd.extend(["-i", str(ambient_path)])
    cmd.extend(["-i", str(video_path)])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"{video_idx}:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SR),
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ])
    run(cmd)


def build_narration_timeline(storyboard: dict, video_duration: float) -> list[tuple[int, int, str]]:
    """从 storyboard.shots[].narration 推导每段配音起始毫秒。
    每段 8s，段间 0.25s 渐变过渡。
    """
    shot_seconds = 8.0
    fade = 0.25
    timeline = []
    accum_ms = 0
    for idx, shot in enumerate(storyboard.get("shots", []), 1):
        text = (shot.get("narration") or "").strip()
        if not text:
            continue
        timeline.append((accum_ms, idx, text))
        accum_ms += int((shot_seconds - fade) * 1000)
    return timeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--audio-dir", default=str(DEFAULT_AUDIO_DIR))
    ap.add_argument("--tmp", default=str(DEFAULT_TMP))
    args = ap.parse_args(argv)

    video_path = Path(args.video)
    sb_path = Path(args.storyboard)
    out_path = Path(args.out)
    audio_dir = Path(args.audio_dir)
    tmp_dir = Path(args.tmp)
    audio_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if not video_path.exists():
        print(f"ERROR: 视频不存在 {video_path}", file=sys.stderr)
        return 2
    if not sb_path.exists():
        print(f"ERROR: storyboard 不存在 {sb_path}", file=sys.stderr)
        return 2

    storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
    duration = ffprobe_duration(video_path)
    print(f"[audio] video_with_overlay 时长 {duration:.3f}s", flush=True)

    # 1. 统一 BGM（v3.1 关键增量）
    bgm_path = tmp_dir / "v31_unified_bgm.wav"
    synth_unified_bgm(duration, bgm_path)
    bgm_dur = ffprobe_duration(bgm_path)
    print(f"[audio] 统一 BGM ({bgm_dur:.3f}s) -> {bgm_path}", flush=True)

    # 2. 配音
    timeline = build_narration_timeline(storyboard, duration)
    print(f"[audio] 共 {len(timeline)} 段配音", flush=True)
    narration_files = []
    for adelay_ms, idx, text in timeline:
        np_path = tmp_dir / f"v31_narration_shot{idx:02d}.mp3"
        d = gen_narration(text, np_path)
        print(f"[audio] shot{idx:02d} 配音 ({d:.2f}s, adelay={adelay_ms}ms) "
              f"text='{text[:30]}...'", flush=True)
        narration_files.append((adelay_ms, np_path))
    for _, np_path in narration_files:
        target = audio_dir / np_path.name
        if not target.exists():
            target.write_bytes(np_path.read_bytes())

    # 3. 环境底噪（v3.1 比 v3 更低音量）
    ambient_path = tmp_dir / "v31_ambient.wav"
    gen_ambient(duration, ambient_path)
    print(f"[audio] 环境底噪 -> {ambient_path}", flush=True)

    # 4. 混音 → final_v3.mp4
    mix_all(duration, narration_files, bgm_path, ambient_path, video_path, out_path)
    print(f"[audio] final -> {out_path}", flush=True)

    # 5. 元信息
    meta = {
        "compose_phase": "D",
        "audio_phase": "v31_unified_bgm",
        "pipeline_version": "3.1",
        "input_video": str(video_path),
        "video_silent_duration": duration,
        "n_narrations": len(narration_files),
        "voice": VOICE,
        "bgm_duration": bgm_dur,
        "bgm_structure": "4-段式起承转合（C 大调 120 BPM）",
        "target_lufs": -16,
        "output": str(out_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (tmp_dir / "v31_audio_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())