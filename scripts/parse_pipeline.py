#!/usr/bin/env python3
"""AI 视频流水线 P3：声明式管道定义解析/校验器。

读取 pipeline.yaml -> 校验阶段顺序 / agent / 输入输出 / gate 完整性 ->
输出 通过 / 错误明细。CLI: python parse_pipeline.py [pipeline.yaml]

退出码：0 = 校验通过；1 = 校验失败（含 YAML/结构错误）；2 = 用法错误（文件不存在/参数缺失）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 依赖缺失时的友好提示
    sys.stderr.write("错误：缺少 PyYAML 依赖，请先执行 pip install pyyaml\n")
    sys.exit(2)

PIPELINE_PATH = Path(__file__).resolve().parent.parent / "pipeline.yaml"

# 首个环节允许消费的种子输入（用户选题文件），也是管道外的唯一外部输入。
SEED_INPUTS = {"user_brief.md"}


def _tokenize(text: str) -> list[str]:
    """把环节 input/output 字符串切成令牌。

    规则：去掉括号描述（如 '(圣经+定妆图+ref2va参考图)'），再按空白与 '+' 切分。
    """
    text = re.sub(r"[（(][^）)]*[）)]", " ", text)
    parts = re.split(r"[\s+]+", text)
    return [p for p in parts if p]


def _is_covered(token: str, available: set[str]) -> bool:
    """令牌是否被已有产物覆盖：精确匹配或目录前缀匹配（clips/ 覆盖 clips/*.mp4）。"""
    return any(token == a or token.startswith(a) or a.startswith(token) for a in available)


def load_pipeline(path: Path) -> dict:
    """加载 pipeline.yaml 为 dict。"""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_pipeline_data(data: dict) -> tuple[bool, list[str]]:
    """校验管道定义 dict。返回 (是否通过, 错误明细列表)。"""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["根节点必须是对象（含 pipeline/version/stages/artifacts）"]

    if not isinstance(data.get("pipeline"), str) or not data.get("pipeline"):
        errors.append("缺少 pipeline 名称（pipeline 字段必填）")
    # version 允许字符串或数字（YAML 会把 0.1 解析为 float）。
    version = data.get("version")
    if version is None or version == "" or not isinstance(version, (str, int, float)):
        errors.append("缺少 version 版本号（version 字段必填）")

    stages = data.get("stages")
    if not isinstance(stages, list) or len(stages) == 0:
        errors.append("stages 必须是非空列表（至少 1 个环节）")
    else:
        seen_ids: set[str] = set()
        for i, stage in enumerate(stages):
            label = f"stages[{i}]"
            if not isinstance(stage, dict):
                errors.append(f"{label} 必须是对象")
                continue
            sid = stage.get("id")
            if not isinstance(sid, str) or not sid:
                errors.append(f"{label} 缺少 id（环节标识必填）")
            elif sid in seen_ids:
                errors.append(f"{label} id 重复: {sid}（阶段顺序要求 id 唯一）")
            else:
                seen_ids.add(sid)
            for field in ("agent", "input", "output", "gate"):
                value = stage.get(field)
                if not isinstance(value, str) or not value:
                    errors.append(f"{label} 缺少 {field}（agent/输入/输出/gate 必填）")

    # 环节交接一致性：后一环节的输入令牌应能由种子输入或前置环节产物覆盖。
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get("root"), str) or not artifacts.get("root"):
        errors.append("artifacts.root 必填（产物根目录）")
    else:
        available = set(SEED_INPUTS)
        layout = artifacts.get("layout")
        if isinstance(layout, dict):
            for key, value in layout.items():
                if isinstance(key, str):
                    available.add(key)
                if isinstance(value, str):
                    available.add(value)
        for i, stage in enumerate(stages if isinstance(stages, list) else []):
            if not isinstance(stage, dict):
                continue
            for token in _tokenize(stage.get("input", "")):
                if not _is_covered(token, available):
                    errors.append(f"stages[{i}]({stage.get('id', '?')}) 输入 '{token}' 无来源（应为种子输入或前置环节产物）")
            for token in _tokenize(stage.get("output", "")):
                available.add(token)

    return (len(errors) == 0), errors


def validate_pipeline(path: Path = PIPELINE_PATH) -> tuple[bool, list[str]]:
    """加载并校验管道定义文件。返回 (是否通过, 错误明细列表)。"""
    try:
        data = load_pipeline(path)
    except yaml.YAMLError as exc:
        return False, [f"YAML 解析失败: {exc}"]
    return validate_pipeline_data(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="管道定义校验器：读取 pipeline.yaml，校验阶段顺序/agent/输入输出/gate 完整性。"
    )
    parser.add_argument("pipeline", nargs="?", default=str(PIPELINE_PATH), help="pipeline.yaml 路径（默认与脚本同级的 ../pipeline.yaml）")
    args = parser.parse_args(argv)

    pipeline_path = Path(args.pipeline)
    if not pipeline_path.is_file():
        sys.stderr.write(f"错误：文件不存在 - {pipeline_path}\n")
        return 2

    ok, errors = validate_pipeline(pipeline_path)
    if ok:
        data = load_pipeline(pipeline_path)
        name = data.get("pipeline", "?")
        version = data.get("version", "?")
        count = len(data.get("stages", []))
        print(f"校验通过: {name} v{version}（{count} 个阶段，全部含 gate）")
        for stage in data.get("stages", []):
            print(f"  - {stage.get('id')}: {stage.get('agent')} -> {stage.get('output')} (gate: {stage.get('gate')})")
        return 0

    print(f"校验失败: {pipeline_path}")
    for err in errors:
        print(f"  - {err}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
