#!/usr/bin/env python3
"""P5-v5 尾帧生成：PIL 同源中心裁剪 78% 再 LANCZOS 放大回原尺寸（推近 1.28 倍视角）。

这是 P5-v5 修正空间错乱的关键：尾帧不另外用 Z-Image 生成（v4 错误源），
直接用首帧同源裁剪推近，两张图 100% 同源同构图，锅/柜台/门位置完全一致，
差异只有"机位距离"，落在 H3 FL2VA 官方允许的"机位距离/角度/光线"变化范围内。

CLI:
  python p5_v5_tail.py --first <first.png> --out <last.png> [--crop 0.78]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image


def make_tail(first: Path, out: Path, crop_ratio: float = 0.78) -> dict:
    """中心裁剪 crop_ratio 比例区域，LANCZOS 放大回原尺寸，返回元信息。"""
    im = Image.open(first).convert("RGB")
    w, h = im.size
    # 任务书给定裁剪框：中心 78%（0.11 ~ 0.89）
    inset = (1.0 - crop_ratio) / 2.0
    box = (int(w * inset), int(h * inset), int(w * (1 - inset)), int(h * (1 - inset)))
    tail = im.crop(box).resize((w, h), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    tail.save(out)
    meta = {
        "first_frame": str(first),
        "output": str(out),
        "crop_ratio": crop_ratio,
        "crop_box": list(box),
        "push_in_scale": round(1.0 / crop_ratio, 3),
        "resample": "LANCZOS",
        "size": [w, h],
    }
    meta_path = out.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--crop", type=float, default=0.78)
    args = ap.parse_args(argv)

    meta = make_tail(Path(args.first), Path(args.out), args.crop)
    print(f"[tail] {args.first} -> {args.out} 推近 {meta['push_in_scale']}x (crop {meta['crop_ratio']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
