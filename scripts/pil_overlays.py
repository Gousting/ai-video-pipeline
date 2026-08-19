#!/usr/bin/env python3
"""用 PIL 直接生成所有 overlay PNG（不依赖 hyperframes）。

从 storyboard 读 overlays[] 和 shot.overlay，调用 PilProvider 渲染 PNG。

CLI:
    python scripts/pil_overlays.py --storyboard D:/ai-video-pipeline/output/same_v1/storyboard_v1.json --out D:/ai-video-pipeline/output/same_v1/overlays
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overlay import PilProvider, REPO_ROOT  # noqa: E402


def render_one(provider: PilProvider, composition: dict, data: dict, out: Path) -> dict:
    try:
        result = provider.render(composition, data, out)
        return {"status": "ok", "output": str(result), "size": result.stat().st_size}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="用 PIL 生成所有 overlay")
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--out", required=True, help="overlay 输出目录")
    args = ap.parse_args(argv)

    sb = json.loads(Path(args.storyboard).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 用 storyboard.composition 解析 composition
    comp_raw = sb.get("composition") or {}
    composition = {
        "width": int(comp_raw.get("width", 720)),
        "height": int(comp_raw.get("height", 1280)),
        "duration": float(sb.get("total_duration", 60.0)),
    }

    provider = PilProvider()
    results = []

    # 1) 顶层 overlays（title-card, end-card）
    for overlay in sb.get("overlays", []) or []:
        oid = overlay.get("id", "global")
        tmpl = overlay.get("template", oid)
        duration = float(overlay.get("duration", 2.0))
        data = dict(overlay.get("data", {}) or {})
        # 用 text/title 字段渲染
        text = data.get("text") or data.get("title") or data.get("credit") or oid
        out_path = out_dir / f"{oid}.png"
        # title-card/end-card 用特殊大字号渲染
        if tmpl in ("title-card", "end-card"):
            r = render_card_pil(provider, composition, data, out_path, tmpl=tmpl)
        else:
            r = render_one(provider, composition, {"text": text, "font_size": 56}, out_path)
        print(f"  {oid} ({tmpl}) dur={duration}s -> {r['status']} {r.get('output', '')}")
        results.append({"id": oid, **r})

    # 2) 每个 shot 的 overlay（subtitle-bar）：从 narration 自动生成
    for shot in sb.get("shots", []) or []:
        idx = shot.get("index")
        text = shot.get("narration", "")
        if not text:
            continue
        out_path = out_dir / f"shot-{int(idx):02d}-subtitle-bar.png"
        r = render_one(provider, composition,
                       {"text": text, "font_size": 56, "bar_ratio": 0.16}, out_path)
        print(f"  shot_{idx:02d}-subtitle-bar text='{text[:30]}...' -> {r['status']} {r.get('output', '')}")
        results.append({"id": f"shot-{int(idx):02d}-subtitle-bar", **r})

    # 写 manifest
    manifest_path = out_dir / "manifest.json"
    manifest = {
        "storyboard_title": sb.get("title"),
        "composition": composition,
        "overlays": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "errors": sum(1 for r in results if r["status"] == "error"),
            "out_dir": str(out_dir),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nmanifest -> {manifest_path}")
    print(f"summary: {manifest['summary']}")
    return 0 if manifest["summary"]["errors"] == 0 else 1


def render_card_pil(provider: PilProvider, composition: dict, data: dict,
                   out: Path, *, tmpl: str = "title-card") -> dict:
    """PIL 渲染 title-card / end-card：全屏背景 + 大字。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        w = composition["width"]
        h = composition["height"]
        bg = (10, 10, 12) if tmpl == "title-card" else (245, 245, 245)
        fg = (255, 255, 255) if tmpl == "title-card" else (15, 15, 15)
        accent = (255, 90, 160)  # hot pink

        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)

        # 字体（优先中文字体）
        def _font(size: int):
            for fp in (r"C:\Windows\Fonts\msyh.ttc",
                       r"C:\Windows\Fonts\simhei.ttf",
                       r"C:\Windows\Fonts\arial.ttf"):
                if Path(fp).is_file():
                    try:
                        return ImageFont.truetype(fp, size)
                    except OSError:
                        continue
            return ImageFont.load_default()

        title = data.get("title", "")
        subtitle = data.get("subtitle", "")
        credit = data.get("credit", "")
        tagline = data.get("tagline", "")

        # 粉/黑 accent bar 左侧
        bar_w = int(w * 0.04)
        bar_h = int(h * 0.02)
        draw.rectangle([(int(w*0.08), int(h*0.35)), (int(w*0.08)+bar_w, int(h*0.35)+bar_h*3)], fill=accent)

        # 主标题（大字，居中偏左）
        title_font = _font(int(h * 0.07))
        # 拆行（按宽度）
        max_w = int(w * 0.75)
        lines = []
        cur = ""
        for ch in title:
            cand = cur + ch
            bbox = draw.textbbox((0, 0), cand, font=title_font)
            if bbox[2] - bbox[0] <= max_w:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        line_h = int(h * 0.085)
        total_h = line_h * len(lines)
        y_start = int(h * 0.4) - total_h // 2
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=title_font)
            tw = bbox[2] - bbox[0]
            x = int((w - tw) / 2)
            draw.text((x, y_start + i * line_h), line, font=title_font, fill=fg)

        # 副标题（小字）
        if subtitle:
            sub_font = _font(int(h * 0.028))
            bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
            tw = bbox[2] - bbox[0]
            x = int((w - tw) / 2)
            draw.text((x, y_start + total_h + 20), subtitle, font=sub_font,
                      fill=(180, 180, 180) if tmpl == "title-card" else (60, 60, 60))

        # credit / tagline（end-card 用）
        if credit and tmpl == "end-card":
            cr_font = _font(int(h * 0.022))
            bbox = draw.textbbox((0, 0), credit, font=cr_font)
            tw = bbox[2] - bbox[0]
            x = int((w - tw) / 2)
            draw.text((x, int(h * 0.85)), credit, font=cr_font, fill=(120, 120, 120))

        if tagline:
            tg_font = _font(int(h * 0.018))
            bbox = draw.textbbox((0, 0), tagline, font=tg_font)
            tw = bbox[2] - bbox[0]
            x = int((w - tw) / 2)
            draw.text((x, int(h * 0.92)), tagline, font=tg_font, fill=(140, 140, 140))

        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG")
        return {"status": "ok", "output": str(out), "size": out.stat().st_size}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    sys.exit(main())
