#!/usr/bin/env python3
"""AI 视频流水线 P3：由 pipeline.yaml 生成可读流程说明（Markdown）。

供 opencode 编排执行时对照。CLI: python render_workflow.py [pipeline.yaml] [output.md]
- 未指定 output.md 时打印到 stdout。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from parse_pipeline import PIPELINE_PATH, load_pipeline, validate_pipeline
except ImportError:  # 作为包模块被导入（如 pytest / 报告脚本）时
    from scripts.parse_pipeline import PIPELINE_PATH, load_pipeline, validate_pipeline

# 环节 id 到中文说明（仅用于文档可读性，不参与校验）。
STAGE_LABELS = {
    "script": "剧本分镜",
    "image": "生图引导",
    "character": "角色设定",
    "video": "视频生成",
    "audio": "配音混音",
    "final": "成片评审",
}


def render_workflow(pipeline_path: Path = PIPELINE_PATH) -> str:
    """生成可读的流程说明 Markdown。"""
    data = load_pipeline(pipeline_path)
    name = data.get("pipeline", "?")
    version = data.get("version", "?")
    stages = data.get("stages", [])
    artifacts = data.get("artifacts", {})
    root = artifacts.get("root", "?")
    layout = artifacts.get("layout", {})

    lines: list[str] = []
    lines.append(f"# AI 视频流水线流程说明（{name} v{version}）")
    lines.append("")
    lines.append("> 本文档由 scripts/render_workflow.py 从 pipeline.yaml 自动生成，供 opencode 编排执行时对照。")
    lines.append("")
    lines.append(f"- 管道名称：`{name}`")
    lines.append(f"- 版本：`{version}`")
    lines.append(f"- 环节总数：{len(stages)}")
    lines.append(f"- 产物根目录：`{root}`")
    lines.append("")
    lines.append("## 执行顺序")
    lines.append("")
    for i, stage in enumerate(stages, start=1):
        sid = stage.get("id", "?")
        label = STAGE_LABELS.get(sid, "")
        label_suffix = f"（{label}）" if label else ""
        lines.append(f"{i}. **{sid}**{label_suffix}：`{stage.get('agent', '?')}`")
        lines.append(f"   - 输入：`{stage.get('input', '?')}`")
        lines.append(f"   - 输出：`{stage.get('output', '?')}`")
        lines.append(f"   - 验收 gate：`{stage.get('gate', '?')}`")
    lines.append("")
    lines.append("## 产物布局")
    lines.append("")
    lines.append("| 环节 | 目录 |")
    lines.append("| --- | --- |")
    for key, value in layout.items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- 每一环节必须通过其 gate 验收后才进入下一环节；gate 未通过则本环节重跑。")
    lines.append("- 除 `script` 环节消费 `user_brief.md`（用户选题）外，各环节输入均来自前置环节产物。")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="由 pipeline.yaml 生成可读流程说明（Markdown）。"
    )
    parser.add_argument("pipeline", nargs="?", default=str(PIPELINE_PATH), help="pipeline.yaml 路径")
    parser.add_argument("output", nargs="?", default=None, help="输出 .md 路径（缺省打印到 stdout）")
    args = parser.parse_args(argv)

    pipeline_path = Path(args.pipeline)
    if not pipeline_path.is_file():
        sys.stderr.write(f"错误：文件不存在 - {pipeline_path}\n")
        return 2

    ok, errors = validate_pipeline(pipeline_path)
    if not ok:
        sys.stderr.write("错误：pipeline.yaml 校验未通过，无法生成流程说明：\n")
        for err in errors:
            sys.stderr.write(f"  - {err}\n")
        return 1

    markdown = render_workflow(pipeline_path)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        print(f"已生成: {args.output}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
