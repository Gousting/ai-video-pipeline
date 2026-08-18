#!/usr/bin/env python3
"""v3.2 节拍分析：用 librosa 检测 bgm_v32.wav 的拍点，输出 beats.json。

策略：
- librosa.beat.beat_track 估 BPM（应得 120 ± 1）
- librosa.beat.beat_tracker（基于 onset envelope）得到精确拍点时间戳
- 与 meta 中的理论 downbeats 对齐（理论 120 BPM，0.5s/beat）
- 输出拍点列表 + downbeats（每小节第 1 拍）用于剪辑对齐

CLI:
  python beat_analyze_v32.py
"""
import argparse
import json
import sys
from pathlib import Path

import librosa
import numpy as np

ROOT = Path(r"D:\ai-video-pipeline")
BGM_PATH = ROOT / "output" / "pipeline_v3" / "music" / "bgm_v32.wav"
OUT_JSON = ROOT / "output" / "pipeline_v3" / "music" / "beats.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bgm", default=str(BGM_PATH))
    ap.add_argument("--out", default=str(OUT_JSON))
    args = ap.parse_args(argv)

    bgm_path = Path(args.bgm)
    out_path = Path(args.out)
    if not bgm_path.exists():
        print(f"ERROR: BGM 不存在 {bgm_path}", file=sys.stderr)
        return 2

    print(f"[beat] 加载 {bgm_path} ...", flush=True)
    y, sr = librosa.load(str(bgm_path), sr=None, mono=True)
    duration = len(y) / sr
    print(f"[beat] sr={sr} duration={duration:.3f}s samples={len(y)}", flush=True)

    # 1) BPM 估计
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.asarray(tempo).item())
    print(f"[beat] estimated BPM = {tempo:.2f}", flush=True)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    print(f"[beat] detected {len(beat_times)} beats (first 8: {beat_times[:8].round(3)})", flush=True)

    # 2) 与理论 120 BPM 对齐（强约束：BG 是程序合成的，理论拍点已知）
    # 理论拍点：t_b = i * 0.5s，i = 0..N-1
    beat_period = 0.5
    n_theoretical = int(duration / beat_period) + 1
    theoretical_beats = np.array([i * beat_period for i in range(n_theoretical)
                                   if i * beat_period <= duration])
    theoretical_downbeats = np.array([i * 2.0 for i in range(int(duration / 2.0) + 1)
                                      if i * 2.0 <= duration])

    # 3) 检测到的拍点 vs 理论拍点的偏差
    if len(beat_times) > 0:
        # 用理论第 1 拍作为锚
        offset = beat_times[0] - theoretical_beats[0]
        aligned = theoretical_beats + offset
        deviation = np.abs(beat_times - aligned[:len(beat_times)])
        max_dev = float(np.max(deviation)) if len(deviation) > 0 else 0.0
        print(f"[beat] first beat offset: {offset:.3f}s, max deviation: {max_dev:.3f}s",
              flush=True)

    # 4) 输出
    out = {
        "bgm_file": str(bgm_path),
        "duration_sec": duration,
        "sr": int(sr),
        "tempo_estimated_bpm": tempo,
        "tempo_theoretical_bpm": 120.0,
        "beat_period_sec": beat_period,
        "bar_period_sec": 2.0,
        "beats": theoretical_beats.tolist(),          # 理论拍点（用于剪辑对齐）
        "downbeats": theoretical_downbeats.tolist(),  # 每小节第 1 拍
        "beats_count": int(len(theoretical_beats)),
        "downbeats_count": int(len(theoretical_downbeats)),
        "detected_beats_count": int(len(beat_times)),
        "structure": "intro(0-12s)-build(12-32s)-peak(32-52s)-outro(52-72s)",
        "shot_rhythm_rule": "duration_sec = N * 1.0 (2-beat phrase) OR N * 2.0 (4-beat bar) OR N * 4.0 (8-beat double-bar). Cuts land on downbeats.",
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[beat] beats.json -> {out_path}", flush=True)
    print(f"[beat] {out['beats_count']} beats, {out['downbeats_count']} downbeats", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
