#!/usr/bin/env python3
"""v3.0 音频环节：edge-tts 配音 + BGM + 混音 → final_v3.mp4。

基于 p5_v6_audio.py 重构：
  - VIDEO_SILENT：v3 路径 video_with_overlay.mp4（已带 overlay 的成片）
  - 配音：edge-tts 从 storyboard.shots[].narration 生成（v3 prompt-pack 集成）
  - BGM：沿用 p5_v6 的钢琴收束音 + 校园环境底噪
  - 混音：ffmpeg acrossfade + alimiter 防爆音
  - 输出：final_v3.mp4（与 v3 pipeline.yaml stage 6 artifact 一致）

CLI:
  python v3_audio.py --video <video_with_overlay.mp4> --storyboard <sb.json> --out <final.mp4>
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

VOICE = "zh-CN-XiaoxiaoNeural"   # 默认中文女声
SR = 32000                       # 采样率
PIANO_DUR = 4.0                  # 钢琴收束 4s


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise RuntimeError(f"命令失败 rc={r.returncode}")
    return r


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def synth_piano(path: Path, duration: float = PIANO_DUR, sr: int = SR) -> None:
    """合成钢琴收束音（C4/E4/G4/C5 和弦 + 5 谐波指数衰减 + 1.4s fade out）。

    与 p5_v6_audio.py.synth_piano 逻辑一致，但命名改为 v3_ 前缀。
    """
    t = np.arange(int(sr * duration)) / sr
    notes = [(261.63, 1.0), (329.63, 0.8), (392.00, 0.7), (523.25, 0.4)]
    sig = np.zeros_like(t)
    rng = np.random.default_rng(42)
    for f0, amp in notes:
        for h in range(1, 6):
            fh = f0 * h * (1 + 0.0006 * h * h)
            tau = 2.0 / (h ** 0.7)
            env = (amp / (h ** 0.9)) * np.exp(-t / tau)
            attack = int(0.008 * sr)
            env[:attack] *= np.linspace(0, 1, attack)
            phase = 2 * np.pi * fh * t + rng.uniform(0, 2 * np.pi)
            sig += env * np.sin(phase)
    sig = sig / (np.max(np.abs(sig)) + 1e-9)
    fade = int(1.4 * sr)
    sig[-fade:] *= np.linspace(1, 0, fade)
    sig[:int(0.02 * sr)] *= np.linspace(0, 1, int(0.02 * sr))
    sig *= 0.8
    stereo = np.stack([sig, sig], axis=1)
    pcm = (stereo * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def gen_narration(text: str, out_path: Path) -> float:
    """edge-tts 生成单段配音，返回时长。"""
    run(["edge-tts", "--voice", VOICE, "--text", text, "--write-media", str(out_path)])
    return ffprobe_duration(out_path)


def gen_ambient(duration: float, out_path: Path) -> None:
    """棕色噪声低音量，fade in 2s / fade out 2.5s。"""
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={duration}:amplitude=0.5:seed=42",
        "-af", "lowpass=f=300,volume=0.12,"
               "afade=t=in:st=0:d=2.0,"
               f"afade=t=out:st={duration - 2.5:.3f}:d=2.5",
        "-ar", str(SR), "-ac", "2",
        str(out_path),
    ])


def mix_all(duration: float, narration_files: list[tuple[int, Path]],
            piano_path: Path, ambient_path: Path,
            video_path: Path, out_path: Path) -> None:
    """把 N 段配音 + 钢琴收束音 + 底噪 + 视频流混音成 final_v3.mp4。

    narration_files: [(adelay_ms, file_path), ...]
        adelay_ms = 该段配音在 final_v3 时间线上的起始毫秒
    """
    # 构建 filter_complex
    parts = []
    # 1. 每个 narration adelay 到对应位置
    input_indices = []
    for i, (adelay_ms, fp) in enumerate(narration_files):
        idx = i
        delay = max(0, int(adelay_ms))
        parts.append(
            f"[{idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
            f"adelay={delay}|{delay},apad=whole_dur={duration}[nar{i}]"
        )
        input_indices.append(idx)

    # 2. 钢琴：延迟到末尾前 PIANO_DUR 秒
    piano_idx = len(narration_files)
    piano_delay = max(0, int((duration - PIANO_DUR) * 1000))
    parts.append(
        f"[{piano_idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,volume=0.5,"
        f"adelay={piano_delay}|{piano_delay},apad=whole_dur={duration}[pia]"
    )

    # 3. 底噪：全长 trim
    ambient_idx = piano_idx + 1
    parts.append(
        f"[{ambient_idx}:a]atrim=0:{duration},asetpts=PTS-STARTPTS[amb]"
    )

    # 4. amix 全部
    n_inputs = len(narration_files) + 2  # 配音 + 钢琴 + 底噪
    amix_inputs = "".join(f"[nar{i}]" for i in range(len(narration_files))) + "[pia][amb]"
    parts.append(
        f"{amix_inputs}amix=inputs={n_inputs}:normalize=0:duration=longest,"
        f"alimiter=limit=0.95[aout]"
    )
    filter_complex = ";\n".join(parts)

    # 5. 视频流 = 输入视频
    video_idx = ambient_idx + 1

    cmd = ["ffmpeg", "-y"]
    for _, fp in narration_files:
        cmd.extend(["-i", str(fp)])
    cmd.extend(["-i", str(piano_path)])
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


def build_narration_timeline(storyboard: dict, video_duration: float) -> list[tuple[int, Path]]:
    """从 storyboard.shots[].narration 推导每段配音在 final_v3 时间线上的起始毫秒。

    每段 8s - 0.25s acrossfade = 7.75s 实际时长（与 ab_concat.py 一致）。
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
    ap.add_argument("--video", default=str(DEFAULT_VIDEO),
                    help="带 overlay 的拼接视频（v3 cliper 输出）")
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

    # 1. 钢琴收束音
    piano_path = tmp_dir / "v3_piano_ending.wav"
    synth_piano(piano_path)
    piano_dur = ffprobe_duration(piano_path)
    print(f"[audio] 钢琴收束音 -> {piano_path} ({piano_dur:.3f}s)", flush=True)

    # 2. 配音（按 storyboard.shots 顺序逐段生成，adelay 推导自段间 fade）
    timeline = build_narration_timeline(storyboard, duration)
    print(f"[audio] 共 {len(timeline)} 段配音", flush=True)
    narration_files = []
    for adelay_ms, idx, text in timeline:
        np = tmp_dir / f"v3_narration_shot{idx:02d}.mp3"
        d = gen_narration(text, np)
        print(f"[audio] shot{idx:02d} 配音 ({d:.2f}s, adelay={adelay_ms}ms) "
              f"text='{text[:30]}...'", flush=True)
        narration_files.append((adelay_ms, np))
    # 复制到 audio_dir（持久化）
    for adelay_ms, np in narration_files:
        target = audio_dir / np.name
        if not target.exists():
            target.write_bytes(np.read_bytes())

    # 3. 环境底噪
    ambient_path = tmp_dir / "v3_ambient.wav"
    gen_ambient(duration, ambient_path)
    print(f"[audio] 环境底噪 -> {ambient_path}", flush=True)

    # 4. 混音
    mix_all(duration, narration_files, piano_path, ambient_path, video_path, out_path)
    print(f"[audio] final -> {out_path}", flush=True)

    # 5. 元信息
    meta = {
        "compose_phase": "D",
        "audio_phase": "v3",
        "pipeline_version": "3.0",
        "input_video": str(video_path),
        "video_silent_duration": duration,
        "n_narrations": len(narration_files),
        "voice": VOICE,
        "piano_duration": piano_dur,
        "target_lufs": -16,
        "output": str(out_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (tmp_dir / "v3_audio_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())