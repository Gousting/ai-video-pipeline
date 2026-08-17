#!/usr/bin/env python3
"""AI 视频流水线 Phase B：overlay 批量渲染。

读 storyboard.json → 对每个 shot 的 `overlay` 字段和顶层 `overlays` 列表
→ 按 overlay.engine 调对应 provider → 输出到 output/overlays/。

CLI:
    python scripts/render_overlays.py <storyboard.json> [--out DIR] [--engine default]
    python scripts/render_overlays.py --demo  # 内置 demo storyboard（不读文件，方便自检）

overlay 字段 schema（推荐；缺字段会跳过）：
    {
      "engine":   "pil" | "hyperframes" | "remotion"   (必填)
      "template": "title-card" | "subtitle-bar" | "lower-third" | "end-card" | "transition"  (HyperFrames 必填)
      "data":     { ... }                              (模板占位符与 provider 参数)
    }

顶层 overlays[] schema：
    { "id": "...", "engine": "...", "template": "...", "data": {...}, "duration": N }
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

try:
    from overlay import (
        DEFAULT_OUTPUT_DIR,
        REPO_ROOT,
        get_provider,
    )
except ImportError:  # 包内调用兼容
    from scripts.overlay import (
        DEFAULT_OUTPUT_DIR,
        REPO_ROOT,
        get_provider,
    )


# ---------------------------------------------------------------------------
# 默认 composition（宽高 / fps）和兜底 engine
# ---------------------------------------------------------------------------

DEFAULT_COMPOSITION = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
}

# 默认 engine：PilProvider 兜底，HyperFrames 失败时自动降级。
DEFAULT_ENGINE = "pil"


def _build_composition(shot: dict | None = None, duration: float | None = None) -> dict:
    """合并默认 composition 与 shot 自带字段（duration / width / height）。"""
    comp = dict(DEFAULT_COMPOSITION)
    if shot:
        if "duration" in shot:
            comp["duration"] = float(shot["duration"])
        if "width" in shot:
            comp["width"] = int(shot["width"])
        if "height" in shot:
            comp["height"] = int(shot["height"])
    if duration is not None:
        comp["duration"] = float(duration)
    return comp


# ---------------------------------------------------------------------------
# 单条 overlay 渲染
# ---------------------------------------------------------------------------


def _render_one_overlay(
    overlay: dict,
    composition: dict,
    out_dir: Path,
    name_hint: str,
    fallback_engine: str,
) -> dict:
    """渲染一条 overlay，返回执行报告 dict。"""
    engine = (overlay.get("engine") or "").lower() or fallback_engine
    template = overlay.get("template")
    data = dict(overlay.get("data") or {})

    report: dict = {
        "name": name_hint,
        "engine_requested": engine,
        "template": template,
        "status": "pending",
        "output": None,
        "duration_s": None,
        "note": "",
    }
    started = time.time()
    try:
        # HyperFrames 渲染需要知道当前是哪个模板；自动注入到 data 方便 provider 取。
        if engine == "hyperframes" and template and "template" not in data:
            data["template"] = template

        # 1) 先按请求 engine 试一次
        provider = get_provider(engine)
        # 决定输出扩展名
        ext = ".mp4" if engine == "hyperframes" else ".png"
        out_path = out_dir / f"{name_hint}.{ext.lstrip('.')}"
        result = provider.render(composition, data, out_path)
        report["status"] = "ok"
        report["output"] = str(result)
    except Exception as exc:  # noqa: BLE001 - 统一兜底，记录详细信息
        report["status"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["trace"] = traceback.format_exc(limit=4)
        # 2) HyperFrames 失败时按 fallback_engine（默认 pil）兜底再试一次
        if engine == "hyperframes" and fallback_engine != engine:
            try:
                fb_provider = get_provider(fallback_engine)
                fb_data = dict(data)
                # PIL provider 只用 text 字段；其他字段忽略。
                if template:
                    fb_data.setdefault("text", _default_text_for_template(template, data))
                fb_ext = ".png"
                fb_path = out_dir / f"{name_hint}.png"
                fb_result = fb_provider.render(composition, fb_data, fb_path)
                report["status"] = "fallback_ok"
                report["fallback_engine"] = fallback_engine
                report["output"] = str(fb_result)
                report["note"] = f"HyperFrames 不可用，已降级到 {fallback_engine}: {exc}"
            except Exception as fb_exc:  # noqa: BLE001
                report["fallback_error"] = f"{type(fb_exc).__name__}: {fb_exc}"
    report["duration_s"] = round(time.time() - started, 3)
    return report


def _default_text_for_template(template: str, data: dict) -> str:
    """PIL 兜底时按模板挑默认文本，避免输出空字幕。"""
    if template in ("title-card", "end-card"):
        return str(data.get("title") or data.get("text") or template)
    if template == "subtitle-bar":
        return str(data.get("text") or data.get("title") or "（无字幕）")
    if template == "lower-third":
        name = data.get("name") or ""
        caption = data.get("caption") or ""
        return f"{name} {caption}".strip() or "（无名）"
    if template == "transition":
        return str(data.get("label") or "·")
    return str(data.get("text") or data.get("title") or template)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def render_storyboard_overlays(
    storyboard: dict,
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    fallback_engine: str = DEFAULT_ENGINE,
) -> dict:
    """遍历 storyboard 全局 overlays + 每个 shot 的 overlay，逐个渲染。

    返回汇总报告 dict（含每个 overlay 的执行结果 + 统计）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 准备一个简易 manifest，方便后续 ffmpeg 合成阶段读取。
    manifest: dict = {
        "storyboard_title": storyboard.get("title"),
        "composition": DEFAULT_COMPOSITION,
        "overlays": [],
    }

    # 1) 全局 overlays（标题卡 / 片尾卡通常放这里）
    for i, overlay in enumerate(storyboard.get("overlays") or []):
        hint_id = str(overlay.get("id") or f"global-{i:02d}")
        comp = _build_composition(
            shot={"duration": overlay.get("duration", 4.0)},
            duration=overlay.get("duration", 4.0),
        )
        report = _render_one_overlay(overlay, comp, out_dir, hint_id, fallback_engine)
        manifest["overlays"].append({"scope": "global", "id": hint_id, **report})

    # 2) 每个 shot 的 overlay 字段
    for shot in storyboard.get("shots") or []:
        if "overlay" not in shot or shot["overlay"] is None:
            continue
        idx = shot.get("index", 0)
        overlay = shot["overlay"] if isinstance(shot["overlay"], dict) else {"engine": shot["overlay"]}
        template = overlay.get("template", "subtitle-bar")
        hint_id = f"shot-{idx:02d}-{template}"
        comp = _build_composition(shot=shot)
        report = _render_one_overlay(overlay, comp, out_dir, hint_id, fallback_engine)
        manifest["overlays"].append(
            {"scope": "shot", "shot_index": idx, "id": hint_id, **report}
        )

    # 统计
    total = len(manifest["overlays"])
    ok = sum(1 for o in manifest["overlays"] if o["status"] in ("ok", "fallback_ok"))
    fallback = sum(1 for o in manifest["overlays"] if o["status"] == "fallback_ok")
    manifest["summary"] = {"total": total, "ok": ok, "fallback": fallback, "out_dir": str(out_dir)}

    # 把 manifest 也写到 out_dir
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


# ---------------------------------------------------------------------------
# demo 数据：当 storyboard 没写 overlay 字段时也能跑通整条链路
# ---------------------------------------------------------------------------


def build_demo_storyboard(base: dict | None = None) -> dict:
    """在基础 storyboard 上叠一层 demo overlay（标题卡 / 字幕条 / 片尾卡）。

    用来自检：原样跑 sample_storyboard.json 也能让 PilProvider / HyperFramesProvider 至少
    各自跑一遍。
    """
    storyboard = json.loads(json.dumps(base or {}))
    storyboard.setdefault("overlays", [])

    # 标题卡：HyperFrames 优先；不可用时降级 PIL
    storyboard["overlays"].append(
        {
            "id": "title-card",
            "engine": "hyperframes",
            "template": "title-card",
            "data": {
                "title": storyboard.get("title", "Untitled"),
                "subtitle": "Phase B · Demo",
                "tagline": "OVERLAY WRAPPER",
                "quality": "draft",
            },
            "duration": 4.0,
        }
    )

    # 每个 shot 的字幕条：PIL 兜底足够
    for shot in storyboard.get("shots", []):
        if not shot.get("narration"):
            continue
        shot["overlay"] = {
            "engine": "pil",
            "template": "subtitle-bar",
            "data": {"text": shot["narration"]},
        }

    # 片尾卡：HyperFrames
    storyboard["overlays"].append(
        {
            "id": "end-card",
            "engine": "hyperframes",
            "template": "end-card",
            "data": {
                "title": storyboard.get("title", "Untitled"),
                "credit": "Rendered by ai-video-pipeline / Phase B",
                "tagline": "END",
                "quality": "draft",
            },
            "duration": 4.0,
        }
    )
    return storyboard


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_storyboard(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="渲染 storyboard 的 overlay 字段（pil / hyperframes）。"
    )
    parser.add_argument(
        "storyboard",
        nargs="?",
        default=str(REPO_ROOT / "examples" / "sample_storyboard.json"),
        help="storyboard JSON 路径（默认 examples/sample_storyboard.json）",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR), help="输出目录（默认 output/overlays/）")
    parser.add_argument(
        "--fallback-engine",
        default=DEFAULT_ENGINE,
        choices=("pil", "hyperframes"),
        help="HyperFrames 失败时的兜底 engine（默认 pil）",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="在原 storyboard 上叠 demo overlay（标题卡 + 每个 shot 字幕 + 片尾卡），便于自检",
    )
    args = parser.parse_args(argv)

    sb_path = Path(args.storyboard)
    if not sb_path.is_file():
        sys.stderr.write(f"错误：storyboard 文件不存在 - {sb_path}\n")
        return 2
    storyboard = _load_storyboard(sb_path)
    if args.demo:
        storyboard = build_demo_storyboard(storyboard)

    out_dir = Path(args.out)
    manifest = render_storyboard_overlays(
        storyboard,
        out_dir=out_dir,
        fallback_engine=args.fallback_engine,
    )

    summary = manifest["summary"]
    print(
        f"[render_overlays] 总数 {summary['total']} | 成功 {summary['ok']} "
        f"| 兜底 {summary['fallback']} | out={summary['out_dir']}"
    )
    for ov in manifest["overlays"]:
        line = (
            f"  - {ov['id']}: engine={ov['engine_requested']} "
            f"template={ov.get('template')} status={ov['status']}"
        )
        if ov.get("output"):
            line += f" -> {ov['output']}"
        if ov.get("note"):
            line += f"  ({ov['note']})"
        if ov.get("error"):
            line += f"\n      ERROR: {ov['error']}"
        print(line)

    # exit code：有 error 但至少兜底跑通，仍按 0；完全失败再 1。
    any_fatal = any(o["status"] == "error" for o in manifest["overlays"])
    return 1 if any_fatal else 0


if __name__ == "__main__":
    sys.exit(main())