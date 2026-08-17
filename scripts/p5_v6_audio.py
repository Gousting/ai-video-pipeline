#!/usr/bin/env python3
"""P5-v6 音频环节（衔接 Phase C overlay 烧录，产出真正完整成片）：

差异（相对 p5_v5_audio.py）：
  - VIDEO_SILENT：video_silent_v5.mp4 → video_with_overlay.mp4（Phase C 产物，带片头/字幕/片尾）
  - NARRATION / AMBIENT / FINAL / META 全部按 v6 命名（与 p5_v5_audio.py 的 v5 命名区分）
  - 其余混音链路（narration.adelay=10000ms / piano_start=duration-piano_dur /
    底噪 amix / alimiter 防爆音）保持原样：video_with_overlay.mp4 的音频 = 主视频音频，
    -map 3:v copy 视频流，-map [aout] 替换音频；逻辑与 v5 完全等价，零漂移衔接。

流程：
  1. numpy 合成钢琴收束音（4s，基频+泛音衰减正弦波，简单和弦，结尾渐弱 fade out）
  2. edge-tts 生成 shot3 配音「雨还在下，他走得不快。」
  3. ffmpeg 合成环境底噪（brown noise 低音量，fade in/out）
  4. 混音：narration(adelay 到 shot3 起始) + 钢琴(adelay 到结尾) + 底噪 amix，alimiter 防爆音
  5. 视频流 copy 自 video_with_overlay.mp4 -> final_v6.mp4
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\ai-video-pipeline")
TMP = ROOT / "output" / "tmp"
OUT = ROOT / "output" / "out"

VIDEO_SILENT = OUT / "video_with_overlay.mp4"
PIANO = TMP / "piano_ending_v6.wav"
NARRATION = TMP / "narration_v6.mp3"
AMBIENT = TMP / "ambient_v6.wav"
FINAL = OUT / "final_v6.mp4"
META = TMP / "p5v6_audio_meta.json"

VOICE = "zh-CN-XiaoxiaoNeural"
NARRATION_TEXT = "雨还在下，他走得不快。"
SR = 32000
PIANO_DUR = 4.0          # 钢琴收束 4s（任务书 3-5s）


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
    """合成钢琴收束音：C4/E4/G4/C5 简单和弦，基频+泛音指数衰减，结尾渐弱。"""
    t = np.arange(int(sr * duration)) / sr
    notes = [(261.63, 1.0), (329.63, 0.8), (392.00, 0.7), (523.25, 0.4)]
    sig = np.zeros_like(t)
    rng = np.random.default_rng(42)
    for f0, amp in notes:
        for h in range(1, 6):
            fh = f0 * h * (1 + 0.0006 * h * h)  # 轻微非谐（钢琴特性）
            tau = 2.0 / (h ** 0.7)               # 高次泛音衰减更快
            env = (amp / (h ** 0.9)) * np.exp(-t / tau)
            attack = int(0.008 * sr)
            env[:attack] *= np.linspace(0, 1, attack)
            phase = 2 * np.pi * fh * t + rng.uniform(0, 2 * np.pi)
            sig += env * np.sin(phase)
    sig = sig / (np.max(np.abs(sig)) + 1e-9)
    # 结尾渐弱 fade out（最后 1.4s 线性归零，保证无爆音/无 click）
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


def gen_narration() -> float:
    run(["edge-tts", "--voice", VOICE, "--text", NARRATION_TEXT, "--write-media", str(NARRATION)])
    return ffprobe_duration(NARRATION)


def gen_ambient(duration: float) -> None:
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={duration}:amplitude=0.5:seed=42",
        "-af", "lowpass=f=300,volume=0.12,"
               "afade=t=in:st=0:d=2.0,"
               f"afade=t=out:st={duration - 2.5:.3f}:d=2.5",
        "-ar", str(SR), "-ac", "2",
        str(AMBIENT),
    ])


def mix(duration: float, shot3_start: float, piano_start: float) -> None:
    nar_delay = int(shot3_start * 1000)
    pia_delay = int(piano_start * 1000)
    fc = (
        f"[0:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
        f"adelay={nar_delay}|{nar_delay},apad=whole_dur={duration}[nar];"
        f"[1:a]aformat=sample_rates={SR}:channel_layouts=stereo,volume=0.5,"
        f"adelay={pia_delay}|{pia_delay},apad=whole_dur={duration}[pia];"
        f"[2:a]atrim=0:{duration},asetpts=PTS-STARTPTS[amb];"
        f"[nar][pia][amb]amix=inputs=3:normalize=0:duration=longest,"
        f"alimiter=limit=0.95[aout]"
    )
    run([
        "ffmpeg", "-y",
        "-i", str(NARRATION),
        "-i", str(PIANO),
        "-i", str(AMBIENT),
        "-i", str(VIDEO_SILENT),
        "-filter_complex", fc,
        "-map", "3:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SR),
        "-shortest",
        str(FINAL),
    ])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(VIDEO_SILENT)
    print(f"[audio] video_with_overlay 时长 {duration:.3f}s", flush=True)

    synth_piano(PIANO)
    piano_dur = ffprobe_duration(PIANO)
    print(f"[audio] 钢琴收束音 -> {PIANO} ({piano_dur:.3f}s)", flush=True)

    narration_dur = gen_narration()
    print(f"[audio] narration -> {NARRATION} ({narration_dur:.3f}s)", flush=True)

    gen_ambient(duration)

    # shot3 起始 ≈ 前两段时长之和减去一次 0.25s acrossfade；钢琴结尾贴齐片尾
    shot3_start = 10.0
    if duration > 12:
        shot3_start = round(duration * 2 / 3, 2)
    piano_start = round(duration - piano_dur, 2)
    print(f"[audio] shot3_start={shot3_start}s piano_start={piano_start}s", flush=True)

    mix(duration, shot3_start, piano_start)
    print(f"[audio] final_v6 -> {FINAL}", flush=True)

    meta = {
        "compose_phase": "D",
        "audio_phase": "p5_v6",
        "input_video": str(VIDEO_SILENT),
        "video_silent_duration": duration,
        "narration_duration": narration_dur,
        "piano_duration": piano_dur,
        "voice": VOICE,
        "narration_text": NARRATION_TEXT,
        "shot3_start": shot3_start,
        "piano_start": piano_start,
        "piano_chord": "C4/E4/G4/C5, 5 harmonics exponential decay, 1.4s fade-out",
        "ambient": "brown noise lowpass 300Hz volume 0.12, fade in 2s / out 2.5s",
        "limiter": "alimiter limit=0.95 (防爆音)",
        "output": str(FINAL),
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
