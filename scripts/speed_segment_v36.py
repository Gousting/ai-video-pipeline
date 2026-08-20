#!/usr/bin/env python3
"""v3.6 局部变速脚本：基于 rhythm_plan 落地 setpts + atempo（per 任务书 v3.6 Step 2）。

vs speed_segment_v34.py 关键差异：

- 不再接受 JSON windows 字符串；改为读 rhythm_plan_v36.json 的
  per-shot `speed_window` 字段，统一处理整段
- 输出每个变速后的 segment mp4（供 compose_final_v36 拼），加一份 speed_meta.json
- 支持 `--dry-run`：打印 ffmpeg 命令链而不执行
- 默认策略：
    - slow_open / slow_tail 段：factor=1.0，整段保持原速
    - fast_middle 段：在中段 50% 窗口用 setpts=(PTS/(1/1.35)) + atempo=1/1.35
      局部快放；前后各 25% 保持原速
- RIFE 接口保留（--with-rife），与 v34 风格一致

CLI:
  python speed_segment_v36.py --rhythm-plan <rhythm.json> --clips-dir <d> \
                               --out-dir <d> --dry-run
  python speed_segment_v36.py --rhythm-plan <rhythm.json> --clips-dir <d> \
                               --out-dir <d>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_RHYTHM = ROOT / "output" / "pipeline_v36" / "sb" / "rhythm_plan_v36.json"
DEFAULT_CLIPS = ROOT / "output" / "pipeline_v36" / "clips"
DEFAULT_OUT = ROOT / "output" / "pipeline_v36" / "clips_sped"

FPS = 24
RIFE_FILTER_CHAIN = (
    "setpts=PTS-STARTPTS,fps=60,"
    "vfrrt=rife_nvfp16:model=rife47.pth:scale=1.0,"
    "fps=24"
)


def run(cmd: list[str], *, dry_run: bool = False) -> str:
    s = " ".join(cmd)
    if dry_run:
        print(f"[dry-run] + {s}", flush=True)
        return s
    print(f"[speed-v36] + {s}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed rc={r.returncode}")
    return s


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return float(r.stdout.strip() or 0.0)


def build_filter_for_window(window_list: list[dict], beat_period: float,
                             *, with_rife: bool = False) -> str:
    """构造多窗口 trim + setpts + concat 滤镜链（filter_complex 风格）。

    ffmpeg setpts 表达式不支持内联 enable= 条件。正确做法：
      1) split 输入到 N 份
      2) 每份 trim + setpts（变速窗口用 setpts=PTS/(1/factor) 加速）
      3) concat 拼回

    窗口格式：{kind, start_beat, end_beat, factor}
    beat → 秒换算：sec = beat * beat_period
    """
    if not window_list:
        base = "setpts=PTS-STARTPTS"
        return base + ("," + RIFE_FILTER_CHAIN if with_rife else "")

    # 把窗口按时间排序
    windows = sorted(window_list, key=lambda w: float(w["start_beat"]))
    n = len(windows)

    # 1) split
    split_labels = [f"[sv{i}]" for i in range(n)]
    parts: list[str] = [f"[0:v]split={n}{''.join(split_labels)}"]

    # 2) 每份 trim + setpts
    out_labels = []
    for i, w in enumerate(windows):
        s_sec = float(w["start_beat"]) * beat_period
        e_sec = float(w["end_beat"]) * beat_period
        f = float(w["factor"])
        if abs(f - 1.0) < 1e-3:
            # 原速：trim + setpts=PTS-STARTPTS（重置 PTS）
            expr = "setpts=PTS-STARTPTS"
        else:
            # 加速 factor 倍：setpts=(PTS-STARTPTS)/(1/factor)
            # 数学：new_pts = (old_pts - STARTPTS) / factor + STARTPTS
            # 即新 PTS 是原 PTS 的 1/factor → 帧时间压缩 → 播放加速 factor 倍
            expr = f"setpts=(PTS-STARTPTS)/{f:.4f}"
        out_label = f"[sv{i}_out]"
        out_labels.append(out_label)
        parts.append(
            f"{split_labels[i]}trim={s_sec:.3f}:{e_sec:.3f},{expr}{out_label}"
        )

    # 3) concat
    concat_in = "".join(out_labels)
    parts.append(
        f"{concat_in}concat=n={n}:v=1:a=0[sped]"
    )

    base = ";\n".join(parts)
    if with_rife:
        # RIFE 插帧需要放在 [sped] 之后
        base = base.replace("[sped]", "[sped_pre]")
        base = base + ";\n[sped_pre]" + RIFE_FILTER_CHAIN + "[sped]"
    return base


def build_afilter_for_window(window_list: list[dict], beat_period: float) -> str:
    """音频 atempo 链：变速窗口同步。"""
    if not window_list:
        return "asetpts=PTS-STARTPTS"

    speed_windows = [
        (float(w["start_beat"]) * beat_period, float(w["end_beat"]) * beat_period,
         float(w["factor"]))
        for w in window_list if abs(float(w["factor"]) - 1.0) >= 1e-3
    ]
    if not speed_windows:
        return "asetpts=PTS-STARTPTS"

    # atempo 不支持 enable=；改用分段 atrim + atempo + concat
    parts: list[str] = []
    last_t = 0.0
    for idx, (s, e, f) in enumerate(speed_windows):
        # 区间前（原速）
        if s > last_t + 0.001:
            parts.append(
                f"[0:a]atrim=0:{s:.3f},asetpts=PTS-STARTPTS[a{idx}_pre]"
            )
        # 区间内（变速：atempo=1/f，ffmpeg atempo 范围 0.5-2.0）
        af = 1.0 / f
        if not (0.5 <= af <= 2.0):
            # 链式 atempo 突破 0.5-2.0 限制（v36 默认 1/1.35=0.74 在范围内）
            af = max(0.5, min(2.0, af))
        parts.append(
            f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS,"
            f"atempo={af:.3f}[a{idx}_sp]"
        )
        last_t = e
    # 区间后（原速）
    parts.append(
        f"[0:a]atrim={last_t:.3f},asetpts=PTS-STARTPTS[a{idx}_post]"
    )
    # concat
    n = len(speed_windows)
    labels = (
        [f"[a{i}_pre]" for i in range(n) if speed_windows[i][0] > (speed_windows[i - 1][1] if i > 0 else 0.0) + 0.001]
        + [f"[a{i}_sp]" for i in range(n)]
        + ["[a{0}_post]".format(n - 1)]
    )
    concat_in = "".join(labels)
    parts.append(
        f"{concat_in}concat=n={len(labels)}:v=0:a=1[aout]"
    )
    return ";\n".join(parts)


def speed_one_shot(clip_path: Path, out_path: Path,
                    rhythm_entry: dict, beat_period: float,
                    *, with_rife: bool = False, dry_run: bool = False) -> str:
    """对单段 shot 应用 rhythm_plan 的 speed_window。

    返回完整 ffmpeg 命令字符串（dry-run 也返回）。
    """
    sw = rhythm_entry.get("speed_window", {}) or {}
    strategy = sw.get("window_strategy", "hold")
    windows = sw.get("windows", []) if strategy != "hold" else []

    filter_complex = build_filter_for_window(windows, beat_period,
                                              with_rife=with_rife)

    if strategy == "hold" or not windows:
        # 单 setpts → 用 -vf 即可
        cmd = [
            "ffmpeg", "-y",
            "-i", str(clip_path),
            "-vf", filter_complex,
            "-an",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        # 多窗口 → filter_complex，最后 map [sped]
        # 把 [sped] 替换成 [vout] 给 -map 用
        fc = filter_complex.replace("[sped]", "[vout]")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(clip_path),
            "-filter_complex", fc,
            "-map", "[vout]",
            "-an",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(out_path),
        ]

    return run(cmd, dry_run=dry_run)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rhythm-plan", default=str(DEFAULT_RHYTHM))
    ap.add_argument("--clips-dir", default=str(DEFAULT_CLIPS),
                    help="输入 shot 视频目录（含 shot01.mp4..shotNN.mp4）")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--with-rife", action="store_true",
                    help="启用 RIFE 补帧（接口保留，当前管线可能不可用）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    plan_path = Path(args.rhythm_plan)
    clips_dir = Path(args.clips_dir)
    out_dir = Path(args.out_dir)

    if not plan_path.exists():
        print(f"ERROR: rhythm_plan 不存在 {plan_path}", file=sys.stderr)
        return 2
    if not args.dry_run and not clips_dir.exists():
        print(f"ERROR: clips_dir 不存在 {clips_dir}", file=sys.stderr)
        return 3

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rhythm_plan = plan.get("rhythm_plan", [])
    beat_period = float(plan.get("beat_period_sec", 0.5))

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[speed-v36] {len(rhythm_plan)} shots, beat_period={beat_period}s",
          flush=True)
    print(f"[speed-v36] clips_dir={clips_dir}", flush=True)
    print(f"[speed-v36] out_dir={out_dir}", flush=True)

    cmds: list[str] = []
    results: list[dict] = []
    for entry in rhythm_plan:
        idx = entry["index"]
        phase = entry["phase"]
        clip_path = clips_dir / f"shot{idx:02d}.mp4"
        out_path = out_dir / f"shot{idx:02d}.mp4"

        # dry-run 时 clip 不必存在
        if not args.dry_run and not clip_path.exists():
            print(f"[speed-v36] WARN: clip 不存在 {clip_path}, 跳过",
                  flush=True)
            results.append({
                "index": idx, "phase": phase, "ok": False,
                "error": "clip missing", "clip_path": str(clip_path),
            })
            continue

        sw = entry.get("speed_window", {}) or {}
        strategy = sw.get("window_strategy", "hold")
        windows = sw.get("windows", [])
        print(f"[speed-v36] shot{idx:02d} phase={phase} "
              f"strategy={strategy} windows={len(windows)}",
              flush=True)

        try:
            cmd_str = speed_one_shot(
                clip_path, out_path, entry, beat_period,
                with_rife=args.with_rife, dry_run=args.dry_run,
            )
            cmds.append(cmd_str)
            results.append({
                "index": idx, "phase": phase, "ok": True,
                "clip_in": str(clip_path),
                "clip_out": str(out_path),
                "speed_window_strategy": strategy,
                "n_windows": len(windows),
            })
        except Exception as e:  # noqa: BLE001
            print(f"[speed-v36] shot{idx:02d} 失败: {e}", flush=True)
            results.append({
                "index": idx, "phase": phase, "ok": False,
                "error": str(e), "clip_path": str(clip_path),
            })

    # 报告
    meta = {
        "compose_phase": "speed_segment_v36",
        "pipeline_version": "v3.6",
        "rhythm_plan_path": str(plan_path),
        "clips_dir": str(clips_dir),
        "out_dir": str(out_dir),
        "with_rife": bool(args.with_rife),
        "beat_period_sec": beat_period,
        "n_shots": len(rhythm_plan),
        "results": results,
        "ffmpeg_cmds": cmds,
        "dry_run": bool(args.dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    report_path = Path(args.report) if args.report else \
        out_dir / "speed_v36_meta.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"[speed-v36] report → {report_path}", flush=True)
    ok_cnt = sum(1 for r in results if r.get("ok"))
    print(f"[speed-v36] done: {ok_cnt}/{len(results)} ok", flush=True)
    return 0 if ok_cnt == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
