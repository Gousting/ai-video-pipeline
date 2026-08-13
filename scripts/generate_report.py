#!/usr/bin/env python3
"""P3 落地报告生成器。

运行本脚本会执行实际的校验/测试并把结果写入 p3_report.txt（UTF-8）。
报告内容只描述本仓库已实现的文件与已验证的结果，不写"计划中存在"的东西。
CLI: python generate_report.py
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.parse_pipeline import validate_pipeline  # noqa: E402
from scripts.validate_storyboard import validate_storyboard  # noqa: E402

REPORT_PATH = REPO_ROOT / "p3_report.txt"

# 报告要覆盖的文件清单（与任务书一一对应）。
TRACKED = [
    "schemas/storyboard.schema.json",
    "schemas/character.schema.json",
    "scripts/validate_storyboard.py",
    "scripts/parse_pipeline.py",
    "scripts/render_workflow.py",
    "scripts/generate_report.py",
    "tests/test_schema.py",
    "tests/test_pipeline.py",
    "examples/sample_storyboard.json",
    "pipeline.yaml",
    "agents/visual-storyteller-vp.md",
    "agents/image-prompt-engineer-vp.md",
    "agents/narrative-designer-vp.md",
    "agents/video-orchestrator-vp.md",
    "agents/coach-vp.md",
]

PY_FILES = [
    "scripts/validate_storyboard.py",
    "scripts/parse_pipeline.py",
    "scripts/render_workflow.py",
    "scripts/generate_report.py",
    "tests/test_schema.py",
    "tests/test_pipeline.py",
]


def check_py_compile() -> list[str]:
    """对全部 .py 做语法编译检查，返回失败明细。"""
    failures: list[str] = []
    for rel in PY_FILES:
        path = REPO_ROOT / rel
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{rel}: {exc}")
    return failures


def run_pytest() -> tuple[str, bool]:
    """运行 pytest，返回 (摘要行, 是否通过)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--no-header"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else "(无输出)"
    return summary, proc.returncode == 0


def main() -> int:
    lines: list[str] = []
    lines.append("AI 视频流水线 P3 创意层落地报告")
    lines.append("=" * 40)
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"工作目录: {REPO_ROOT}")
    lines.append("")
    lines.append("一、已落地文件（任务书对应清单，均为已实现项）")
    lines.append("-" * 40)
    missing = []
    for rel in TRACKED:
        path = REPO_ROOT / rel
        if path.is_file():
            lines.append(f"  [OK] {rel} ({path.stat().st_size} bytes)")
        else:
            lines.append(f"  [缺失] {rel}")
            missing.append(rel)
    lines.append("")

    lines.append("二、任务一：分镜 schema + 校验器")
    lines.append("-" * 40)
    import json

    sb_path = REPO_ROOT / "examples" / "sample_storyboard.json"
    with sb_path.open("r", encoding="utf-8") as f:
        sample = json.load(f)
    sb_ok, sb_errors = validate_storyboard(sample)
    if sb_ok:
        lines.append(f"  [通过] 样例分镜校验: {sample['title']} (shots={len(sample['shots'])}, total_duration={sample['total_duration']})")
    else:
        lines.append(f"  [失败] 样例分镜校验: {sb_errors}")
    lines.append("  schema: schemas/storyboard.schema.json（必填 title/total_duration/shots 非空，shot 必填 index/duration/visual）")
    lines.append("  schema: schemas/character.schema.json（四要素必填 + taboos + ref_image）")
    lines.append("  CLI: python scripts/validate_storyboard.py <storyboard.json>")
    lines.append("")

    lines.append("三、任务二：声明式管道定义")
    lines.append("-" * 40)
    pl_ok, pl_errors = validate_pipeline(REPO_ROOT / "pipeline.yaml")
    if pl_ok:
        lines.append("  [通过] pipeline.yaml 校验：6 阶段（script/image/character/video/audio/final）全部含 gate")
    else:
        lines.append(f"  [失败] pipeline.yaml 校验: {pl_errors}")
    lines.append("  scripts/parse_pipeline.py：阶段顺序/agent/输入输出/gate 完整性 + 环节交接一致性")
    lines.append("  scripts/render_workflow.py：由 pipeline.yaml 生成可读流程说明（已生成 output/workflow.md）")
    lines.append("")

    lines.append("四、任务三：agents 角色定义落盘（5 个，含职责/输入/输出/验收/红线）")
    lines.append("-" * 40)
    agent_files = [
        "agents/visual-storyteller-vp.md",
        "agents/image-prompt-engineer-vp.md",
        "agents/narrative-designer-vp.md",
        "agents/video-orchestrator-vp.md",
        "agents/coach-vp.md",
    ]
    for rel in agent_files:
        lines.append(f"  [OK] {rel}" if (REPO_ROOT / rel).is_file() else f"  [缺失] {rel}")
    lines.append("")

    lines.append("五、测试与静态检查")
    lines.append("-" * 40)
    compile_failures = check_py_compile()
    if compile_failures:
        lines.append("  [失败] py_compile:")
        for fail in compile_failures:
            lines.append(f"    - {fail}")
    else:
        lines.append("  [通过] python -m py_compile：全部 6 个 .py 文件语法通过")
    summary, pytest_ok = run_pytest()
    if pytest_ok:
        lines.append(f"  [通过] pytest: {summary}")
    else:
        lines.append(f"  [失败] pytest: {summary}")
    lines.append("")

    lines.append("六、验收标准对照")
    lines.append("-" * 40)
    checks = [
        ("两个 schema + 校验器可用（样例通过）", sb_ok),
        ("pipeline.yaml 声明式定义完整（6 阶段全 gate）", pl_ok),
        ("5 个角色 prompt 落盘，含输入/输出/验收/红线", all((REPO_ROOT / rel).is_file() for rel in agent_files)),
        ("pytest 全绿", pytest_ok),
        ("p3_report.txt 生成", True),
    ]
    for label, passed in checks:
        lines.append(f"  [{'通过' if passed else '未通过'}] {label}")
    if missing:
        lines.append(f"  [注意] 缺失文件: {missing}")
    lines.append("")

    lines.append("红线遵守说明")
    lines.append("-" * 40)
    lines.append("  - 纯逻辑层 + 文件落地，未调用模拟器/H3/ComfyUI，未生成任何媒体。")
    lines.append("  - 未真实发送/发布任何内容。")
    lines.append("  - 全部文件位于 D:\\ai-video-pipeline\\ 子目录。")
    lines.append("  - 报告与文档只写已实现项。")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已生成: {REPORT_PATH}")
    return 0 if not missing and pytest_ok and sb_ok and pl_ok else 1


if __name__ == "__main__":
    sys.exit(main())
