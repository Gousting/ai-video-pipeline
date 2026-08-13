#!/usr/bin/env python3
"""P5-v2 音频环节：配音(edge-tts) + BGM(ffmpeg 低沉环境底噪) + 混音 -> output/out/final_v2.mp4。

流程：
  1. 配音：edge-tts 生成 narration（shot3「雨还在下，他走得不快。」）
  2. BGM：ffmpeg 合成低沉环境底噪（55Hz+110Hz 低频 sine + brown 噪声，低音量，fade in/out）
  3. 混音：narration(adelay 到 shot3 起始) + BGM(音量压低) amix
  4. 视频流 copy 自 video_silent_v2.mp4 + 混音音频 -> final_v2.mp4
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
TMP = ROOT / "output" / "tmp"
OUT = ROOT / "output" / "out"

VIDEO_SILENT = OUT / "video_silent_v2.mp4"
NARRATION = TMP / "narration_v2.mp3"
BGM = TMP / "bgm_v2.wav"
FINAL = OUT / "final_v2.mp4"

VOICE = "zh-CN-XiaoxiaoNeural"
NARRATION_TEXT = "雨还在下，他走得不快。"
SHOT3_START = 10.0     # 3 镜头 × 5s，shot3 从第 10s 开始


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
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


def gen_narration() -> float:
    run(["edge-tts", "--voice", VOICE, "--text", NARRATION_TEXT,
         "--write-media", str(NARRATION)])
    return ffprobe_duration(NARRATION)


def gen_bgm(duration: float) -> None:
    fc = (
        "[0:a]volume=0.55[a0];"
        "[1:a]volume=0.22[a1];"
        "[2:a]volume=0.10,lowpass=f=280[a2];"
        "[a0][a1][a2]amix=inputs=3:normalize=0,"
        "afade=t=in:st=0:d=2.0,"
        f"afade=t=out:st={duration - 2.5:.3f}:d=2.5[bgm]"
    )
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=55:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=110:duration={duration}",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={duration}:amplitude=0.5:seed=42",
        "-filter_complex", fc,
        "-map", "[bgm]",
        "-ar", "32000", "-ac", "2",
        str(BGM),
    ])


def mix(duration: float) -> None:
    fc = (
        f"[1:a]volume=0.16,atrim=0:{duration},asetpts=PTS-STARTPTS[bgm];"
        f"[0:a]aformat=sample_rates=32000:channel_layouts=stereo,"
        f"adelay={int(SHOT3_START * 1000)}|{int(SHOT3_START * 1000)},"
        f"apad=whole_dur={duration}[nar];"
        f"[nar][bgm]amix=inputs=2:normalize=0:duration=longest[aout]"
    )
    run([
        "ffmpeg", "-y",
        "-i", str(NARRATION),
        "-i", str(BGM),
        "-i", str(VIDEO_SILENT),
        "-filter_complex", fc,
        "-map", "2:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", "32000",
        "-shortest",
        str(FINAL),
    ])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(VIDEO_SILENT)
    print(f"[audio] video_silent_v2 时长 {duration:.3f}s")
    narration_dur = gen_narration()
    print(f"[audio] narration 时长 {narration_dur:.3f}s")
    gen_bgm(duration)
    mix(duration)
    print(f"[audio] final_v2 -> {FINAL}")
    meta = {"video_silent_duration": duration, "narration_duration": narration_dur,
            "voice": VOICE, "narration_text": NARRATION_TEXT, "shot3_start": SHOT3_START,
            "bgm": "55Hz+110Hz sine + brown noise, low volume, fade in 2s / out 2.5s"}
    (TMP / "p5v2_audio_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
