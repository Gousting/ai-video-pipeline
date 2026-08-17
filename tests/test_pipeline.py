"""P3 管道定义（pipeline.yaml）解析测试。

覆盖任务书要求：pipeline.yaml 解析通过 / 缺 gate 报错 / 缺 output 报错。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.parse_pipeline import PIPELINE_PATH, validate_pipeline, validate_pipeline_data  # noqa: E402


def _base_pipeline() -> dict:
    return {
        "pipeline": "ai-video",
        "version": "0.1",
        "stages": [
            {
                "id": "script",
                "agent": "visual-storyteller",
                "input": "user_brief.md",
                "output": "storyboard.json",
                "gate": "script-review",
            },
            {
                "id": "image",
                "agent": "image-prompt-engineer",
                "input": "storyboard.json",
                "output": "frames/ + char_bible.json",
                "gate": "vlm-review",
            },
        ],
        "artifacts": {
            "root": "D:\\ai-video-pipeline\\output\\",
            "layout": {"storyboard": "sb/", "frames": "frames/"},
        },
    }


def test_pipeline_yaml_parses():
    """pipeline.yaml 应解析通过，且 7 个阶段全部含 gate。"""
    ok, errors = validate_pipeline(PIPELINE_PATH)
    assert ok, f"pipeline.yaml 应通过校验，但报错: {errors}"
    assert errors == []


def test_pipeline_stages_all_have_gates():
    """pipeline.yaml 的 7 个阶段必须全部声明 gate（Phase B 后新增 overlay 环节）。"""
    ok, errors = validate_pipeline(PIPELINE_PATH)
    assert ok
    # 通过校验本身就保证 gate 完整；此处额外确认阶段数量与 gate 字段。
    import yaml

    with PIPELINE_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    stages = data["stages"]
    assert len(stages) == 7
    assert all(stage.get("gate") for stage in stages)
    stage_ids = [stage["id"] for stage in stages]
    # Phase B 新增 overlay 阶段，位于 video 之后、audio 之前。
    assert stage_ids.index("overlay") == stage_ids.index("video") + 1
    assert stage_ids.index("overlay") == stage_ids.index("audio") - 1


def test_missing_gate_fails():
    """缺 gate 的环节应报错。"""
    data = _base_pipeline()
    del data["stages"][0]["gate"]
    ok, errors = validate_pipeline_data(data)
    assert not ok
    assert any("gate" in err for err in errors)


def test_missing_output_fails():
    """缺 output 的环节应报错。"""
    data = _base_pipeline()
    del data["stages"][1]["output"]
    ok, errors = validate_pipeline_data(data)
    assert not ok
    assert any("output" in err for err in errors)


def test_missing_agent_fails():
    """缺 agent 的环节应报错。"""
    data = _base_pipeline()
    del data["stages"][0]["agent"]
    ok, errors = validate_pipeline_data(data)
    assert not ok
    assert any("agent" in err for err in errors)


def test_duplicate_stage_id_fails():
    """阶段 id 重复应报错（阶段顺序要求 id 唯一）。"""
    data = _base_pipeline()
    data["stages"][1]["id"] = "script"
    ok, errors = validate_pipeline_data(data)
    assert not ok
    assert any("重复" in err for err in errors)


def test_empty_stages_fails():
    """stages 为空列表应报错。"""
    data = _base_pipeline()
    data["stages"] = []
    ok, errors = validate_pipeline_data(data)
    assert not ok
    assert any("stages" in err for err in errors)


def test_unresolved_input_fails():
    """环节输入无来源（种子输入或前置产物均不覆盖）应报错。"""
    data = _base_pipeline()
    data["stages"][1]["input"] = "ghost_file.json"
    ok, errors = validate_pipeline_data(data)
    assert not ok
    assert any("无来源" in err for err in errors)
