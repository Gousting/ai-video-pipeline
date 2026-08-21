#!/usr/bin/env python3
"""v3.6.7 Step 0 续: 用 minimax-m3 VLM 视觉识别参考视频, 输出风格档案。

任务书 oc_task_v367.txt §2.1 §2.2:
- 把 12 个关键帧 (filmstrip + 单独帧) 喂给 minimax-m3
- 要求返回结构化 JSON: 角色/背景/色彩/画风/构图/光线/一致性描述词
- 落盘到 ref_analysis_v367/v367_style_profile.md (含结构化 + 原始 VLM 输出)

CLI:
    python vlm_analyze_ref_v367.py
    python vlm_analyze_ref_v367.py --frames-dir ...
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # noqa

import requests
from PIL import Image

ROOT = Path(r"D:\ai-video-pipeline")
sys.path.insert(0, str(ROOT / "scripts"))

from vlm_config import VLM_API_KEY, VLM_API_URL, VLM_MODEL

DEFAULT_FRAMES_DIR = ROOT / "ref_analysis_v367" / "frames"
DEFAULT_OUT_DIR = ROOT / "ref_analysis_v367"
DEFAULT_OUT_JSON = DEFAULT_OUT_DIR / "v367_vlm_raw.json"
DEFAULT_PROFILE_MD = DEFAULT_OUT_DIR / "v367_style_profile.md"


REVIEW_PROMPT = """你是一名短视频画面风格审查员, 专精动漫/动画美术分析。

我会给你一张 4×3 的 filmstrip, 共 12 帧关键画面, 来自一段参考视频
`input_h3_pv_ref.mp4` (1358×576 横屏, 30fps, 31.33s, 横屏动漫风格)。
关键帧已按场景切换点抽取 (t=0.20 / 1.37 / 3.50 / 8.0 / 10.6 / 12.27 /
14.03 / 15.67 / 20.87 / 24.97 / 27.27 / 30.5 秒)。

**任务**: 基于这些帧做严格的视觉风格识别, 输出可被 AI 视频生成模型
(MiniMax H3 Reference-to-Video) 直接消费的风格档案。

请只返回一个 JSON 对象 (不要 markdown 代码块, 不要额外文字), 字段严格如下:

{
  "characters": [
    {
      "name": "<可辨识名, e.g. 学姐/学妹/男主/女主/角色1>",
      "appearance": {
        "hair_style": "<发式>",
        "hair_color": "<发色>",
        "eye_color": "<瞳色>",
        "skin_tone": "<肤色>",
        "outfit": "<服装 (含颜色+款式+配饰)>",
        "body_shape": "<体型>",
        "distinctive_features": ["<识别特征 1>", "<2>", "<3>"],
        "expression_default": "<默认表情气质>"
      },
      "first_seen_at_frame": <1-12 帧号>,
      "stable_frames": [<出现该角色且可辨识的帧号列表>],
      "consistency_score": <0-100, 跨帧身份保真度>
    },
    ... // 多角色逐个列出
  ],
  "background_and_scenes": {
    "primary_settings": ["<主要场景 1>", "<2>", ...],
    "environment_props": ["<环境元素 1>", "<2>", ...],
    "scene_changes_count": <粗估场景切换次数>,
    "depth_of_field": "<深景/浅景/混合>",
    "has_distinct_background": <bool>
  },
  "color_palette": {
    "primary_colors": ["<主色 1>", "<2>", ...],
    "secondary_colors": ["<辅色 1>", "<2>", ...],
    "color_temperature": "<暖/冷/中性>",
    "saturation": "<高/中/低>",
    "color_grade_style": "<调色风格: 日系清新/电影青橙/复古胶片/...>",
    "color_anchors_hex_approx": ["<近似主色 hex 1>", "<2>"]
  },
  "art_style": {
    "rendering": "<赛璐璐/厚涂/3D/混合>",
    "linework": "<粗细/锐度/手绘感>",
    "shading": "<平涂/光影渐变/无阴影>",
    "texture": "<光滑/磨砂/水彩>",
    "is_anime": <bool>,
    "sub_style": "<新海诚/京都动画/吉卜力/Pixar/...">,
    "specific_director_or_studio_vibe": "<参考动画师/工作室, 或 'no clear reference'>"
  },
  "composition_and_camera": {
    "aspect_ratio": "<16:9 / 2.36:1 / 9:16 / ...>",
    "framing_distribution": {
      "extreme_closeup_pct": <0-100>,
      "medium_pct": <0-100>,
      "wide_pct": <0-100>
    },
    "camera_movement": "<静态/推拉/摇移/手持>",
    "composition_habits": ["<构图习惯 1>", "<2>", ...]
  },
  "lighting_and_mood": {
    "lighting_source": "<自然光/室内/人造/混合>",
    "lighting_direction": "<主光方向>",
    "time_of_day_distribution": ["<白天>", "<黄昏>", "<夜>", ...],
    "mood_primary": "<主要情绪基调>",
    "mood_secondary": ["<次要情绪>", ...]
  },
  "reusable_consistency_descriptors": {
    "character_block_for_prompt": "<一段可直接粘到 AI prompt 的英文角色描述, 100-300 词, 含全部识别特征>",
    "style_block_for_prompt": "<一段可直接粘到 AI prompt 的英文画风描述, 50-150 词>",
    "color_block_for_prompt": "<一段可直接粘到 AI prompt 的英文色彩描述, 30-80 词>",
    "lighting_block_for_prompt": "<一段可直接粘到 AI prompt 的英文光线/氛围描述, 30-80 词>",
    "banned_in_prompt": ["<生成时禁止出现的元素 1>", "<2>", ...]
  },
  "summary": {
    "overall_genre": "<类型标签>",
    "one_sentence_style": "<一句话总结这个参考视频的视觉风格>",
    "best_for_h3_r2v": <bool, 是否适合作为 H3 Reference-to-Video 的 ref_images 来源>,
    "risks": ["<生成时的风险点 1>", "<2>", ...]
  }
}

严格要求:
1. 只返回 JSON, 不要任何额外说明
2. character_block_for_prompt 必须是英文 (H3 模型吃英文)
3. 数字字段必须给具体值, 不许 null 或省略
4. consistency_score 必须基于实际观察 (跨帧身份可辨识程度)
"""


def frame_to_b64(img: Image.Image, target_kb: int = 200) -> str:
    img = img.convert("RGB")
    img.thumbnail((768, 768))
    quality = 85
    for _ in range(6):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if len(buf.getvalue()) <= target_kb * 1024:
            break
        quality -= 12
    return base64.b64encode(buf.getvalue()).decode()


def chat_vlm(messages: list, attempts: int = 4) -> str:
    last = None
    for i in range(attempts):
        try:
            r = requests.post(
                VLM_API_URL,
                json={"model": VLM_MODEL, "messages": messages},
                headers={"Authorization": f"Bearer {VLM_API_KEY}"},
                timeout=240,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last = f"{r.status_code}: {r.text[:300]}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:300]
        time.sleep(4 + i * 3)
    raise RuntimeError(f"VLM call failed: {last}")


def parse_json_response(raw: str) -> dict:
    m = re.search(r"</think>\s*(\{.*\})\s*$", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return {"parse_error": True, "raw": raw}
        return {"parse_error": True, "raw": raw}


def render_profile_md(profile: dict, manifest: dict,
                      raw_vlm: str) -> str:
    """生成可读的 v367_style_profile.md。"""
    lines = []
    lines.append("# 视觉风格档案 — 参考视频 input_h3_pv_ref.mp4 (v3.6.7)")
    lines.append("")
    lines.append(f"> 自动生成: {time.strftime('%Y-%m-%d %H:%M:%S')}  "
                 f"  VLM: `{VLM_MODEL}`  Frames: {manifest.get('n_frames')}")
    lines.append("")
    lines.append("> 任务书: oc_task_v367.txt §2.1 §2.2 (视觉识别 + 落盘)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 摘要
    s = profile.get("summary", {})
    lines.append("## 0. 摘要")
    lines.append(f"- **类型**: {s.get('overall_genre', '?')}")
    lines.append(f"- **一句话风格**: {s.get('one_sentence_style', '?')}")
    lines.append(f"- **适合 H3 R2V**: {s.get('best_for_h3_r2v', '?')}")
    if s.get("risks"):
        lines.append(f"- **生成风险**: {'; '.join(s['risks'])}")
    lines.append("")

    # 角色
    chars = profile.get("characters", [])
    lines.append("## 1. 角色设定")
    if not chars:
        lines.append("_未识别出明确角色 (单场景物体/风景视频)_")
    for i, c in enumerate(chars, start=1):
        lines.append(f"### 角色 {i}: {c.get('name', '?')}")
        ap = c.get("appearance", {})
        lines.append(f"- 发型: {ap.get('hair_style', '?')}")
        lines.append(f"- 发色: {ap.get('hair_color', '?')}")
        lines.append(f"- 瞳色: {ap.get('eye_color', '?')}")
        lines.append(f"- 肤色: {ap.get('skin_tone', '?')}")
        lines.append(f"- 服装: {ap.get('outfit', '?')}")
        lines.append(f"- 体型: {ap.get('body_shape', '?')}")
        lines.append(f"- 默认表情: {ap.get('expression_default', '?')}")
        feat = ap.get("distinctive_features", [])
        if feat:
            lines.append(f"- 识别特征: {'; '.join(feat)}")
        lines.append(f"- 首次出现: 帧 {c.get('first_seen_at_frame', '?')}")
        lines.append(f"- 稳定帧: {c.get('stable_frames', '?')}")
        lines.append(f"- 一致性评分: {c.get('consistency_score', '?')}/100")
        lines.append("")

    # 背景
    bg = profile.get("background_and_scenes", {})
    lines.append("## 2. 背景与场景")
    lines.append(f"- 主要场景: {'; '.join(bg.get('primary_settings', [])) or '?'}")
    lines.append(f"- 环境元素: {'; '.join(bg.get('environment_props', [])) or '?'}")
    lines.append(f"- 场景切换次数: {bg.get('scene_changes_count', '?')}")
    lines.append(f"- 景深: {bg.get('depth_of_field', '?')}")
    lines.append(f"- 明确背景: {bg.get('has_distinct_background', '?')}")
    lines.append("")

    # 色彩
    cp = profile.get("color_palette", {})
    lines.append("## 3. 色彩倾向")
    lines.append(f"- 主色: {'; '.join(cp.get('primary_colors', [])) or '?'}")
    lines.append(f"- 辅色: {'; '.join(cp.get('secondary_colors', [])) or '?'}")
    lines.append(f"- 色温: {cp.get('color_temperature', '?')}")
    lines.append(f"- 饱和度: {cp.get('saturation', '?')}")
    lines.append(f"- 调色风格: {cp.get('color_grade_style', '?')}")
    hex_approx = cp.get("color_anchors_hex_approx", [])
    if hex_approx:
        lines.append(f"- 主色近似 hex: {' '.join(hex_approx)}")
    lines.append("")

    # 画风
    a = profile.get("art_style", {})
    lines.append("## 4. 画风")
    lines.append(f"- 渲染: {a.get('rendering', '?')}")
    lines.append(f"- 线条: {a.get('linework', '?')}")
    lines.append(f"- 阴影: {a.get('shading', '?')}")
    lines.append(f"- 质感: {a.get('texture', '?')}")
    lines.append(f"- 动漫: {a.get('is_anime', '?')}")
    lines.append(f"- 子风格: {a.get('sub_style', '?')}")
    lines.append(f"- 动画师/工作室气质: {a.get('specific_director_or_studio_vibe', '?')}")
    lines.append("")

    # 构图
    cm = profile.get("composition_and_camera", {})
    lines.append("## 5. 构图与镜头语言")
    lines.append(f"- 画幅: {cm.get('aspect_ratio', '?')}")
    fd = cm.get("framing_distribution", {})
    if fd:
        lines.append(f"- 景别分布: 特写 {fd.get('extreme_closeup_pct', '?')}%, "
                     f"中景 {fd.get('medium_pct', '?')}%, "
                     f"远景 {fd.get('wide_pct', '?')}%")
    lines.append(f"- 运镜: {cm.get('camera_movement', '?')}")
    lines.append(f"- 构图习惯: {'; '.join(cm.get('composition_habits', [])) or '?'}")
    lines.append("")

    # 光线氛围
    lh = profile.get("lighting_and_mood", {})
    lines.append("## 6. 光线 / 氛围 / 情绪")
    lines.append(f"- 光源: {lh.get('lighting_source', '?')}")
    lines.append(f"- 主光方向: {lh.get('lighting_direction', '?')}")
    lines.append(f"- 时段分布: {'; '.join(lh.get('time_of_day_distribution', [])) or '?'}")
    lines.append(f"- 主情绪: {lh.get('mood_primary', '?')}")
    lines.append(f"- 次情绪: {'; '.join(lh.get('mood_secondary', [])) or '?'}")
    lines.append("")

    # 一致性描述词 (供 prompt 直接引用)
    rc = profile.get("reusable_consistency_descriptors", {})
    lines.append("## 7. 可复用的一致性描述词 (供 prompt 直接引用)")
    lines.append("")
    lines.append("### 7.1 character_block_for_prompt")
    lines.append("```")
    lines.append(rc.get("character_block_for_prompt", "?").strip())
    lines.append("```")
    lines.append("")
    lines.append("### 7.2 style_block_for_prompt")
    lines.append("```")
    lines.append(rc.get("style_block_for_prompt", "?").strip())
    lines.append("```")
    lines.append("")
    lines.append("### 7.3 color_block_for_prompt")
    lines.append("```")
    lines.append(rc.get("color_block_for_prompt", "?").strip())
    lines.append("```")
    lines.append("")
    lines.append("### 7.4 lighting_block_for_prompt")
    lines.append("```")
    lines.append(rc.get("lighting_block_for_prompt", "?").strip())
    lines.append("```")
    lines.append("")
    lines.append("### 7.5 banned_in_prompt (生成时禁止)")
    if rc.get("banned_in_prompt"):
        for b in rc["banned_in_prompt"]:
            lines.append(f"- {b}")
    else:
        lines.append("- (无)")
    lines.append("")

    # 原始 VLM 输出 (折叠)
    lines.append("---")
    lines.append("")
    lines.append("## 附录 A: 原始 VLM 输出 (raw, 已 parse JSON)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(profile, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", default=str(DEFAULT_FRAMES_DIR))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    ap.add_argument("--profile-md", default=str(DEFAULT_PROFILE_MD))
    args = ap.parse_args(argv)

    frames_dir = Path(args.frames_dir)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    profile_md = Path(args.profile_md)

    frames = sorted(frames_dir.glob("frame_*.jpg"))
    if not frames:
        print(f"ERROR: no frames found in {frames_dir}", file=sys.stderr)
        return 2

    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"n_frames": len(frames)}

    print(f"[vlm-v367] loading {len(frames)} frames from {frames_dir}",
          flush=True)
    content = [{"type": "text", "text": REVIEW_PROMPT}]
    for fp in frames:
        img = Image.open(fp).convert("RGB")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(img)}"}
        })

    print(f"[vlm-v367] sending {len(frames)} frames to VLM "
          f"({VLM_MODEL} @ {VLM_API_URL[:60]}...)", flush=True)

    try:
        raw = chat_vlm([{"role": "user", "content": content}])
    except RuntimeError as e:
        print(f"ERROR: VLM call failed: {e}", file=sys.stderr)
        return 3

    profile = parse_json_response(raw)
    if profile.get("parse_error"):
        print("ERROR: JSON parse failed", file=sys.stderr)
        print(raw[:1500], file=sys.stderr)
        return 4

    profile["_meta"] = {
        "vlm_model": VLM_MODEL,
        "frames_sent": len(frames),
        "manifest": manifest,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    profile["raw_response"] = raw

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(profile, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[vlm-v367] raw JSON -> {out_json}", flush=True)

    md_text = render_profile_md(profile, manifest, raw)
    profile_md.write_text(md_text, encoding="utf-8")
    print(f"[vlm-v367] profile MD -> {profile_md}", flush=True)

    # 简版摘要
    chars = profile.get("characters", [])
    print(f"[vlm-v367] 识别到 {len(chars)} 个角色")
    for c in chars:
        print(f"  - {c.get('name')}: 一致性={c.get('consistency_score')}/100")
    print(f"[vlm-v367] 主色: {profile.get('color_palette', {}).get('primary_colors')}")
    print(f"[vlm-v367] 画风: {profile.get('art_style', {}).get('rendering')} / "
          f"{profile.get('art_style', {}).get('sub_style')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
