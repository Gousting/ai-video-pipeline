#!/usr/bin/env python3
"""v3.2 BGM 合成：120 BPM J-pop 风，4 段式起承转合，~72s。

设计要点（vs v3.1）：
- 4 个独立音轨层（kick / hi-hat / bass / synth-lead）→ librosa 节拍分析更准
- 4 段式结构：intro(0-12s) → build(12-32s) → peak(32-52s) → outro(52-72s)
- 节奏密度逐段递增 → 给"快切 MV"的能量曲线
- 每个 kick 都落在 downbeat → 节拍对齐剪辑天然成立

CLI:
  python bgm_gen_v32.py                # 默认 72s
  python bgm_gen_v32.py --duration 80  # 自定义时长
"""
import argparse
import json
import wave
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\ai-video-pipeline")
OUT_DIR = ROOT / "output" / "pipeline_v3" / "music"
SR = 32000  # 与 v3.1 一致

# C 大调频率（C4 = 261.63Hz）
C4 = 261.63
NOTES_C_MAJOR = [C4 * (2 ** (n / 12)) for n in [0, 2, 4, 5, 7, 9, 11]]  # CDEFGAB
NOTES_C_MINOR = [C4 * (2 ** (n / 12)) for n in [0, 2, 3, 5, 7, 8, 10]]  # Cm
NOTES_A_MINOR = [C4 * (2 ** (n / 12)) for n in [-3, -1, 0, 2, 4, 5, 7]]  # Am (rel to C)
NOTES_F_MAJOR = [C4 * (2 ** (n / 12)) for n in [-5, -3, -1, 0, 2, 4, 6]]  # F
NOTES_G_MAJOR = [C4 * (2 ** (n / 12)) for n in [-2, 0, 2, 3, 5, 7, 9]]    # G


def envelope(n: int, attack_s: float = 0.01, decay_s: float = 0.05,
             sustain: float = 0.7, release_s: float = 0.1) -> np.ndarray:
    """ADSR 包络。"""
    if n <= 0:
        return np.zeros(0)
    env = np.ones(n)
    atk = int(attack_s * SR)
    dec = int(decay_s * SR)
    rel = int(release_s * SR)
    if atk > 0:
        env[:atk] = np.linspace(0, 1, atk)
    if dec > 0:
        env[atk:atk + dec] = np.linspace(1, sustain, dec)
    if rel > 0 and atk + dec < n:
        env[atk + dec:] = np.linspace(sustain, 0, max(1, n - atk - dec))
    return env


def kick_drum(t: np.ndarray, freq: float = 60.0, decay: float = 0.08) -> np.ndarray:
    """Kick: 低频正弦 + 指数衰减 + pitch sweep。"""
    n = len(t)
    # pitch sweep from 120Hz -> 50Hz
    phase = 2 * np.pi * np.cumsum(freq + (120 - freq) * np.exp(-t / 0.04)) * (1 / SR)
    env = np.exp(-t / decay)
    return 0.9 * env * np.sin(phase)


def hihat(t: np.ndarray, decay: float = 0.04) -> np.ndarray:
    """Hi-hat: 高频白噪声 + 短衰减。"""
    rng = np.random.default_rng(seed=int(np.sum(t[:1000]) * 1000) % (2**31))
    noise = rng.standard_normal(len(t))
    # 高通滤波（差分）
    hp = np.diff(noise, prepend=noise[0])
    env = np.exp(-t / decay)
    return 0.25 * env * hp


def snare(t: np.ndarray, decay: float = 0.12) -> np.ndarray:
    """Snare: 噪声 + 中频正弦。"""
    rng = np.random.default_rng(seed=int(np.sum(t[:1000]) * 2000) % (2**31))
    noise = rng.standard_normal(len(t))
    env = np.exp(-t / decay)
    tone = 0.4 * env * np.sin(2 * np.pi * 200 * t)
    return 0.5 * (noise + tone) * env


def bass_note(freq: float, n_samples: int, decay_s: float = 0.4) -> np.ndarray:
    """贝斯音: 低八度 + 衰减。"""
    t = np.arange(n_samples) / SR
    env = np.exp(-t / decay_s)
    # 二次谐波丰富
    sig = 0.6 * env * np.sin(2 * np.pi * freq * t)
    sig += 0.3 * env * np.sin(2 * np.pi * freq * 2 * t)
    sig += 0.15 * env * np.sin(2 * np.pi * freq * 0.5 * t)  # sub
    return sig


def synth_lead(freq: float, n_samples: int, decay_s: float = 0.5,
               vibrato: float = 0.003) -> np.ndarray:
    """Synth 主旋律: 三角波 + 颤音 + 谐波。"""
    t = np.arange(n_samples) / SR
    # 颤音 (5.5 Hz)
    vib = 1 + vibrato * np.sin(2 * np.pi * 5.5 * t)
    env = np.exp(-t / decay_s)
    sig = 0.5 * env * np.sin(2 * np.pi * freq * vib * t)
    sig += 0.25 * env * np.sin(2 * np.pi * freq * 2 * vib * t)
    sig += 0.1 * env * np.sin(2 * np.pi * freq * 3 * vib * t)
    return sig


def synth_pad(freqs: list[float], n_samples: int) -> np.ndarray:
    """Synth 持续和弦: 多频叠加 + 长包络。"""
    t = np.arange(n_samples) / SR
    # ADSR：长 attack + 长 release
    env = envelope(n_samples, attack_s=0.5, decay_s=0.3, sustain=0.6, release_s=0.8)
    sig = np.zeros(n_samples)
    for f in freqs:
        sig += 0.18 * np.sin(2 * np.pi * f * t)
        sig += 0.09 * np.sin(2 * np.pi * f * 2 * t)
    return sig * env


def synth_bgm(duration: float) -> tuple[np.ndarray, dict]:
    """合成 120 BPM J-pop BGM，返回 (stereo_signal, meta)。

    节拍结构 (120 BPM = 0.5s/beat)：
    - Bar 长度 = 4 beats = 2s
    - 4 段式分割：
      - Intro:   0-12s  (6 bars = 24 beats)
      - Build:  12-32s (10 bars = 40 beats)
      - Peak:   32-52s (10 bars = 40 beats)
      - Outro:  52-72s (10 bars = 40 beats)
    """
    n_total = int(SR * duration)
    sig = np.zeros(n_total)
    beat_period = 0.5  # 120 BPM
    bar_period = 2.0   # 4 beats

    # ---- Kick: 每拍打 downbeat + 每小节第 2、4 拍 (four-on-the-floor 风格) ----
    n_beats = int(duration / beat_period)
    for b in range(n_beats):
        t_start = b * beat_period
        idx = int(t_start * SR)
        # 跳过 4 段最弱的 intro 前 2 bar（保留 build-up）
        # Intro 后半 (6s 后) 起 kick
        if t_start < 6.0:
            continue
        t_seg = np.arange(int(0.15 * SR)) / SR
        kick = kick_drum(t_seg, freq=55.0, decay=0.08)
        end = min(idx + len(kick), n_total)
        sig[idx:end] += kick[:end - idx]

    # ---- Hi-hat: 每拍 8 分音符（更密集的 groove） ----
    n_eighths = int(duration / (beat_period / 2))
    for e in range(n_eighths):
        t_start = e * (beat_period / 2)
        # Intro 不打，build 起打
        if t_start < 4.0:
            continue
        # 在 off-beats (奇数 8 分音符) 上打，开拍 (偶数) 弱打
        on_off_beat = (e % 2 == 1)
        idx = int(t_start * SR)
        t_seg = np.arange(int(0.06 * SR)) / SR
        hh = hihat(t_seg, decay=0.04 if on_off_beat else 0.025)
        end = min(idx + len(hh), n_total)
        sig[idx:end] += hh[:end - idx]

    # ---- Snare: 每小节第 2、4 拍 (backbeat)，build 起 ----
    n_bars = int(duration / bar_period)
    for bar in range(n_bars):
        for beat_in_bar in [1, 3]:  # 第 2、4 拍
            t_start = bar * bar_period + beat_in_bar * beat_period
            if t_start < 8.0:
                continue  # build-up 后才加
            idx = int(t_start * SR)
            t_seg = np.arange(int(0.18 * SR)) / SR
            sn = snare(t_seg, decay=0.12)
            end = min(idx + len(sn), n_total)
            sig[idx:end] += sn[:end - idx]

    # ---- Bass: 按和弦走，每个 bar 一个根音 (C-F-G-Am 或类似) ----
    # Intro: 慢四和弦 (每个 bar 一个) | Build: 加快 | Peak: 快 + 高音 | Outro: 回到 C
    chord_progression = [
        # (bar_idx_start, bar_idx_end, root_freq_index_in_C_MAJOR, duration_per_bar_sec)
        (0, 6, 0, 2.0),     # Intro: 0-12s, C 大调，每 bar 一个 C
        (6, 11, 0, 2.0),    # Build: 12-22s, C-F-G-C
        (11, 16, 4, 2.0),   # Peak: 22-32s, F-G-C-Am
        (16, 21, 2, 2.0),   # Outro: 32-42s, ...
        (21, 26, 0, 2.0),
        (26, 31, 4, 2.0),
        (31, 36, 2, 2.0),   # 收束前回到 C 大调
    ]
    # 简化：只取每 bar 的根音
    roots_cycle = [0, 4, -2, -3, 0, 4, -2, -3]  # C, F, G, Am, C, F, G, Am
    for bar in range(n_bars):
        root_idx = roots_cycle[bar % len(roots_cycle)]
        # 把 root_idx 映射到 C 大调音阶
        if root_idx >= 0:
            f = NOTES_C_MAJOR[root_idx]
        else:
            f = NOTES_C_MAJOR[0] * (2 ** (root_idx / 12))
        t_start = bar * bar_period
        idx = int(t_start * SR)
        n_seg = int(bar_period * SR)
        # 每 bar 前半 + 后半各打一次 bass
        for sub_start in [0.0, bar_period / 2]:
            sub_idx = int((t_start + sub_start) * SR)
            bass = bass_note(f, int(beat_period * SR), decay_s=0.3)
            end = min(sub_idx + len(bass), n_total)
            sig[sub_idx:end] += bass[:end - sub_idx]

    # ---- Synth Lead (主旋律): intro 不打，build 起每 bar 4 音 ----
    melody_progression = [
        # (bar_idx, [note_offsets_from_C4])
        (6, [0, 2, 4, 7]),   # C D E G
        (7, [4, 7, 9, 12]),  # E G B C5
        (8, [7, 4, 2, 0]),   # G E D C
        (9, [-3, 0, 2, 4]),  # Am 段
        (10, [4, 7, 4, 2]),  # E G E D
        (11, [7, 9, 12, 14]),
        (12, [12, 9, 7, 4]),  # C5 B G E
        (13, [4, 2, 0, -3]),
        (14, [2, 4, 7, 9]),
        (15, [7, 12, 9, 7]),
        (16, [4, 7, 4, 2]),
        (17, [0, 2, 4, 7]),
        (18, [-3, 0, 2, 4]),
        (19, [2, 4, 7, 9]),
        (20, [4, 7, 9, 12]),
        (21, [7, 4, 2, 0]),
        (22, [-3, 0, 2, 4]),
        (23, [0, 2, 4, 7]),
        (24, [4, 7, 9, 12]),
        (25, [12, 9, 7, 4]),
        (26, [7, 4, 2, 0]),
        (27, [4, 2, 0, -3]),
        (28, [0, 2, 4, 7]),
        (29, [4, 7, 4, 2]),
        (30, [2, 0, -3, -5]),
        (31, [-5, -3, 0, 2]),
        (32, [0, 2, 4, 7]),
        (33, [4, 7, 9, 12]),
        (34, [12, 9, 7, 4]),
        (35, [7, 4, 2, 0]),
    ]
    for bar_idx, notes in melody_progression:
        t_bar_start = bar_idx * bar_period
        if t_bar_start >= duration:
            continue
        for i, note_off in enumerate(notes):
            t_note = t_bar_start + i * (bar_period / 4)  # 4 分音符
            f = C4 * (2 ** (note_off / 12))
            idx = int(t_note * SR)
            note_dur = int(bar_period / 4 * SR)
            lead = synth_lead(f, note_dur, decay_s=0.3)
            end = min(idx + len(lead), n_total)
            sig[idx:end] += lead[:end - idx]

    # ---- Synth Pad (和弦铺底): intro + build 都铺 ----
    pad_chord_progression = [
        (0, 6, NOTES_C_MAJOR[0:3]),     # Intro: C
        (6, 9, NOTES_F_MAJOR[0:3]),     # F
        (9, 11, NOTES_G_MAJOR[0:3]),    # G
        (11, 16, NOTES_C_MAJOR[0:3] + [NOTES_C_MAJOR[4]]),  # C + 高音
        (16, 21, NOTES_A_MINOR[0:3]),   # Am
        (21, 26, NOTES_F_MAJOR[0:3]),
        (26, 31, NOTES_G_MAJOR[0:3]),
        (31, 36, NOTES_C_MAJOR[0:3]),
    ]
    for bar_start, bar_end, chord_notes in pad_chord_progression:
        # 每 2 bar 换和弦
        for sub_bar in range(bar_start, bar_end, 2):
            t_start = sub_bar * bar_period
            n_pad = int(2 * bar_period * SR)
            pad = synth_pad(chord_notes, n_pad)
            idx = int(t_start * SR)
            end = min(idx + len(pad), n_total)
            sig[idx:end] += pad[:end - idx]

    # ---- 全局处理 ----
    # 归一化
    peak = np.max(np.abs(sig)) + 1e-9
    sig = sig / peak * 0.85

    # 渐入渐出
    fade_in = int(0.5 * SR)
    fade_out = int(2.0 * SR)
    sig[:fade_in] *= np.linspace(0, 1, fade_in)
    sig[-fade_out:] *= np.linspace(1, 0, fade_out)

    # 立体声
    stereo = np.stack([sig, sig * 0.95], axis=1)  # 微弱立体声差

    # 节拍元数据（用于 beats.json）
    meta = {
        "duration_sec": duration,
        "sr": SR,
        "bpm": 120.0,
        "beat_period_sec": beat_period,
        "bar_period_sec": bar_period,
        "n_bars": n_bars,
        "n_beats": n_beats,
        "downbeats": [b * beat_period for b in range(n_beats)],
        "bars": [bar * bar_period for bar in range(n_bars)],
        "structure": "intro(0-12s)-build(12-32s)-peak(32-52s)-outro(52-72s)",
        "chord_progression_per_bar": roots_cycle,
        "key": "C major",
        "synth_layers": ["kick", "hihat", "snare", "bass", "synth_lead", "synth_pad"],
    }
    return stereo.astype(np.float32), meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=72.0)
    ap.add_argument("--out", default=str(OUT_DIR / "bgm_v32.wav"))
    ap.add_argument("--meta-out", default=str(OUT_DIR / "bgm_v32_meta.json"))
    args = ap.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[bgm] 合成 {args.duration}s 120 BPM J-pop ...", flush=True)
    stereo, meta = synth_bgm(args.duration)

    # 写 WAV
    pcm = (stereo * 32767).astype(np.int16)
    out_path = Path(args.out)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"[bgm] WAV -> {out_path} ({out_path.stat().st_size // 1024} KB)", flush=True)

    # 写 meta
    Path(args.meta_out).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bgm] meta -> {args.meta_out}", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
