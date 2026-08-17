"""Phase B overlay 包装层测试。

覆盖任务书要求：
  - OverlayProvider Protocol / get_provider 路由
  - PilProvider 能对一段文字渲染出可用的字幕 PNG
  - HyperFramesProvider 的模板占位符替换逻辑（不实际跑 npx，避免 CI 依赖）
  - render_overlays.py 跳过没有 overlay 字段的 shot
  - render_overlays.py 在 HyperFrames 不可用时自动降级到 PIL
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.overlay import (  # noqa: E402
    HyperFramesProvider,
    OVERLAYS_DIR,
    PilProvider,
    RemotionProvider,
    get_provider,
)
from scripts.render_overlays import (  # noqa: E402
    _default_text_for_template,
    build_demo_storyboard,
    render_storyboard_overlays,
)


# ---------------------------------------------------------------------------
# OverlayProvider / registry
# ---------------------------------------------------------------------------


def test_get_provider_routes_known_engines():
    """get_provider 应正确返回三种 provider。"""
    assert isinstance(get_provider("pil"), PilProvider)
    assert isinstance(get_provider("hyperframes"), HyperFramesProvider)
    assert isinstance(get_provider("remotion"), RemotionProvider)


def test_get_provider_unknown_engine_raises():
    """未知 engine 应抛 ValueError，不应静默回退。"""
    with pytest.raises(ValueError):
        get_provider("not-a-real-engine")


def test_protocol_id_attribute():
    """三 provider 都应暴露稳定的 id 字符串。"""
    assert PilProvider().id == "pil"
    assert HyperFramesProvider().id == "hyperframes"
    assert RemotionProvider().id == "remotion"


def test_remotion_raises_not_implemented():
    """RemotionProvider.render 应抛 NotImplementedError（占位）。"""
    provider = RemotionProvider()
    with pytest.raises(NotImplementedError):
        provider.render({}, {}, Path("/tmp/dummy"))


# ---------------------------------------------------------------------------
# PilProvider
# ---------------------------------------------------------------------------


def test_pil_provider_writes_subtitle_png(tmp_path: Path):
    """PilProvider 应渲染半透明黑底字幕条 PNG。"""
    out = tmp_path / "subtitle.png"
    PilProvider().render(
        composition={"width": 1280, "height": 720},
        data={"text": "这座城市睡着以后，还有一班车。"},
        out=out,
    )
    assert out.is_file()
    assert out.stat().st_size > 1024  # PNG 不是空文件
    # 简单的非空校验：PNG 头 8 字节。
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_pil_provider_force_png_extension(tmp_path: Path):
    """即便传入 .jpg，输出也应是 .png（PIL 唯一支持）。"""
    out = tmp_path / "subtitle.jpg"
    result = PilProvider().render(
        composition={"width": 800, "height": 600},
        data={"text": "test"},
        out=out,
    )
    assert result.suffix == ".png"
    assert result.is_file()


def test_pil_provider_rejects_empty_text(tmp_path: Path):
    """空 text 应直接抛错，避免生成空白 PNG。"""
    provider = PilProvider()
    with pytest.raises(ValueError):
        provider.render(
            composition={"width": 800, "height": 600},
            data={"text": ""},
            out=tmp_path / "x.png",
        )


# ---------------------------------------------------------------------------
# HyperFramesProvider：仅测占位符替换逻辑，不真跑 npx
# ---------------------------------------------------------------------------


def test_hyperframes_template_replacement():
    """_render_template 应同时替换 {{key}} 和 __KEY__ 占位符。"""
    template = OVERLAYS_DIR / "title-card.html"
    assert template.is_file(), f"模板未找到: {template}"
    rendered = HyperFramesProvider._render_template(
        template,
        data={"title": "测试标题", "subtitle": "副标题", "tagline": "标语"},
        width=1920,
        height=1080,
        duration=4.0,
    )
    assert "测试标题" in rendered
    assert "副标题" in rendered
    assert "标语" in rendered
    # meta 占位符应被替换成具体数值
    assert "__WIDTH__" not in rendered
    assert "__HEIGHT__" not in rendered
    assert "__DURATION__" not in rendered
    assert 'width="1920"' in rendered or "1920" in rendered


def test_hyperframes_resolve_template_missing_raises(tmp_path: Path, monkeypatch):
    """模板不存在时应抛 FileNotFoundError；未知模板名返回 None。"""
    provider = HyperFramesProvider()
    assert provider._resolve_template(None) is None
    assert provider._resolve_template("") is None
    # 已知模板
    assert provider._resolve_template("title-card").is_file()
    # 不存在的模板
    with pytest.raises(FileNotFoundError):
        provider._resolve_template("no-such-template")


# ---------------------------------------------------------------------------
# render_overlays 编排逻辑
# ---------------------------------------------------------------------------


def _load_sample_storyboard() -> dict:
    sb_path = REPO_ROOT / "examples" / "sample_storyboard.json"
    with sb_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_render_overlays_skips_shots_without_overlay(tmp_path: Path):
    """原始 sample_storyboard.json 没有 overlay 字段，应只生成空 manifest。"""
    sb = _load_sample_storyboard()
    # 确保没有 overlays 字段
    sb.pop("overlays", None)
    for shot in sb["shots"]:
        shot.pop("overlay", None)
    manifest = render_storyboard_overlays(sb, out_dir=tmp_path, fallback_engine="pil")
    assert manifest["summary"]["total"] == 0
    assert (tmp_path / "manifest.json").is_file()


def test_render_overlays_runs_pil_subtitles(tmp_path: Path):
    """每个 shot 带 pil overlay → 每个 shot 都应生成 PNG。"""
    sb = _load_sample_storyboard()
    for shot in sb["shots"]:
        shot["overlay"] = {"engine": "pil", "data": {"text": shot.get("narration", "")}}
    manifest = render_storyboard_overlays(sb, out_dir=tmp_path, fallback_engine="pil")
    assert manifest["summary"]["ok"] == len(sb["shots"])
    produced = [o for o in manifest["overlays"] if o["status"] == "ok"]
    assert len(produced) == len(sb["shots"])
    # 每个 shot 都对应一张 PNG
    for entry in produced:
        assert Path(entry["output"]).is_file()
        assert entry["output"].endswith(".png")


def test_render_overlays_hyperframes_falls_back_to_pil(tmp_path: Path, monkeypatch):
    """HyperFrames 不可用时，自动降级到 PIL，status=fallback_ok。"""
    # 伪造一个总是失败的 HyperFramesProvider
    class FakeHF:
        id = "hyperframes"
        def __init__(self):
            pass
        def render(self, composition, data, out):
            raise RuntimeError("hyperframes unavailable (simulated)")

    monkeypatch.setattr("scripts.overlay.HyperFramesProvider", FakeHF)

    sb = _load_sample_storyboard()
    sb["overlays"] = [
        {
            "id": "title-card",
            "engine": "hyperframes",
            "template": "title-card",
            "data": {"title": sb["title"]},
            "duration": 4.0,
        }
    ]
    manifest = render_storyboard_overlays(sb, out_dir=tmp_path, fallback_engine="pil")
    assert manifest["summary"]["fallback"] == 1
    assert manifest["summary"]["ok"] == 1
    overlay = manifest["overlays"][0]
    assert overlay["status"] == "fallback_ok"
    assert overlay["fallback_engine"] == "pil"
    assert "HyperFrames" in overlay["note"]
    # 兜底产物存在且是 PNG
    assert Path(overlay["output"]).is_file()
    assert overlay["output"].endswith(".png")


def test_render_overlays_writes_manifest(tmp_path: Path):
    """manifest.json 应包含每个 overlay 的执行报告。"""
    sb = _load_sample_storyboard()
    for shot in sb["shots"]:
        shot["overlay"] = {"engine": "pil", "data": {"text": "ok"}}
    manifest = render_storyboard_overlays(sb, out_dir=tmp_path, fallback_engine="pil")
    manifest_path = Path(manifest["manifest_path"])
    assert manifest_path.is_file()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "overlays" in payload and "summary" in payload
    assert payload["summary"]["ok"] >= 1


def test_default_text_for_template():
    """兜底文本生成按模板分支，覆盖五个模板。"""
    assert "主标题" in _default_text_for_template("title-card", {"title": "主标题"})
    assert "结束" in _default_text_for_template("end-card", {"title": "结束"})
    assert _default_text_for_template("subtitle-bar", {"text": "字幕文本"}) == "字幕文本"
    assert _default_text_for_template("lower-third", {"name": "阿澈", "caption": "二十岁"}) == "阿澈 二十岁"
    assert _default_text_for_template("transition", {"label": "NEXT"}) == "NEXT"


def test_build_demo_storyboard_attaches_overlays():
    """build_demo_storyboard 在原 storyboard 上挂 demo overlays 但不破坏原有字段。"""
    sb = _load_sample_storyboard()
    demo = build_demo_storyboard(sb)
    # 原 shots 字段保留
    assert len(demo["shots"]) == len(sb["shots"])
    # 每个有 narration 的 shot 都挂上 overlay
    for shot in demo["shots"]:
        if shot.get("narration"):
            assert "overlay" in shot
            assert shot["overlay"]["engine"] == "pil"
    # 顶层有 title-card + end-card
    engine_pairs = [(o["id"], o["engine"]) for o in demo["overlays"]]
    assert ("title-card", "hyperframes") in engine_pairs
    assert ("end-card", "hyperframes") in engine_pairs


# ---------------------------------------------------------------------------
# pipeline.yaml：overlay 阶段已插入 video 之后
# ---------------------------------------------------------------------------


def test_pipeline_has_overlay_stage_after_video():
    import yaml

    with (REPO_ROOT / "pipeline.yaml").open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    ids = [s["id"] for s in data["stages"]]
    assert "overlay" in ids
    assert ids.index("overlay") > ids.index("video")
    # overlay 阶段声明了 options.default_engine
    overlay_stage = next(s for s in data["stages"] if s["id"] == "overlay")
    assert overlay_stage["options"]["default_engine"] == "pil"