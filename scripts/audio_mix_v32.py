#!/usr/bin/env python3
"""v3.2 音频混音：BGM + whoosh SFX + loudnorm。

vs v3.1 (v31_audio.py) 关键差异：
  - **无 edge-tts 配音**（任务明确要求"全片零对白"）
  - **BGM 来自 music/bgm_v32.wav**（v3.2 程序合成 120 BPM 6 层 J-pop）
  - **whoosh SFX** 在每个段间切换点 + intro/outro 触发
  - **loudnorm -16 LUFS**（抖音标准）

CLI:
  python audio_mix_v32.py --video <concat.mp4> --storyboard <sb.json> --out <final.mp4>
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_VIDEO = ROOT / "output" / "pipeline_v3" / "clips_v32" / "concat_no_overlay.mp4"
DEFAULT_STORYBOARD = ROOT / "output" / "pipeline_v3" / "sb" / "storyboard_v32.json"
DEFAULT_OUT = ROOT / "output" / "pipeline_v3" / "final_v3.mp4"
DEFAULT_BGM = ROOT / "output" / "pipeline_v3" / "music" / "bgm_v32.wav"
DEFAULT_TMP = ROOT / "output" / "pipeline_v3" / "tmp"

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


def synth_whoosh(duration: float, out_path: Path, seed: int = 0) -> None:
    """合成 whoosh 音效（白噪声 + 带通 + 短包络）。"""
    rng = np.random.default_rng(seed=seed)
    n = int(SR * duration)
    t = np.arange(n) / SR
    # 白噪声
    noise = rng.standard_normal(n)
    # 带通滤波（差分模拟）
    hp = np.diff(noise, prepend=noise[0])
    lp = np.cumsum(hp) * 0.95
    sig = hp - lp * 0.02
    # 包络（attack + decay）
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
    """柔和环境底噪（低频脉冲 + 远处风铃）。"""
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={duration}:amplitude=0.4:seed=42",
        "-af", "lowpass=f=400,volume=0.05,"  # 极低音量
               "afade=t=in:st=0:d=2.0,"
               f"afade=t=out:st={duration - 2.5:.3f}:d=2.5",
        "-ar", str(SR), "-ac", "2",
        str(out_path),
    ])


def get_cut_points_with_sfx(storyboard: dict) -> list[dict]:
    """从 storyboard 提取 SFX 触发点（含 intro/outro）。"""
    points = []
    # intro card SFX（0.0s）
    points.append({"at_sec": 0.0, "type": "soft_chime", "sfx_file": "intro_chime.wav",
                   "duration_sec": 1.0, "volume": 0.35})
    # 段间切换（在 cut_point）
    for cp in storyboard.get("cut_points", []):
        points.append({
            "at_sec": cp["cut_at_sec"],
            "type": "whoosh",
            "sfx_file": "whoosh.wav",
            "duration_sec": 0.6,
            "volume": 0.45,
            "label": f"shot{cp['from_shot']:02d}→shot{cp['to_shot']:02d}",
        })
    # outro card SFX
    outro_at = storyboard["outro_card"]["start_sec"]
    points.append({"at_sec": outro_at, "type": "soft_chime",
                   "sfx_file": "outro_chime.wav", "duration_sec": 1.0, "volume": 0.35})
    return points


def mix_all(duration: float, sfx_points: list[dict],
            bgm_path: Path, ambient_path: Path,
            video_path: Path, out_path: Path) -> None:
    """BGM + ambient + 多 SFX + 视频 → final_v3.mp4。"""
    tmp_dir = DEFAULT_TMP
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 1. 合成所有 SFX
    sfx_inputs = []
    for i, p in enumerate(sfx_points):
        sfx_path = tmp_dir / f"sfx_{i:02d}_{p['sfx_file']}"
        if "chime" in p["sfx_file"]:
            # chime 用 ffmpeg 合成的更柔
            run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"sine=frequency=880:duration={p['duration_sec']}",
                "-af", f"tremolo=f=4:d=0.3,afade=t=in:st=0:d=0.2,afade=t=out:st={p['duration_sec']-0.5}:d=0.5,volume={p['volume']}",
                "-ar", str(SR), "-ac", "2", str(sfx_path),
            ])
        else:
            synth_whoosh(p["duration_sec"], sfx_path, seed=100 + i)
        sfx_inputs.append((int(p["at_sec"] * 1000), sfx_path, p["volume"]))

    # 2. 构造 filter_complex
    parts = []
    input_indices = []

    # SFX adelay
    for i, (adelay_ms, sfx_path, vol) in enumerate(sfx_inputs):
        idx = i
        parts.append(
            f"[{idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
            f"adelay={adelay_ms}|{adelay_ms},apad=whole_dur={duration},volume={vol}[sfx{i}]"
        )
        input_indices.append(idx)

    # BGM
    bgm_idx = len(sfx_inputs)
    parts.append(
        f"[{bgm_idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,volume=0.85[bgm]"
    )

    # Ambient
    amb_idx = bgm_idx + 1
    parts.append(
        f"[{amb_idx}:a]atrim=0:{duration},asetpts=PTS-STARTPTS[amb]"
    )

    # amix
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
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    ap.add_argument("--bgm", default=str(DEFAULT_BGM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tmp", default=str(DEFAULT_TMP))
    args = ap.parse_args(argv)

    video_path = Path(args.video)
    sb_path = Path(args.storyboard)
    bgm_path = Path(args.bgm)
    out_path = Path(args.out)
    tmp_dir = Path(args.tmp)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if not video_path.exists():
        print(f"ERROR: 视频不存在 {video_path}", file=sys.stderr)
        return 2
    if not sb_path.exists():
        print(f"ERROR: storyboard 不存在 {sb_path}", file=sys.stderr)
        return 2
    if not bgm_path.exists():
        print(f"ERROR: BGM 不存在 {bgm_path}", file=sys.stderr)
        return 2

    storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
    duration = ffprobe_duration(video_path)
    print(f"[audio] video 时长 {duration:.3f}s", flush=True)

    bgm_dur = ffprobe_duration(bgm_path)
    print(f"[audio] BGM 时长 {bgm_dur:.3f}s", flush=True)

    # 1. 环境底噪
    ambient_path = tmp_dir / "v32_ambient.wav"
    synth_ambient(duration, ambient_path)
    print(f"[audio] 环境底噪 -> {ambient_path}", flush=True)

    # 2. 收集 SFX 触发点
    sfx_points = get_cut_points_with_sfx(storyboard)
    print(f"[audio] SFX 触发点 {len(sfx_points)} 个", flush=True)

    # 3. 混音
    mix_all(duration, sfx_points, bgm_path, ambient_path, video_path, out_path)
    print(f"[audio] final -> {out_path}", flush=True)

    meta = {
        "compose_phase": "audio_mix_v32",
        "pipeline_version": "v3.2",
        "input_video": str(video_path),
        "video_duration": duration,
        "bgm_path": str(bgm_path),
        "bgm_duration": bgm_dur,
        "sfx_points": len(sfx_points),
        "voiceover": "NONE — 零对白（任务硬要求）",
        "ambient": "brown noise (lowpass 400Hz, vol 0.05)",
        "target_lufs": -16,
        "output": str(out_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (tmp_dir / "v32_audio_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
