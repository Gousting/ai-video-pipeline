#!/usr/bin/env python3
"""same_v2 audio stage: edge-tts voiceover, generated BGM, and ambient bed.

The script reads the rendered storyboard timeline, so the voice starts remain
aligned with the 14-video clock used by same_v2_finalize.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SR = 32000
VIDEO_DURATION = 61.0


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd[:8]), "...", flush=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"{(proc.stderr or proc.stdout or '')[-4000:]}"
        )
    return proc


def ffprobe_duration(path: Path) -> float:
    proc = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(proc.stdout.strip() or 0.0)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def shot_start(storyboard: dict[str, Any], shot_index: int) -> float:
    elapsed = 0.0
    for shot in storyboard.get("shots", []) or []:
        if int(shot.get("index", 0)) == shot_index:
            return elapsed + min(0.35, float(shot.get("duration", 4.5)) * 0.12)
        elapsed += float(shot.get("duration", 0.0))
    raise KeyError(f"shot {shot_index} not found")


def synth_narration(storyboard: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    lines = [
        {"text": "学姐还是学妹？", "start": 0.35, "voice": "zh-CN-XiaoxiaoNeural",
         "rate": "+0%", "pitch": "-2Hz"},
        {"text": "暗黑偶像，骷髅朋克。", "start": shot_start(storyboard, 5),
         "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%", "pitch": "-4Hz"},
        {"text": "元气学园，萌熊校园。", "start": shot_start(storyboard, 11),
         "voice": "zh-CN-XiaoyiNeural", "rate": "+3%", "pitch": "+3Hz"},
        {"text": "你选学姐还是学妹？评论区告诉我。", "start": shot_start(storyboard, 14) + 0.2,
         "voice": "zh-CN-XiaoyiNeural", "rate": "+0%", "pitch": "+1Hz"},
    ]
    paths: list[Path] = []
    for i, item in enumerate(lines):
        path = out_dir / f"narration_{i:02d}.mp3"
        # Reuse a valid cached file so interrupted runs do not spend four TTS
        # calls again; overwrite is implicit in edge-tts --write-media.
        if not path.is_file() or path.stat().st_size < 1000:
            run([
                "edge-tts", "--voice", item["voice"],
                f"--rate={item['rate']}", f"--pitch={item['pitch']}",
                "--text", item["text"], "--write-media", str(path),
            ], timeout=300)
        item["file"] = str(path)
        item["duration"] = ffprobe_duration(path)
        paths.append(path)
        print(f"  narration_{i:02d}: {item['text']} ({item['duration']:.2f}s)", flush=True)
    return lines


def concat_narrations(lines: list[dict[str, Any]], out: Path, duration: float) -> None:
    inputs: list[str] = []
    for item in lines:
        inputs.extend(["-i", item["file"]])
    filters: list[str] = []
    for i, item in enumerate(lines):
        delay = int(round(float(item["start"]) * 1000))
        filters.append(
            f"[{i}:a]aresample={SR},adelay={delay}|{delay},"
            f"apad=whole_dur={duration:.3f}[a{i}]"
        )
    amix = "".join(f"[a{i}]" for i in range(len(lines)))
    filters.append(f"{amix}amix=inputs={len(lines)}:duration=longest:normalize=0[narr]")
    run([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters), "-map", "[narr]",
        "-c:a", "pcm_s16le", "-ar", str(SR), "-ac", "2", str(out),
    ])


def synth_bgm(path: Path, duration: float = VIDEO_DURATION) -> None:
    """Subtle anime-style arpeggio with a warm, low-volume pulse bed."""
    n = int(SR * duration)
    t = np.arange(n, dtype=np.float64) / SR
    sig = np.zeros(n, dtype=np.float64)
    rng = np.random.default_rng(20260818)
    progression = [261.63, 311.13, 392.00, 466.16, 349.23, 392.00, 440.00, 523.25]
    note_len = 2.0
    for note_idx, freq in enumerate(progression * 8):
        begin = note_idx * note_len
        if begin >= duration:
            break
        end = min(begin + note_len * 0.92, duration)
        start = int(begin * SR)
        stop = int(end * SR)
        nt = t[start:stop] - begin
        # Two harmonics and a quiet high partial keep the piano readable
        # without competing with the voice.
        note = 0.34 * np.sin(2 * np.pi * freq * nt)
        note += 0.14 * np.sin(2 * np.pi * freq * 2.0 * nt)
        note += 0.06 * np.sin(2 * np.pi * freq * 3.0 * nt)
        attack = int(0.018 * SR)
        note[:attack] *= np.linspace(0, 1, attack)
        note *= np.exp(-3.2 * nt / max(note_len, 0.1))
        sig[start:stop] += note
    # A quiet octave pulse at 1Hz adds movement between character changes.
    beat = 0.5 + 0.5 * np.sin(2 * np.pi * 1.0 * t)
    sig += 0.025 * beat * np.sin(2 * np.pi * 65.41 * t)
    fade = int(2.5 * SR)
    if len(sig) > 2 * fade:
        sig[:fade] *= np.linspace(0, 1, fade)
        sig[-fade:] *= np.linspace(1, 0, fade)
    peak = float(np.max(np.abs(sig))) or 1.0
    sig = (sig / peak * 0.78).astype(np.float32)
    stereo = np.stack([sig, sig * (0.985 + 0.015 * np.sin(2 * np.pi * 0.23 * t))], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def synth_ambient(path: Path, duration: float = VIDEO_DURATION) -> None:
    run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"anoisesrc=color=brown:duration={duration:.3f}:amplitude=0.22:seed=42",
        "-af", "lowpass=f=280,volume=0.075,afade=t=in:st=0:d=1.5,"
        f"afade=t=out:st={max(0.0, duration - 2.0):.3f}:d=2.0",
        "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(path),
    ])


def mix_final(video: Path, narr: Path, bgm: Path, ambient: Path, out: Path,
              duration: float) -> None:
    fc = (
        f"[1:a]aresample={SR},apad=whole_dur={duration:.3f}[nar];"
        f"[2:a]aresample={SR},apad=whole_dur={duration:.3f}[pia];"
        f"[3:a]aresample={SR},apad=whole_dur={duration:.3f}[amb];"
        "[nar][pia][amb]amix=inputs=3:duration=longest:normalize=0,"
        "loudnorm=I=-16:LRA=11:TP=-1.5,alimiter=limit=0.92[aout]"
    )
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(narr),
        "-i", str(bgm), "-i", str(ambient), "-filter_complex", fc,
        "-map", "0:v:0", "-map", "[aout]", "-t", f"{duration:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
        "-ac", "2", "-movflags", "+faststart", str(out),
    ], timeout=900)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="same_v2 edge-tts + BGM + ambient mix")
    ap.add_argument("--same-dir", default=str(REPO_ROOT / "output" / "same_v2"))
    args = ap.parse_args(argv)
    same_dir = Path(args.same_dir).resolve()
    storyboard_path = same_dir / "storyboard_v2.json"
    video = same_dir / "out" / "video_with_overlay.mp4"
    final = same_dir / "final_same_v2.mp4"
    if not storyboard_path.is_file() or not video.is_file():
        raise FileNotFoundError("rendered storyboard/video is missing")
    sb = read_json(storyboard_path)
    duration = float(sb.get("total_duration", VIDEO_DURATION))
    out_dir = same_dir / "tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    narr_files = synth_narration(sb, out_dir)
    narr_track = out_dir / "narration_track.wav"
    concat_narrations(narr_files, narr_track, duration)
    bgm = out_dir / "piano_v2.wav"
    synth_bgm(bgm, duration)
    ambient = out_dir / "ambient_v2.wav"
    synth_ambient(ambient, duration)
    mix_final(video, narr_track, bgm, ambient, final, duration)
    metadata = {
        "input_video": str(video), "output": str(final), "duration": duration,
        "narrations": narr_files, "narration_track": str(narr_track),
        "bgm": "C/G/Bb arpeggio + 65Hz pulse, 2.5s fades",
        "ambient": "brown noise lowpass 280Hz, volume 0.075, fade 1.5s/2.0s",
        "audio": "AAC 192kbps 32kHz stereo, loudnorm -16 LUFS, limiter 0.92",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_json(out_dir / "same_v2_audio_meta.json", metadata)
    print(f"[audio] final -> {final}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
