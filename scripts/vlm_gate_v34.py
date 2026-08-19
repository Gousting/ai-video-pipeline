#!/usr/bin/env python3
"""v3.4 VLM 门禁审核脚本（P3，可复用工具）。

vs v3.3 line0 关键差异（per 任务书 v3.4）：

- 检查项从 3 项扩到 3 项但语义改：
    a. 构图完整性（composition_integrity）
       - 终幅构图完整、主体不裁半（与 P3 well-framed composition 对应）
    b. 动作自然度（motion_naturalness）
       - 无明显拖沓/僵硬（与 P2 单动作链 + then 表对应）
    c. 音乐衔接（music_transition）
       - 合并片视角：节拍对齐、无刺耳突兀（与 P1 BGM 后期化对应）
- 输入：图片集路径（*.jpg）或视频路径（自动抽帧）
- 输出：pass/fail + 原因 JSON + Markdown 报告
- 支持 `--fake-input`：不调真实 VLM，用内置 fake 输入快速自检
- 不调用真实 VLM（per 任务硬性禁止）：仅提供脚本与 fake 自检实现

CLI:
  python vlm_gate_v34.py --video clips/concat_with_bgm_v34.mp4 \
                         --fake-input --out output/pipeline_v34/qa/vlm_gate/v34.json
  python vlm_gate_v34.py --images frame_*.jpg --fake-input \
                         --out output/pipeline_v34/qa/vlm_gate/v34.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
DEFAULT_OUT_JSON = ROOT / "output" / "pipeline_v34" / "qa" / "vlm_gate" / "v34_vlm_gate.json"
DEFAULT_OUT_MD = ROOT / "output" / "pipeline_v34" / "qa" / "vlm_gate" / "v34_vlm_gate.md"
DEFAULT_FRAMES_DIR = ROOT / "output" / "pipeline_v34" / "qa" / "vlm_gate" / "frames"

# 检查项 v3.4
V34_CHECKS = [
    "composition_integrity",  # 构图完整性
    "motion_naturalness",     # 动作自然度
    "music_transition",       # 音乐衔接
]


def collect_inputs_from_images(images: list[Path]) -> list[dict]:
    """接受 image 列表 → 返回 [{path, kind: 'image', index}]."""
    return [{"path": str(p), "kind": "image", "index": i}
            for i, p in enumerate(sorted(images))]


def collect_inputs_from_video(video: Path) -> list[dict]:
    """接受 video 路径 → 返回 [{path, kind: 'video', index}].

    真实实现会抽关键帧并跑 VLM；fake 模式只返回占位。
    """
    return [{"path": str(video), "kind": "video", "index": 0}]


def fake_vlm_check(inputs: list[dict]) -> dict:
    """不调用真实 VLM：用内置 fake 输入快速自检（per 任务硬性禁止）。

    行为：根据 inputs 数量生成确定性结果；用于脚本冒烟测试 + CI 自检。
    """
    n = len(inputs)
    if n == 0:
        return {
            "available": False,
            "overall_pass": False,
            "checks": {c: {"pass": False, "reason": "no inputs"} for c in V34_CHECKS},
            "score": 0,
            "opinion": "no inputs",
            "fake": True,
        }
    # 确定性假结果：只要 inputs 非空，全部 pass
    return {
        "available": True,
        "overall_pass": True,
        "checks": {
            "composition_integrity": {
                "pass": True,
                "reason": "fake: subject framed fully, both eyes visible, "
                            "no half-face cropping",
                "severity": "none",
            },
            "motion_naturalness": {
                "pass": True,
                "reason": "fake: motion snappy (~1.5s per action), no dragging",
                "severity": "none",
            },
            "music_transition": {
                "pass": True,
                "reason": "fake: 120 BPM grid aligned, no jarring whoosh at cuts",
                "severity": "none",
            },
        },
        "score": 80,
        "opinion": "fake-input self-test passed (no real VLM invoked)",
        "fake": True,
    }


def write_markdown(result: dict, out_md: Path) -> None:
    lines: list[str] = []
    lines.append("# v3.4 VLM Gate Report")
    lines.append("")
    lines.append(f"- Generated: {result.get('generated_at', '')}")
    lines.append(f"- Mode: **{'FAKE INPUT (no real VLM)' if result.get('fake') else 'REAL VLM'}**")
    lines.append(f"- Inputs: {result.get('n_inputs', 0)}")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    icon = "✅" if result.get("overall_pass") else "❌"
    lines.append(f"{icon}  Overall: **{'PASS' if result.get('overall_pass') else 'FAIL'}**")
    lines.append(f"- Score: {result.get('score', 0)}/100")
    lines.append(f"- Opinion: {result.get('opinion', '')}")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | Pass | Severity | Reason |")
    lines.append("|-------|------|----------|--------|")
    for c in V34_CHECKS:
        chk = result["checks"].get(c, {})
        icon_c = "✅" if chk.get("pass") else "❌"
        lines.append(
            f"| {c} | {icon_c} | {chk.get('severity', '?')} | {chk.get('reason', '')} |"
        )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    for inp in result.get("inputs", []):
        lines.append(f"- `{inp.get('path', '')}` ({inp.get('kind', '')} #{inp.get('index', 0)})")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("v3.4 VLM 门禁审核（可复用工具）。本任务**只实现脚本**，不调用真实 VLM：")
    lines.append("")
    lines.append("- `composition_integrity`（构图完整性）：终幅构图完整、主体不裁半")
    lines.append("  - 与 P3 well-framed composition 对应（`the camera pulls back to frame "
                 "the ENTIRE head and shoulders, both eyes clearly visible`）")
    lines.append("- `motion_naturalness`（动作自然度）：无明显拖沓/僵硬")
    lines.append("  - 与 P2 单动作链 + then 表对应（`fast, snappy, completes in ~1.5s`）")
    lines.append("- `music_transition`（音乐衔接）：合并片视角节拍对齐、无刺耳突兀")
    lines.append("  - 与 P1 BGM 后期化对应（`120 BPM beat grid, ≤41ms alignment`）")
    lines.append("")
    lines.append("`--fake-input` 模式：跳过 VLM 调用，用确定性假结果验证脚本链路 + 报告格式。")
    lines.append("")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=None, help="视频路径（与 --images 二选一）")
    ap.add_argument("--images", nargs="*", default=None,
                    help="图片集路径（glob 展开或直接列）")
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    ap.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    ap.add_argument("--frames-dir", default=str(DEFAULT_FRAMES_DIR),
                    help="视频抽帧目录（仅当 --video 时有意义）")
    ap.add_argument("--fake-input", action="store_true",
                    help="不调真实 VLM；用内置 fake 输入快速自检")
    args = ap.parse_args(argv)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    frames_dir = Path(args.frames_dir)

    inputs: list[dict] = []
    if args.video:
        vp = Path(args.video)
        if not vp.exists():
            print(f"ERROR: video 不存在 {vp}", file=sys.stderr)
            return 2
        inputs.extend(collect_inputs_from_video(vp))
    if args.images:
        for ip in args.images:
            pp = Path(ip)
            if pp.is_dir():
                inputs.extend(collect_inputs_from_images(list(pp.glob("*.jpg"))
                                                          + list(pp.glob("*.png"))))
            elif pp.exists():
                inputs.append({"path": str(pp), "kind": "image", "index": len(inputs)})
            else:
                # glob 展开
                parent = pp.parent
                for m in sorted(parent.glob(pp.name)):
                    inputs.append({"path": str(m), "kind": "image", "index": len(inputs)})

    if not inputs:
        print(f"WARN: 无 inputs；仅生成空报告", file=sys.stderr)

    if args.fake_input:
        result = fake_vlm_check(inputs)
    else:
        # 真实 VLM 入口：本任务**禁止**实际调用；返回占位 + 说明
        print("ERROR: 真实 VLM 调用在 v3.4 任务中禁用；请用 --fake-input 模式自检",
                file=sys.stderr)
        return 3

    result["inputs"] = inputs
    result["n_inputs"] = len(inputs)
    result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    result["pipeline_version"] = "v3.4"
    result["gate_checks_v34"] = V34_CHECKS

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    write_markdown(result, out_md)
    print(f"[vlm_gate] JSON → {out_json}", flush=True)
    print(f"[vlm_gate] MD → {out_md}", flush=True)
    print(f"[vlm_gate] overall_pass={result.get('overall_pass')} "
            f"fake={result.get('fake')} n_inputs={len(inputs)}", flush=True)
    return 0 if result.get("overall_pass") else 1


if __name__ == "__main__":
    sys.exit(main())
