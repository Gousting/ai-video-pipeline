#!/usr/bin/env python3
"""same_v1 专用 audio 配音 + BGM 混音。

流程：
  1. edge-tts 4 段配音（钩子/学姐介绍/学妹介绍/CTA）
  2. numpy 合成钢琴 BGM（C4/E4/G4/C5 和弦，泛音衰减，结尾渐弱）
  3. ffmpeg ambient 底噪（brown noise 低音量）
  4. 混音：narration (adelay 到对应 shot 起点) + piano + ambient → alimiter 防爆音
  5. 视频流 copy 自 video_with_overlay.mp4 → final_same_v1.mp4

配音时间码对齐 storyboard：
  - shot 1 (title, 0-2s)      → 「学姐还是学妹？」
  - shot 9 (senior info, 27-29s) → 「暗黑偶像 · 骷髅朋克 · 学姐」
  - shot 17 (junior info, 52-54s) → 「元气学园 · 萌熊校园 · 学妹」
  - shot 18 (CTA, 54-59.5s)    → 「你选学姐还是学妹？评论区告诉我。」

CLI:
    python scripts/same_v1_audio.py --video D:/ai-video-pipeline/output/same_v1/out/video_with_overlay.mp4 --out D:/ai-video-pipeline/output/same_v1/final_same_v1.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\ai-video-pipeline")
SAME_DIR = ROOT / "output" / "same_v1"
TMP = SAME_DIR / "tmp"
OUT_FINAL = SAME_DIR / "final_same_v1.mp4"

SR = 32000
VOICE_SENIOR = "zh-CN-XiaoxiaoNeural"   # 学姐（成熟磁性）
VOICE_JUNIOR = "zh-CN-XiaoyiNeural"     # 学妹（年轻可爱）

# 配音台词（与 storyboard shot 1/9/17/18 对齐）
NARRATIONS = [
    # (text, start_sec, voice)
    ("学姐还是学妹？", 0.5, VOICE_SENIOR),               # shot 1 hook
    ("暗黑偶像，骷髅朋克。", 27.0, VOICE_SENIOR),          # shot 9
    ("元气学园，萌熊校园。", 51.5, VOICE_JUNIOR),          # shot 17
    ("你选学姐还是学妹？评论区告诉我。", 54.5, VOICE_JUNIOR),  # shot 18
]

PIANO_DUR = 6.0


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd[:5]), "...", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print("stderr:", proc.stderr[-2000:], flush=True)
        raise RuntimeError(f"命令失败 rc={proc.returncode}")
    return proc


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def synth_piano(path: Path, duration: float = PIANO_DUR, sr: int = SR) -> None:
    """合成钢琴和弦 C4/E4/G4/C5，泛音衰减 + 1.4s fade out。"""
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
    sig *= 0.7
    stereo = np.stack([sig, sig], axis=1)
    pcm = (stereo * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def gen_narration_files(out_dir: Path) -> list[Path]:
    """用 edge-tts 生成 4 段配音 mp3，返回路径列表（与 NARRATIONS 顺序对齐）。"""
    paths = []
    for i, (text, start, voice) in enumerate(NARRATIONS):
        p = out_dir / f"narration_{i:02d}.mp3"
        run(["edge-tts", "--voice", voice, "--text", text,
             "--write-media", str(p)])
        paths.append(p)
        print(f"  narration_{i:02d}: '{text[:30]}...' voice={voice} dur={ffprobe_duration(p):.2f}s",
              flush=True)
    return paths


def concat_narrations(narr_files: list[Path], output: Path) -> Path:
    """按 NARRATIONS 时码把 4 段配音拼成一条总音频轨（含静音段）。"""
    # 用 ffmpeg concat demuxer + adelay
    # 先生成 4 段带 adelay 的 filter
    total_dur = 65.0  # 视频总时长上限
    inputs = []
    for p in narr_files:
        inputs.extend(["-i", str(p)])
    # 算每段延迟 (ms)
    filters = []
    n = len(narr_files)
    for i, (_, start, _) in enumerate(NARRATIONS):
        delay_ms = int(start * 1000)
        filters.append(f"[{i}:a]aresample={SR},adelay={delay_ms}|{delay_ms},apad=whole_dur={total_dur}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(n))
    fc = ";".join(filters) + f";{mix}amix=inputs={n}:normalize=0[mixed]"
    run([
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", fc,
        "-map", "[mixed]",
        "-c:a", "pcm_s16le", "-ar", str(SR), "-ac", "2",
        str(output),
    ])
    return output


def gen_ambient(duration: float, output: Path) -> Path:
    """生成 brown noise 底噪（低音量，fade in/out）。"""
    run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={duration}:amplitude=0.5:seed=42",
        "-af", f"lowpass=f=300,volume=0.10,afade=t=in:st=0:d=2.0,afade=t=out:st={duration - 2.5:.3f}:d=2.5",
        "-ar", str(SR), "-ac", "2",
        str(output),
    ])
    return output


def mix_audio(*, video: Path, narr_track: Path, piano: Path, ambient: Path,
              duration: float, output: Path) -> None:
    """把 narr + piano + ambient 混在一起，video 流 copy 自 video。"""
    # piano 延迟到 ~duration - piano_dur
    piano_delay_ms = int((duration - PIANO_DUR) * 1000)
    fc = (
        f"[0:a]aresample={SR},apad=whole_dur={duration}[nar];"
        f"[1:a]aresample={SR},volume=0.45,adelay={piano_delay_ms}|{piano_delay_ms},"
        f"apad=whole_dur={duration}[pia];"
        f"[2:a]aresample={SR},volume=0.85,atrim=0:{duration}[amb];"
        f"[nar][pia][amb]amix=inputs=3:normalize=0:duration=longest,"
        f"alimiter=limit=0.95[aout]"
    )
    run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(narr_track),
        "-i", str(piano),
        "-i", str(ambient),
        "-i", str(video),
        "-filter_complex", fc,
        "-map", "3:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SR),
        "-shortest",
        str(output),
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="same_v1 配音 + BGM 混音")
    ap.add_argument("--video", default=str(SAME_DIR / "out" / "video_with_overlay.mp4"))
    ap.add_argument("--out", default=str(OUT_FINAL))
    args = ap.parse_args(argv)

    video = Path(args.video)
    out = Path(args.out)
    TMP.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)

    duration = ffprobe_duration(video)
    print(f"[audio] video duration: {duration:.2f}s", flush=True)

    # 1) 配音 4 段
    print("\n[1/4] 生成配音 ...", flush=True)
    narr_files = gen_narration_files(TMP)

    # 2) 拼配音成一条
    print("\n[2/4] 拼配音为总轨 ...", flush=True)
    narr_track = TMP / "narration_track.wav"
    concat_narrations(narr_files, narr_track)
    print(f"  -> {narr_track} ({ffprobe_duration(narr_track):.2f}s)", flush=True)

    # 3) 钢琴 BGM
    print("\n[3/4] 合成钢琴 BGM ...", flush=True)
    piano = TMP / "piano_v1.wav"
    synth_piano(piano, duration=PIANO_DUR, sr=SR)
    print(f"  -> {piano} ({PIANO_DUR:.2f}s)", flush=True)

    # 4) ambient + 最终混音
    print("\n[4/4] 合成 ambient + 终混 ...", flush=True)
    ambient = TMP / "ambient_v1.wav"
    gen_ambient(duration, ambient)
    mix_audio(video=video, narr_track=narr_track, piano=piano, ambient=ambient,
              duration=duration, output=out)

    # 写 meta
    meta = {
        "phase": "audio",
        "input_video": str(video),
        "output": str(out),
        "duration": duration,
        "narrations": [{"text": t, "start_sec": s, "voice": v}
                       for t, s, v in NARRATIONS],
        "piano": "C4/E4/G4/C5, 5 harmonics exponential decay, 1.4s fade-out",
        "ambient": "brown noise lowpass 300Hz volume 0.10, fade in 2s / out 2.5s",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    meta_path = TMP / "same_v1_audio_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[audio] final -> {out}", flush=True)
    print(f"[audio] meta  -> {meta_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
