#!/usr/bin/env python3
"""A 组 8 段批量 T2V 生成（顺序执行，每段独立 seed）。

用法:
  python ab_batch.py                       # 跑全部 8 段
  python ab_batch.py --only 2,3,4          # 只跑指定段
  python ab_batch.py --skip-existing       # 跳过已生成 mp4 的段
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab_t2v_gen import gen_one, SEGMENT_META, OUT_DIR  # noqa: E402


def batch(only: list[int] | None = None, skip_existing: bool = True) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    shots = sorted(SEGMENT_META.keys())
    if only:
        shots = [s for s in shots if s in only]
    t_total = time.time()
    for shot in shots:
        pf = OUT_DIR / f"a_shot{shot:02d}.txt"
        out = OUT_DIR / f"a_shot{shot:02d}.mp4"
        if skip_existing and out.exists() and out.stat().st_size > 100_000:
            print(f"[batch] shot{shot:02d} 已存在 {out.stat().st_size//1024} KB，跳过", flush=True)
            results[shot] = {"ok": True, "skipped": True, "path": str(out)}
            continue
        if not pf.exists():
            print(f"[batch] shot{shot:02d} prompt 文件 {pf} 不存在，跳过", flush=True)
            results[shot] = {"ok": False, "error": "prompt file missing"}
            continue
        try:
            t0 = time.time()
            meta = gen_one(shot, pf, out)
            dt = time.time() - t0
            results[shot] = {"ok": True, "path": str(out), "elapsed_sec": round(dt, 1), "meta": meta}
            print(f"[batch] shot{shot:02d} 完成 累计 {time.time()-t_total:.1f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[batch] shot{shot:02d} 失败: {e}", flush=True)
            results[shot] = {"ok": False, "error": str(e)}

    summary = OUT_DIR / "batch_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_cnt = sum(1 for r in results.values() if r.get("ok"))
    print(f"[batch] 全部完成 {ok_cnt}/{len(results)} 段 总耗时 {time.time()-t_total:.1f}s -> {summary}", flush=True)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="逗号分隔段号，如 2,3,4")
    ap.add_argument("--no-skip", action="store_true", help="不跳过已存在的视频")
    args = ap.parse_args()
    only = [int(x) for x in args.only.split(",") if x.strip()] if args.only else None
    batch(only=only, skip_existing=not args.no_skip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
