"""P3 分镜/角色 schema 测试。

覆盖任务书要求：合法分镜通过 / 缺 visual 报错 / shots 空报错 / 角色四要素缺项报错。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_storyboard import load_schema, validate_storyboard  # noqa: E402

SAMPLE_PATH = REPO_ROOT / "examples" / "sample_storyboard.json"
CHARACTER_SCHEMA_PATH = REPO_ROOT / "schemas" / "character.schema.json"


def _load_sample() -> dict:
    with SAMPLE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _base_storyboard() -> dict:
    return {
        "title": "测试分镜",
        "total_duration": 10,
        "tone": "测试",
        "characters": [
            {
                "name": "角色A",
                "appearance": "外貌描述",
                "personality": "性格描述",
                "speech_style": "说话方式",
                "taboos": "禁忌",
            }
        ],
        "shots": [
            {
                "index": 1,
                "duration": 5,
                "scene": "场景",
                "action": "运镜",
                "mood": "情绪",
                "visual": "画面描述",
                "narration": "旁白",
                "first_frame_ref": "无",
                "last_frame_ref": "无",
                "sound": "BGM提示",
            },
            {
                "index": 2,
                "duration": 5,
                "scene": "场景二",
                "action": "运镜二",
                "mood": "情绪二",
                "visual": "画面描述二",
                "narration": "旁白二",
                "sound": "BGM提示二",
            },
        ],
    }


def test_valid_storyboard_passes():
    """合法分镜（样例文件，3 镜头）应通过校验。"""
    ok, errors = validate_storyboard(_load_sample())
    assert ok, f"样例分镜应通过校验，但报错: {errors}"
    assert errors == []


def test_missing_visual_fails():
    """shot 缺 visual 必填字段应报错。"""
    data = _base_storyboard()
    del data["shots"][0]["visual"]
    ok, errors = validate_storyboard(data)
    assert not ok
    assert any("visual" in err for err in errors)


def test_empty_shots_fails():
    """shots 为空数组应报错（必填且非空）。"""
    data = _base_storyboard()
    data["shots"] = []
    ok, errors = validate_storyboard(data)
    assert not ok
    assert any("shots" in err for err in errors)


def test_missing_title_fails():
    """缺 title 应报错。"""
    data = _base_storyboard()
    del data["title"]
    ok, errors = validate_storyboard(data)
    assert not ok
    assert any("title" in err for err in errors)


@pytest.mark.parametrize("missing_field", ["name", "appearance", "personality", "speech_style"])
def test_character_four_elements_missing_fails(missing_field):
    """角色四要素缺任意一项应报错。"""
    data = _base_storyboard()
    del data["characters"][0][missing_field]
    ok, errors = validate_storyboard(data)
    assert not ok
    assert any(missing_field in err for err in errors)


def test_character_schema_valid_passes():
    """角色圣经 schema：合法角色通过。"""
    with CHARACTER_SCHEMA_PATH.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    character = {
        "name": "角色A",
        "appearance": "外貌描述",
        "personality": "性格描述",
        "speech_style": "说话方式",
        "taboos": "禁忌",
        "ref_image": "char/角色A_定妆.png",
    }
    errors = list(Draft7Validator(schema).iter_errors(character))
    assert errors == []


def test_character_schema_missing_required_fails():
    """角色圣经 schema：缺四要素之一应报错。"""
    with CHARACTER_SCHEMA_PATH.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    character = {
        "name": "角色A",
        "appearance": "外貌描述",
        # 缺 personality
        "speech_style": "说话方式",
    }
    errors = list(Draft7Validator(schema).iter_errors(character))
    assert any("personality" in err.message for err in errors)
