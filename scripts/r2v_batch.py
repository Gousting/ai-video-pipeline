#!/usr/bin/env python3
"""R2V 批量生成：读 storyboard_v1.json，为每个需要视频的 shot 调用 r2v_video_gen.py。

- 9:16 分辨率（720x1280），每镜头 ~4 秒
- vision-audit 门禁（r2v_review.py review_filmstrip）：score >= 70 自动重跑（最多 3 次）
- 不同 motion 模板交替使用，保证镜头语言丰富

CLI:
    python scripts/r2v_batch.py --storyboard D:/ai-video-pipeline/output/same_v1/storyboard_v1.json
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
R2V_SCRIPT = REPO_ROOT / "scripts" / "r2v_video_gen.py"
R2V_REVIEW_SCRIPT = REPO_ROOT / "scripts" / "r2v_review.py"

# Motion 模板池（output/r2v_test/ 下已有）
MOTION_POOL = [
    REPO_ROOT / "output" / "r2v_test" / "motion_template.mp4",
    REPO_ROOT / "output" / "r2v_test" / "motion_template_legs.mp4",
    REPO_ROOT / "output" / "r2v_test" / "motion_raw_girl.mp4",
    REPO_ROOT / "output" / "r2v_test" / "motion_raw_hike.mp4",
]


def pick_motion(idx: int) -> Path:
    """按 shot index 选 motion（轮转，避免相邻镜头同一模板）。"""
    candidates = [m for m in MOTION_POOL if m.is_file()]
    if not candidates:
        raise FileNotFoundError(f"未找到 motion 模板：{MOTION_POOL}")
    return candidates[idx % len(candidates)]


def run_r2v(*, char: Path, motion: Path, prompt: str, out: Path,
            width: int, height: int, length: int, seed: int | None) -> dict:
    """调一次 r2v_video_gen.py，返回 meta dict。"""
    prompt_file = out.with_suffix(".prompt.txt")
    prompt_file.write_text(prompt, encoding="utf-8")

    cmd = [
        sys.executable, str(R2V_SCRIPT),
        "--char", str(char),
        "--motion", str(motion),
        "--prompt-file", str(prompt_file),
        "--out", str(out),
        "--width", str(width),
        "--height", str(height),
        "--length", str(length),
        "--steps", "20",
        "--prefix", f"r2v_{out.stem}",
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    print(f"[r2v] shot_{out.stem}: {' '.join(cmd[2:8])}...", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=2400)
    dt = time.time() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"r2v_video_gen.py 失败 rc={proc.returncode}\n"
                           f"stdout: {proc.stdout[-1500:]}\n"
                           f"stderr: {proc.stderr[-1500:]}")
    meta_path = out.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    meta["elapsed_sec"] = round(dt, 1)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[r2v] shot_{out.stem} 完成 ({dt:.1f}s) seed={meta.get('seed')}", flush=True)
    return meta


def review_clip(clip: Path, ref_char: Path | None = None) -> dict:
    """调 r2v_review.py review_filmstrip 对生成的 R2V 镜头打分。"""
    cmd = [sys.executable, str(R2V_REVIEW_SCRIPT), "review_filmstrip",
           "--video", str(clip), "--n", "4"]
    if ref_char:
        cmd += ["--ref", str(ref_char)]
    print(f"[audit] {clip.name} ...", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0:
        return {"pass": False, "score": 0, "error": f"VLM 审查失败: {proc.stderr[-500:]}"}
    # 解析最后一段 JSON 输出
    out = proc.stdout.strip()
    try:
        review = json.loads(out)
    except json.JSONDecodeError:
        # 取最后一行大括号
        start = out.rfind("{")
        end = out.rfind("}")
        if start != -1 and end > start:
            review = json.loads(out[start:end + 1])
        else:
            review = {"pass": False, "score": 0, "error": f"VLM 输出无法解析: {out[:300]}"}
    review_path = clip.with_suffix(".review.json")
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[audit] {clip.name} score={review.get('score')} pass={review.get('pass')}", flush=True)
    return review


def gen_one_shot(shot: dict, *, char_ref: Path, motion: Path, width: int, height: int,
                 length: int, retries: int = 3) -> tuple[Path, dict]:
    """生成一个镜头；score<70 自动重跑不同 seed 最多 3 次。"""
    idx = shot["index"]
    out = shot["__out_path"]
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = shot["__r2v_prompt"]

    for attempt in range(1, retries + 1):
        seed = random.randint(0, int(1e9))
        try:
            run_r2v(char=char_ref, motion=motion, prompt=prompt, out=out,
                    width=width, height=height, length=length, seed=seed)
        except RuntimeError as e:
            print(f"[r2v] shot_{idx} 第 {attempt} 次失败：{e}", flush=True)
            continue
        # vision-audit
        review = review_clip(out, ref_char=char_ref)
        if review.get("pass"):
            return out, review
        print(f"[r2v] shot_{idx} score={review.get('score')} < 70，"
              f"第 {attempt}/{retries} 次重试", flush=True)
    # 所有 retries 都失败，返回最后一次结果
    return out, review


def build_shot_r2v_prompt(shot: dict) -> str:
    """把 storyboard shot 转成 R2V prompt（保留 visual 描述 + 运镜 + 风格约束）。"""
    visual = shot.get("visual", "").strip()
    action = shot.get("action", "").strip()
    mood = shot.get("mood", "").strip()
    parts = [visual]
    if action:
        parts.append(f"Camera: {action}")
    if mood:
        parts.append(f"Mood: {mood}")
    parts.append("anime PV aesthetic, high contrast, 9:16 portrait")
    return ", ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R2V 批量生成")
    ap.add_argument("--storyboard", required=True, help="storyboard.json 路径")
    ap.add_argument("--width", type=int, default=720, help="视频宽度（9:16=720）")
    ap.add_argument("--height", type=int, default=1280, help="视频高度（9:16=1280）")
    ap.add_argument("--length", type=int, default=97, help="每镜头帧数（24fps=4s=96 帧，+1 兼容）")
    ap.add_argument("--retries", type=int, default=3, help="vision-audit 不通过时的重试次数")
    ap.add_argument("--shots", default="", help="指定 shot 索引列表（逗号分隔，空=全部）")
    args = ap.parse_args(argv)

    sb_path = Path(args.storyboard)
    sb = json.loads(sb_path.read_text(encoding="utf-8"))
    same_root = sb_path.parent
    clips_dir = same_root / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # 决定哪些 shot 需要 R2V：first_frame_ref != '无' / 'none' 的需要视频
    target_indices = set()
    if args.shots:
        target_indices = {int(x) for x in args.shots.split(",") if x.strip()}
    else:
        for s in sb.get("shots", []):
            ref = (s.get("first_frame_ref") or "").strip()
            if ref and ref not in ("无", "none", "", "None"):
                target_indices.add(s["index"])

    # 准备角色 ref 路径
    senior_ref = same_root / "char_pack" / "senior" / "senior_ref.png"
    junior_ref = same_root / "char_pack" / "junior" / "junior_ref.png"

    results = []
    for s in sb.get("shots", []):
        idx = s["index"]
        if idx not in target_indices:
            print(f"[skip] shot_{idx}: 无 first_frame_ref（标题/字幕卡类）", flush=True)
            continue

        ref_str = s.get("first_frame_ref", "")
        is_senior = "senior" in ref_str.lower() or "学姐" in ref_str
        char_ref = senior_ref if is_senior else junior_ref
        if not char_ref.is_file():
            print(f"[skip] shot_{idx}: char ref 不存在 {char_ref}", flush=True)
            continue

        # 计算镜头帧数（按 duration 算，24fps）
        duration = float(s.get("duration", 4.0))
        length = max(48, int(duration * 24))  # 至少 48 帧（2s）

        out = clips_dir / f"shot{idx:02d}.mp4"
        motion = pick_motion(idx)
        r2v_prompt = build_shot_r2v_prompt(s)

        print(f"\n=== shot_{idx} (length={length}f, duration={duration}s) ===", flush=True)
        print(f"  char={char_ref.name} motion={motion.name}", flush=True)
        print(f"  prompt={r2v_prompt[:150]}...", flush=True)

        try:
            clip, review = gen_one_shot(
                {**s, "__out_path": out, "__r2v_prompt": r2v_prompt},
                char_ref=char_ref, motion=motion,
                width=args.width, height=args.height, length=length, retries=args.retries,
            )
            results.append({
                "index": idx,
                "clip": str(clip),
                "duration": duration,
                "length": length,
                "review_score": review.get("score"),
                "review_pass": review.get("pass"),
                "review_verdict": review.get("opinion", review.get("consistency", "")),
                "elapsed_sec": review.get("elapsed_sec"),
            })
        except Exception as e:  # noqa: BLE001
            print(f"[fail] shot_{idx}: {e}", flush=True)
            results.append({"index": idx, "clip": None, "error": str(e)[:500]})

    # 写 manifest
    manifest = {
        "storyboard": str(sb_path),
        "width": args.width, "height": args.height,
        "results": results,
        "n_pass": sum(1 for r in results if r.get("review_pass")),
        "n_total": len(results),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    manifest_path = same_root / "r2v_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 总结: {manifest['n_pass']}/{manifest['n_total']} 通过 ===", flush=True)
    print(f"manifest: {manifest_path}", flush=True)
    return 0 if manifest["n_pass"] == manifest["n_total"] else 1


if __name__ == "__main__":
    sys.exit(main())
