#!/usr/bin/env python3
"""v3.3-line0 拼接 + 音频混音：BGM + ambient + whoosh SFX + loudnorm。

适配 1344×768 landscape，3 段 × 10s = ~30s。
复用 v3.2 BGM（72s，取前 30s）+ v3.2 ambient + 自合成 whoosh。

vs audio_mix_v32.py 关键差异：
  - **landscape 1344×768**（v3.2 是 portrait 720x1280），不做 scale（已是原生分辨率）
  - **3 段**（v3.2 是 6 段）
  - **无 intro/outro 卡片**（30s 验证版省略，与评分口径一致）
  - 输出 final_v33_line0_<condition>.mp4

CLI:
  python compose_v33_line0.py --condition v32_30s
  python compose_v33_line0.py --condition v33_30s
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\ai-video-pipeline")
OUT_ROOT = ROOT / "output" / "pipeline_v33_line0"
BGM_PATH = ROOT / "output" / "pipeline_v3" / "music" / "bgm_v32.wav"
TMP_DIR = OUT_ROOT / "tmp"
SR = 32000

CONDITIONS = {
    "v32_30s": {"clips_dir": OUT_ROOT / "clips_v32_30s",
                "out": OUT_ROOT / "final_v33_line0_v32_30s.mp4"},
    "v33_30s": {"clips_dir": OUT_ROOT / "clips_v33_30s",
                "out": OUT_ROOT / "final_v33_line0_v33_30s.mp4"},
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg 失败 rc={r.returncode}")
    return r


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def synth_whoosh(duration: float, out_path: Path, seed: int = 0) -> None:
    rng = np.random.default_rng(seed=seed)
    n = int(SR * duration)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    hp = np.diff(noise, prepend=noise[0])
    lp = np.cumsum(hp) * 0.95
    sig = hp - lp * 0.02
    env = np.ones(n)
    atk = int(0.05 * SR)
    rel = int(0.3 * SR)
    if atk > 0:
        env[:atk] = np.linspace(0, 1, atk)
    if rel > 0:
        env[-rel:] = np.linspace(1, 0, rel)
    sig = sig * env * 0.4
    stereo = np.stack([sig, sig * 0.92], axis=1)
    pcm = (stereo * 32767).astype(np.int16)
    import wave
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def synth_ambient(duration: float, out_path: Path) -> None:
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={duration}:amplitude=0.4:seed=42",
        "-af", "lowpass=f=400,volume=0.05,"
               "afade=t=in:st=0:d=2.0,"
               f"afade=t=out:st={max(0, duration - 2.5):.3f}:d=2.5",
        "-ar", str(SR), "-ac", "2",
        str(out_path),
    ])


def concat_only(clips_dir: Path, out_path: Path) -> float:
    list_file = clips_dir / "concat_list.txt"
    durations = []
    with list_file.open("w", encoding="utf-8") as f:
        for shot_idx in (1, 2, 3):
            clip = clips_dir / f"shot{shot_idx:02d}.mp4"
            if not clip.exists():
                raise RuntimeError(f"缺失 shot {clip}")
            d = ffprobe_duration(clip)
            durations.append(d)
            f.write(f"file '{clip.as_posix()}'\n")
            print(f"  shot{shot_idx:02d}: {d:.3f}s -> {clip.name}", flush=True)

    # concat demuxer → libx264 1344x768, drop audio (audio mix will replace it)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-vf", "scale=1344:768:flags=lanczos,setsar=1:1",
        "-an",
        "-r", "24",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run(cmd)
    final_dur = ffprobe_duration(out_path)
    print(f"[concat] final -> {out_path} ({final_dur:.3f}s)", flush=True)
    return final_dur


def mix_audio(duration: float, bgm_path: Path, video_path: Path,
              out_path: Path, tmp_dir: Path) -> None:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ambient_path = tmp_dir / "ambient_v33l0.wav"
    synth_ambient(duration, ambient_path)

    # SFX triggers: whoosh at 10s and 20s (segment boundaries)
    sfx_points = [
        {"at_sec": 10.0, "duration_sec": 0.6, "volume": 0.45, "label": "seg1→seg2"},
        {"at_sec": 20.0, "duration_sec": 0.6, "volume": 0.45, "label": "seg2→seg3"},
    ]
    sfx_inputs = []
    for i, p in enumerate(sfx_points):
        sfx_path = tmp_dir / f"sfx_{i:02d}_whoosh.wav"
        synth_whoosh(p["duration_sec"], sfx_path, seed=100 + i)
        sfx_inputs.append((int(p["at_sec"] * 1000), sfx_path, p["volume"]))

    parts = []
    for i, (adelay_ms, sfx_path, vol) in enumerate(sfx_inputs):
        parts.append(
            f"[{i}:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
            f"adelay={adelay_ms}|{adelay_ms},apad=whole_dur={duration},volume={vol}[sfx{i}]"
        )

    bgm_idx = len(sfx_inputs)
    parts.append(
        f"[{bgm_idx}:a]atrim=0:{duration},asetpts=PTS-STARTPTS,"
        f"aformat=sample_rates={SR}:channel_layouts=stereo,volume=0.85[bgm]"
    )

    amb_idx = bgm_idx + 1
    parts.append(
        f"[{amb_idx}:a]atrim=0:{duration},asetpts=PTS-STARTPTS[amb]"
    )

    n_inputs = len(sfx_inputs) + 2
    amix_inputs = "".join(f"[sfx{i}]" for i in range(len(sfx_inputs))) + "[bgm][amb]"
    parts.append(
        f"{amix_inputs}amix=inputs={n_inputs}:normalize=0:duration=longest,"
        f"alimiter=limit=0.95,loudnorm=I=-16:TP=-1:LRA=11[aout]"
    )
    filter_complex = ";\n".join(parts)

    video_idx = amb_idx + 1

    cmd = ["ffmpeg", "-y"]
    for _, sfx_path, _ in sfx_inputs:
        cmd.extend(["-i", str(sfx_path)])
    cmd.extend(["-i", str(bgm_path)])
    cmd.extend(["-i", str(ambient_path)])
    cmd.extend(["-i", str(video_path)])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"{video_idx}:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ])
    run(cmd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=list(CONDITIONS.keys()))
    ap.add_argument("--bgm", default=str(BGM_PATH))
    args = ap.parse_args(argv)

    cfg = CONDITIONS[args.condition]
    clips_dir = cfg["clips_dir"]
    final_out = cfg["out"]
    concat_out = clips_dir / "concat_no_overlay.mp4"

    print(f"[compose] condition={args.condition}", flush=True)
    print(f"[compose] clips_dir={clips_dir}", flush=True)

    duration = concat_only(clips_dir, concat_out)
    print(f"[compose] concat 时长 {duration:.3f}s", flush=True)

    bgm_path = Path(args.bgm)
    if not bgm_path.exists():
        raise RuntimeError(f"BGM 不存在 {bgm_path}")

    mix_audio(duration, bgm_path, concat_out, final_out, TMP_DIR / args.condition)
    print(f"[compose] final -> {final_out}", flush=True)

    final_dur = ffprobe_duration(final_out)
    meta = {
        "condition": args.condition,
        "pipeline_version": "v3.3-line0",
        "input_video": str(concat_out),
        "video_duration": duration,
        "final_duration": final_dur,
        "bgm_path": str(bgm_path),
        "sfx_points": [{"at_sec": 10.0, "type": "whoosh"},
                       {"at_sec": 20.0, "type": "whoosh"}],
        "voiceover": "NONE",
        "target_lufs": -16,
        "output": str(final_out),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (TMP_DIR / args.condition / "compose_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
