#!/usr/bin/env python3
"""P5 音频环节：配音(edge-tts) + BGM(ffmpeg 低沉环境底噪) + 混音 -> final.mp4。

CLI: python p5_audio.py
流程：
  1. BGM：ffmpeg 合成 15.5s 低沉环境底噪（低频 sine + brown 噪声，低音量，fade in/out）
  2. 配音：复用 output/tmp/narration.mp3（edge-tts 已生成，2.736s），adelay 到 shot3 起始
  3. 混音：narration(1.0) + BGM(≤20% 音量) amix -> final 音频
  4. 复用 video_silent.mp4 的视频流(copy) + 混音音频 -> output/out/final.mp4
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
TMP = ROOT / "output" / "tmp"
OUT = ROOT / "output" / "out"

VIDEO_SILENT = OUT / "video_silent.mp4"
NARRATION = TMP / "narration.mp3"
BGM = TMP / "bgm.wav"
FINAL = OUT / "final.mp4"

DURATION = 15.5       # 成片时长（3×124 帧 / 24fps）
SHOT3_START = 10.5    # 配音在 shot3 的起始位置（秒）
NARRATION_DUR = 2.736  # edge-tts 实际时长


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise RuntimeError(f"命令失败 rc={r.returncode}")


def gen_bgm() -> None:
    """ffmpeg 合成低沉环境底噪：55Hz+110Hz 低频 sine + brown 噪声，低音量，fade。"""
    fc = (
        "[0:a]volume=0.55[a0];"
        "[1:a]volume=0.22[a1];"
        "[2:a]volume=0.10,lowpass=f=280[a2];"
        "[a0][a1][a2]amix=inputs=3:normalize=0,"
        "afade=t=in:st=0:d=2.0,"
        "afade=t=out:st=13.0:d=2.5[bgm]"
    )
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=55:duration={DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=110:duration={DURATION}",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={DURATION}:amplitude=0.5:seed=42",
        "-filter_complex", fc,
        "-map", "[bgm]",
        "-ar", "32000", "-ac", "2",
        str(BGM),
    ])


def mix() -> None:
    """配音 + BGM 混音，BGM 音量压到配音的 20% 以下，替换 final 音频。"""
    # BGM 振幅 0.16（约为配音 -16dB，即 <20% 音量）；配音保持 1.0
    fc = (
        f"[1:a]volume=0.16,atrim=0:{DURATION},asetpts=PTS-STARTPTS[bgm];"
        f"[0:a]aformat=sample_rates=32000:channel_layouts=stereo,"
        f"adelay={int(SHOT3_START*1000)}|{int(SHOT3_START*1000)},"
        f"apad=whole_dur={DURATION}[nar];"
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
    gen_bgm()
    mix()
    print(f"final -> {FINAL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
