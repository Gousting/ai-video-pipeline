#!/usr/bin/env python3
"""AI 视频流水线 P3：分镜表校验器。

读取 storyboard JSON -> 对照 schemas/storyboard.schema.json 校验 ->
输出 通过 / 错误明细。CLI: python validate_storyboard.py <storyboard.json>

退出码：0 = 校验通过；1 = 校验失败（含 schema/JSON 错误）；2 = 用法错误（文件不存在/参数缺失）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft7Validator
    from jsonschema.exceptions import ValidationError
except ImportError:  # pragma: no cover - 依赖缺失时的友好提示
    sys.stderr.write("错误：缺少 jsonschema 依赖，请先执行 pip install jsonschema\n")
    sys.exit(2)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "storyboard.schema.json"


def load_schema(schema_path: Path = SCHEMA_PATH) -> dict:
    """加载分镜表 JSON Schema。"""
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_storyboard(data: dict, schema: dict | None = None) -> tuple[bool, list[str]]:
    """校验 storyboard 数据（dict）。

    返回 (是否通过, 错误明细列表)。通过时错误明细为空列表。
    """
    if schema is None:
        schema = load_schema()
    validator = Draft7Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.path) or "(root)"
        errors.append(f"{path}: {error.message}")
    return (len(errors) == 0), errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="分镜表校验器：读取 storyboard JSON，对照 schema 校验并输出结果。"
    )
    parser.add_argument("storyboard", help="storyboard JSON 文件路径")
    args = parser.parse_args(argv)

    storyboard_path = Path(args.storyboard)
    if not storyboard_path.is_file():
        sys.stderr.write(f"错误：文件不存在 - {storyboard_path}\n")
        return 2

    try:
        with storyboard_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"错误：JSON 解析失败 - {exc}\n")
        return 1

    ok, errors = validate_storyboard(data)
    if ok:
        title = data.get("title", "?")
        shots = len(data.get("shots", []))
        total = data.get("total_duration", "?")
        print(f"校验通过: {title} (shots={shots}, total_duration={total})")
        return 0

    print(f"校验失败: {storyboard_path}")
    for err in errors:
        print(f"  - {err}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
