#!/usr/bin/env python3
"""R2V 测试 VLM 辅助审查脚本（minimax-m3，统一 VLM 入口）。

- check_motion：审查动作模板视频（单人/动作清晰/无文字无 Logo）
- check_char：审查角色参考图（是否阿迟新海诚动画风半身）
- review：审查最终 R2V 结果（character_locked / motion_natural / spatial_stable）
- review_filmstrip：合成 filmstrip 单次审查（节省 VLM 调用，能横向对比帧间一致性）

CLI:
  python r2v_review.py check_motion --video <mp4>
  python r2v_review.py check_char --image <png>
  python r2v_review.py review --video <mp4> --ref <character_ref.png>
  python r2v_review.py review_filmstrip --video <mp4> [--n 4] [--ref <png>] [--out <json>]
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image

# 本仓库同目录 helper：合成 filmstrip
sys.path.insert(0, str(Path(__file__).resolve().parent))
import filmstrip as _fs  # noqa: E402  （提供 filmstrip() / filmstrip_b64()）

API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
API_KEY = "sk-VxLhB9Fqnm6XBgd4l1kjloOGq2bJ9g9sKJ2Y0SJTdLwdt6Rtd0olISu02pkmNCZr"
MODEL = "minimax-m3"


def frame_to_b64(img: Image.Image, target_kb: int = 80) -> str:
    img = img.convert("RGB")
    img.thumbnail((768, 768))
    quality = 78
    for _ in range(6):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if len(buf.getvalue()) <= target_kb * 1024:
            break
        quality -= 12
    return base64.b64encode(buf.getvalue()).decode()


def extract_frames(video: Path, n: int = 4) -> list[Image.Image]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dur = float(probe.stdout.strip() or 0.0) or 5.0
    ts = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    for t in ts:
        out = video.with_suffix(".tmp.jpg")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", "-y", str(out)],
            capture_output=True, check=True,
        )
        frames.append(Image.open(out).convert("RGB"))
        out.unlink(missing_ok=True)
    return frames


def chat(messages: list, attempts: int = 4) -> str:
    last = None
    for i in range(attempts):
        try:
            r = requests.post(
                API_URL,
                json={"model": MODEL, "messages": messages},
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=180,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last = f"{r.status_code}: {r.text[:300]}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:300]
        time.sleep(4 + i * 3)
    raise RuntimeError(f"VLM 调用失败: {last}")


def parse_json(raw: str) -> dict:
    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
        return {"parse_error": True, "raw": raw}


MOTION_PROMPT = (
    "你是一名视频素材审查员。我会给你从一段视频抽出的若干帧（按时间顺序）。"
    "请判断这段视频是否适合作为 AI 视频生成的动作参考模板（motion template），"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"single_person": <true/false, 是否只有一个人>'
    ', "action_clear": <true/false, 动作是否清晰明确可辨认>'
    ', "no_text_logo": <true/false, 画面是否无文字/无 Logo/无水印>'
    ', "action_desc": "<这段视频的主要动作一句话描述，英文>"'
    ', "verdict": "<是否适合作为动作参考模板的结论>}'
)

CHAR_PROMPT = (
    "你是一名角色设定审查员。我会给你一张角色定妆参考图。"
    "请判断这张图是否符合以下人设：二十七八岁清瘦、东方面孔、黑短发带雨珠、细框眼镜、"
    "深灰半湿大衣、帆布斜挎包，且画风为新海诚动画风。"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"is_anime_style": <true/false, 是否新海诚动画风>'
    ', "face_clear": <true/false, 面部是否清晰可辨（半身/正面，非远景小人物）>'
    ', "matches_character": <true/false, 是否符合上述人设（东方脸/黑短发/细框眼镜/深灰大衣）>'
    ', "comment": "<一句话说明>}'
)

REVIEW_PROMPT = (
    "你是一名 AI 视频生成质量审查员。我会先给你 1 张角色定妆参考图（锁定角色身份用），"
    "随后给你从一段 AI 生成视频抽出的若干帧（按时间顺序）。请严格审查这段视频：\n"
    "1) character_locked：画面中的角色是否与参考图保持同一人（东方面孔、黑短发、细框眼镜、深灰大衣），"
    "还是变成了参考动作视频里的人；\n"
    "2) motion_natural：是否复刻了参考动作（自然流畅的肢体运动），还是僵着不动只推镜；\n"
    "3) spatial_stable：运镜是否舒服，背景元素（建筑、霓虹灯牌、积水反光等）是否稳定不漂移、不扭曲。\n"
    "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
    '{"score": <0-100 整数>, '
    '"character_locked": <true/false>, "character_comment": "<角色锁定说明>", '
    '"motion_natural": <true/false>, "motion_comment": "<动作自然度说明>", '
    '"spatial_stable": <true/false>, "spatial_comment": "<空间/运镜稳定性说明>", '
    '"opinion": "<一句话结论>"}'
)


def _filmstrip_prompt(n: int) -> str:
    """FILMSTRIP_PROMPT：单张合成图横评多帧，强调帧间一致性。"""
    return (
        "你是一名 AI 视频生成质量审查员。我会给你一张合成图（filmstrip），"
        f"内含按时间顺序排列的 {n} 帧视频抽帧（每帧左上角有白底黑字序号 1..{n}）。"
        "请横向对比所有帧，对视频进行多帧合一审查：\n"
        "1) 逐帧打分：每帧视觉质量（构图/光影/清晰度/无伪影）打 0-100 整数分；\n"
        "2) 帧间一致性（关键项）：\n"
        "   - 角色一致性：同一角色是否在多帧中保持同一人（脸型/发型/服装/配饰无漂移、无突变）；\n"
        "   - 动作连贯性：动作是否自然流畅、无跳帧、无人物闪烁/凭空消失；\n"
        "   - 运镜稳定性：镜头是否稳定（背景元素无明显漂移、扭曲、闪烁）。\n"
        "3) 总评分：综合质量 + 一致性的整体 0-100 整数分。\n\n"
        "只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：\n"
        + ", ".join(f'"frame_{i+1}": <0-100 整数>' for i in range(n))
        + ', "character_consistent": <true/false>, '
        '"motion_continuous": <true/false>, '
        '"spatial_stable": <true/false>, '
        '"consistency": "<中文一句话总结帧间一致性，含是否漂移/突变/闪烁>", '
        '"opinion": "<中文一句话整体结论>", "score": <0-100 整数总分>}'
    )


def _run(content: list) -> str:
    return chat([{"role": "user", "content": content}])


def cmd_check_motion(video: Path, out: Path | None) -> int:
    frames = extract_frames(video, 4)
    content = [{"type": "text", "text": MOTION_PROMPT}]
    for fr in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(fr)}"}})
    raw = _run(content)
    review = parse_json(raw)
    review["raw"] = raw
    print(json.dumps(review, ensure_ascii=False, indent=2))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_check_char(image: Path, out: Path | None) -> int:
    img = Image.open(image)
    content = [{"type": "text", "text": CHAR_PROMPT},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(img)}"}}]
    raw = _run(content)
    review = parse_json(raw)
    review["raw"] = raw
    print(json.dumps(review, ensure_ascii=False, indent=2))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_review(video: Path, ref: Path, out: Path | None) -> int:
    frames = extract_frames(video, 4)
    ref_img = Image.open(ref)
    content = [{"type": "text", "text": REVIEW_PROMPT}]
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(ref_img)}"}})
    for fr in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(fr)}"}})
    raw = _run(content)
    review = parse_json(raw)
    review["raw"] = raw
    review["pass"] = (
        int(review.get("score", 0) or 0) >= 70
        and bool(review.get("character_locked", False))
        and bool(review.get("motion_natural", False))
        and bool(review.get("spatial_stable", False))
    )
    print(json.dumps(review, ensure_ascii=False, indent=2))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def review_filmstrip(video: Path, n: int = 4, ref: Path | None = None) -> dict:
    """抽取 n 帧 -> 合成 1 张 filmstrip -> 单次 VLM 调用 -> 返回帧级评分 JSON。

    返回 dict 形如：
      {"frame_1": 88, "frame_2": 75, ..., "frame_n": 62,
       "character_consistent": bool, "motion_continuous": bool, "spatial_stable": bool,
       "consistency": "<中文一句话>", "opinion": "<中文一句话>", "score": int,
       "raw": <VLM 原文>, "video": str(video), "n": int}

    与 `review` 的区别：
      - 不依赖独立 ref 图；以相邻帧互为参考
      - 1 张 image 输入（filmstrip 图），具备横向对比能力
      - 帧级评分 + 一致性结论，而非单一 overall score
    """
    frames = extract_frames(video, n)
    content: list = [{"type": "text", "text": _filmstrip_prompt(n)}]
    if ref is not None:
        ref_img = Image.open(ref)
        content.append(
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{frame_to_b64(ref_img)}"}}
        )
        content.append({"type": "text", "text": "上方为角色定妆参考图，下方 filmstrip 为视频抽帧。"})
    # 一次 image 输入：filmstrip JPEG（≤200KB），带 data: 前缀直接可被 VLM 解析
    content.append(
        {"type": "image_url",
         "image_url": {"url": _fs.filmstrip_b64(frames, labels=True, target_kb=200)}}
    )
    raw = _run(content)
    review = parse_json(raw)
    review["raw"] = raw
    review["video"] = str(video)
    review["n"] = n
    # pass 判定：与现有 review 阈值对齐（score>=70 且三项一致性都为 true）
    review["pass"] = (
        int(review.get("score", 0) or 0) >= 70
        and bool(review.get("character_consistent", False))
        and bool(review.get("motion_continuous", False))
        and bool(review.get("spatial_stable", False))
    )
    return review


def cmd_review_filmstrip(video: Path, n: int, ref: Path | None, out: Path | None) -> int:
    review = review_filmstrip(video, n=n, ref=ref)
    print(json.dumps(review, ensure_ascii=False, indent=2))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("check_motion")
    p1.add_argument("--video", required=True)
    p1.add_argument("--out", default=None)

    p2 = sub.add_parser("check_char")
    p2.add_argument("--image", required=True)
    p2.add_argument("--out", default=None)

    p3 = sub.add_parser("review")
    p3.add_argument("--video", required=True)
    p3.add_argument("--ref", required=True)
    p3.add_argument("--out", default=None)

    p4 = sub.add_parser("review_filmstrip")
    p4.add_argument("--video", required=True)
    p4.add_argument("--n", type=int, default=4, help="抽帧数量（N=4 自动用 2x2 网格，其余 1xN）")
    p4.add_argument("--ref", default=None, help="可选：1 张角色定妆参考图")
    p4.add_argument("--out", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "check_motion":
        return cmd_check_motion(Path(args.video), Path(args.out) if args.out else None)
    if args.cmd == "check_char":
        return cmd_check_char(Path(args.image), Path(args.out) if args.out else None)
    if args.cmd == "review":
        return cmd_review(Path(args.video), Path(args.ref), Path(args.out) if args.out else None)
    if args.cmd == "review_filmstrip":
        return cmd_review_filmstrip(
            Path(args.video),
            n=args.n,
            ref=Path(args.ref) if args.ref else None,
            out=Path(args.out) if args.out else None,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
