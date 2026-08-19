#!/usr/bin/env python3
"""same_v2 专用 R2V 批量生成：12 个独立 base 各 5s（124 帧），配 vision-audit 门禁。

对比 same_v1_r2v（v1 一次成 4 个 base 且环节薄弱）：
- v2 显式 requirement：每角色 5+ 个差异化 base（v1 仅 2）
- v2 强制 124 帧（v1 97 帧 ≈ 4s，动作幅度小）
- v2 配饰锁：每个 prompt 重复 3 个关键配饰
- v2 audit 门禁：score < 70 换 seed 重跑（最多 3 次）

CLI:
    python scripts/same_v2_r2v.py --storyboard D:/ai-video-pipeline/output/same_v2/storyboard_v2.json

输出：
    output/same_v2/clips/<base_id>.mp4
    output/same_v2/clips/<base_id>.audit.json
    output/same_v2/r2v_manifest.json
"""
from __future__ import annotations

# Keep the wrapper's console output safe on Windows hosts whose default
# stdout codec is GBK (the child renderer may emit Unicode checkmarks).
# This script is also run from PowerShell, so do not rely on the locale
# inherited by the parent process.
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

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

# Motion 模板池（沿用 v1 验证通过的 4 个）
MOTION_POOL = [
    REPO_ROOT / "output" / "r2v_test" / "motion_template.mp4",
    REPO_ROOT / "output" / "r2v_test" / "motion_template_legs.mp4",
    REPO_ROOT / "output" / "r2v_test" / "motion_raw_girl.mp4",
    REPO_ROOT / "output" / "r2v_test" / "motion_raw_hike.mp4",
]

# Retried/quality-sensitive shots use the clean legs template. It is the
# only motion reference with a VLM-confirmed, isolated walking signal; the
# character identity and the shot prompt still provide the requested gesture.
MOTION_OVERRIDES = {
    "senior_full": "motion_template_legs.mp4",
    "junior_eye_smile": "motion_template_legs.mp4",
    "junior_full_wave": "motion_template_legs.mp4",
    "junior_half_heart": "motion_template_legs.mp4",
}


def available_motions() -> list[Path]:
    return [m for m in MOTION_POOL if m.is_file()]


def pick_motion_for_base(base_id: str, motions: list[Path]) -> Path:
    """按 base_id 分配 motion；质量敏感镜头优先使用已验证的干净步态模板。"""
    if not motions:
        raise FileNotFoundError(f"未找到 motion 模板：{MOTION_POOL}")
    override = MOTION_OVERRIDES.get(base_id)
    if override:
        for motion in motions:
            if motion.name == override:
                return motion
    idx = sum(ord(c) for c in base_id) % len(motions)
    return motions[idx]


def run_r2v(
    *, char: Path, motion: Path, prompt: str, out: Path,
    width: int, height: int, length: int, steps: int, seed: int | None,
) -> dict:
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
        "--steps", str(steps),
        "--prefix", f"r2v_v2_{out.stem}",
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    print(f"  [r2v] {out.stem}: gen (motion={motion.name}, length={length}f, steps={steps})", flush=True)
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=2400,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"r2v 超时 2400s: {exc}")
    dt = time.time() - t0
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"r2v_video_gen.py 失败 rc={proc.returncode}\n{tail}")
    meta_path = out.with_suffix(".json")
    meta: dict = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    meta["elapsed_sec"] = round(dt, 1)
    meta["prompt"] = prompt
    meta["motion"] = motion.name
    meta["seed"] = seed
    meta["length"] = length
    meta["steps"] = steps
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [r2v] {out.stem} OK ({dt:.1f}s) seed={seed}", flush=True)
    return meta


def review_clip(clip: Path, ref_char: Path | None = None) -> dict:
    """调 r2v_review.py review_filmstrip 对生成的 R2V 镜头打分。"""
    cmd = [sys.executable, str(R2V_REVIEW_SCRIPT), "review_filmstrip",
           "--video", str(clip), "--n", "4"]
    if ref_char:
        cmd += ["--ref", str(ref_char)]
    print(f"  [audit] {clip.name} ...", flush=True)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"pass": False, "score": 0, "error": "VLM 审查超时 300s"}
    if proc.returncode != 0:
        return {"pass": False, "score": 0, "error": f"VLM 审查失败: {proc.stderr[-500:]}"}
    out = proc.stdout.strip()
    try:
        review = json.loads(out)
    except json.JSONDecodeError:
        start = out.rfind("{")
        end = out.rfind("}")
        if start != -1 and end > start:
            try:
                review = json.loads(out[start:end + 1])
            except json.JSONDecodeError:
                review = {"pass": False, "score": 0, "error": f"VLM 输出无法解析: {out[:300]}"}
        else:
            review = {"pass": False, "score": 0, "error": f"VLM 输出无法解析: {out[:300]}"}
    review_path = clip.with_suffix(".audit.json")
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [audit] {clip.name} score={review.get('score')} pass={review.get('pass')}", flush=True)
    return review


def gen_one_base(
    *, base_id: str, char_ref: Path, motion: Path, prompt: str,
    out: Path, width: int, height: int, length: int, steps: int,
    retries: int = 3, audit_threshold: int = 70,
) -> tuple[Path, dict, list[dict]]:
    """为单个 base 生成视频，多次重试直到审计通过或 retry 耗尽。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    attempts: list[dict] = []
    last_review: dict = {}
    for attempt in range(1, retries + 1):
        seed = random.randint(0, int(1e9))
        print(f"\n=== {base_id} 第 {attempt}/{retries} 次 (seed={seed}) ===", flush=True)
        try:
            run_r2v(char=char_ref, motion=motion, prompt=prompt, out=out,
                    width=width, height=height, length=length, steps=steps, seed=seed)
        except RuntimeError as e:
            print(f"  [r2v] {base_id} 第 {attempt} 次生成失败：{e}", flush=True)
            attempts.append({"attempt": attempt, "seed": seed, "error": str(e)[:500]})
            continue
        # vision-audit
        review = review_clip(out, ref_char=char_ref)
        last_review = review
        score = int(review.get("score") or 0)
        # score is the hard gate requested by same_v2, while the three
        # film-strip consistency flags are a secondary quality gate. A
        # high-scoring clip is not accepted when the reviewer explicitly
        # reports a broken action or character lock.
        audit_ok = all(review.get(key) is True for key in (
            "character_consistent", "motion_continuous", "spatial_stable"
        ))
        passed = bool(review.get("pass")) or (score >= audit_threshold and audit_ok)
        attempts.append({
            "attempt": attempt,
            "seed": seed,
            "score": score,
            "pass": passed,
            "review_verdict": review.get("opinion", review.get("consistency", "")),
        })
        if passed:
            return out, review, attempts
        print(f"  [r2v] {base_id} score={score} < {audit_threshold}，第 {attempt}/{retries} 次重试", flush=True)
    # 全部失败，返回最后一次结果
    return out, last_review, attempts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="same_v2 R2V 批量生成 12 个独立 base")
    ap.add_argument("--storyboard", required=True, help="storyboard_v2.json 路径")
    ap.add_argument("--width", type=int, default=720, help="视频宽度（9:16=720）")
    ap.add_argument("--height", type=int, default=1280, help="视频高度（9:16=1280）")
    ap.add_argument("--length", type=int, default=124, help="每镜头帧数（24fps=5s=120 帧，+4 兼容）")
    ap.add_argument("--steps", type=int, default=20, help="采样步数（v1=20，本轮保持）")
    ap.add_argument("--retries", type=int, default=3, help="audit 不通过时的重试次数")
    ap.add_argument("--audit-threshold", type=int, default=70, help="audit 通过分数阈值")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="输出已存在就跳过（默认开）")
    ap.add_argument("--only-bases", default="",
                    help="只生成指定 base_id（逗号分隔），空=全部")
    args = ap.parse_args(argv)

    sb_path = Path(args.storyboard).resolve()
    sb = json.loads(sb_path.read_text(encoding="utf-8"))
    same_root = sb_path.parent
    clips_dir = same_root / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    senior_ref = same_root / "char_pack" / "senior" / "senior_ref.png"
    junior_ref = same_root / "char_pack" / "junior" / "junior_ref.png"

    # 收集每个 base_id 的代表性 shot（用第一个匹配的 shot）
    base_to_shot: dict[str, dict] = {}
    for s in sb.get("shots", []):
        base_id = s.get("base_id", "").strip()
        if not base_id or base_id == "none":
            continue
        if base_id in base_to_shot:
            continue
        base_to_shot[base_id] = s

    # 过滤 only-bases
    if args.only_bases:
        wanted = {b.strip() for b in args.only_bases.split(",") if b.strip()}
        base_to_shot = {k: v for k, v in base_to_shot.items() if k in wanted}

    motions = available_motions()
    if not motions:
        print(f"ERROR: 未找到 motion 模板: {MOTION_POOL}", flush=True)
        return 2

    print(f"待生成 base 数: {len(base_to_shot)}", flush=True)
    print(f"可用 motion: {[m.name for m in motions]}", flush=True)
    print(f"参数: {args.width}x{args.height} length={args.length} steps={args.steps} "
          f"retries={args.retries} audit>={args.audit_threshold}", flush=True)

    # 按 base_id 排序，确定可复现顺序
    sorted_bases = sorted(base_to_shot.keys())
    results: list[dict] = []
    overall_t0 = time.time()

    for base_id in sorted_bases:
        shot = base_to_shot[base_id]
        char_str = shot.get("first_frame_ref", "")
        is_senior = ("senior" in char_str.lower()) or ("学姐" in char_str)
        char_ref = senior_ref if is_senior else junior_ref
        if not char_ref.is_file():
            print(f"[skip] {base_id}: char ref 缺失 {char_ref}", flush=True)
            results.append({"base_id": base_id, "clip": None, "error": "char ref missing"})
            continue

        out = clips_dir / f"{base_id}.mp4"
        if args.skip_existing and out.is_file() and out.stat().st_size > 100_000:
            existing_audit = out.with_suffix(".audit.json")
            if existing_audit.is_file():
                try:
                    er = json.loads(existing_audit.read_text(encoding="utf-8"))
                    score = int(er.get("score") or 0)
                    review_pass = er.get("pass")
                    if score >= args.audit_threshold and (review_pass is not False):
                        print(f"[skip] {base_id}: 已存在且 audit {score} >= {args.audit_threshold}", flush=True)
                        results.append({
                            "base_id": base_id,
                            "clip": str(out),
                            "score": int(er.get("score") or 0),
                            "pass": True,
                            "skipped": True,
                        })
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass

        prompt = shot.get("r2v_prompt", "").strip()
        if not prompt:
            print(f"[skip] {base_id}: r2v_prompt 为空", flush=True)
            results.append({"base_id": base_id, "clip": None, "error": "empty prompt"})
            continue

        motion = pick_motion_for_base(base_id, motions)
        t0 = time.time()
        try:
            clip, review, attempts = gen_one_base(
                base_id=base_id, char_ref=char_ref, motion=motion, prompt=prompt,
                out=out, width=args.width, height=args.height,
                length=args.length, steps=args.steps,
                retries=args.retries, audit_threshold=args.audit_threshold,
            )
            elapsed = round(time.time() - t0, 1)
            results.append({
                "base_id": base_id,
                "clip": str(clip),
                "score": int(review.get("score") or 0),
                "pass": bool(review.get("pass")) or (int(review.get("score") or 0) >= args.audit_threshold),
                "attempts": attempts,
                "elapsed_sec": elapsed,
            })
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {base_id}: {e}", flush=True)
            results.append({"base_id": base_id, "clip": None, "error": str(e)[:500]})

    overall_elapsed = round(time.time() - overall_t0, 1)
    n_pass = sum(1 for r in results if r.get("pass"))
    n_total = len(results)
    manifest = {
        "storyboard": str(sb_path),
        "width": args.width, "height": args.height,
        "length": args.length, "steps": args.steps,
        "audit_threshold": args.audit_threshold,
        "results": results,
        "n_pass": n_pass,
        "n_total": n_total,
        "overall_elapsed_sec": overall_elapsed,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    manifest_path = same_root / "r2v_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 总结: {n_pass}/{n_total} 通过 ===", flush=True)
    print(f"总耗时: {overall_elapsed}s ({overall_elapsed / 60:.1f} min)", flush=True)
    print(f"manifest: {manifest_path}", flush=True)
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
