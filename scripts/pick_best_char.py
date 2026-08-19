#!/usr/bin/env python3
"""用 VLM 给 Z-Image 生成的 6 张角色定妆图打分，选最佳。

调用 minimax-m3 VLM：
- 对每张图给 0-100 综合分
- 评估维度：人设匹配度（发色/瞳色/服装/气质）+ 画面质量 + 构图（半身/正面）
- 每角色挑分数最高的图，复制为 senior_ref.png / junior_ref.png

CLI:
    python scripts/pick_best_char.py --same-dir D:/ai-video-pipeline/output/same_v1
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vlm_config import API_KEY, API_URL, MODEL  # noqa: E402


CHAR_CHECK_PROMPT = """你是一名 AI 角色定妆图质量审查员。我会给你一张动漫风格的二次元角色定妆图。

请按以下维度严格评估（0-100 整数打分）：

1. **人设匹配度**（最关键）：
   - 学姐（senior）：银白长发 + 亮粉色瞳孔 + 黑色哥特朋克皮夹克 + 骷髅元素（骷髅耳坠/项链/T恤图案）+ 暗黑冷艳气质
   - 学妹（junior）：黑色中长发 + 暖棕色瞳孔 + 日系水手服（白衬衫 + 蓝色格纹百褶裙 + 红蓝领结）+ 小熊挂件（TEDDY CHARM）+ 元气活泼气质
2. **画面质量**：清晰度、光影、色彩饱和度
3. **构图**：半身以上 + 主体清晰可辨（不要全身小人在远处）

只返回一个 JSON 对象（不要 markdown 代码块、不要额外文字），字段严格如下：
{"character_match": <0-100 整数>, "image_quality": <0-100 整数>, "composition": <0-100 整数>, "score": <0-100 综合分>, "verdict": "<中文一句话结论>"}

综合分 = 人设匹配*0.5 + 画面质量*0.3 + 构图*0.2。"""


def _img_b64(path: Path, target_kb: int = 80) -> str:
    from PIL import Image
    import io
    img = Image.open(path).convert("RGB")
    img.thumbnail((768, 768))
    quality = 78
    for _ in range(6):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if len(buf.getvalue()) <= target_kb * 1024:
            break
        quality -= 12
    return base64.b64encode(buf.getvalue()).decode()


def chat(messages: list, attempts: int = 3) -> str:
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


def score_image(path: Path) -> dict:
    b64 = _img_b64(path)
    content = [
        {"type": "text", "text": CHAR_CHECK_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]
    raw = chat([{"role": "user", "content": content}])
    review = parse_json(raw)
    review["image"] = str(path)
    review["raw"] = raw
    return review


def pick_best(images: Iterable[Path]) -> tuple[Path, dict]:
    best_path = None
    best_review = None
    best_score = -1
    for p in images:
        if not p.is_file() or not p.name.endswith(".png"):
            continue
        print(f"  审查 {p.name} ...", flush=True)
        try:
            review = score_image(p)
            score = int(review.get("score", 0) or 0)
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: {p.name} 审查失败: {e}", flush=True)
            continue
        print(f"  → score={score} verdict={review.get('verdict', '')[:80]}", flush=True)
        if score > best_score:
            best_score = score
            best_path = p
            best_review = review
    return best_path, best_review


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VLM 选最佳角色定妆图")
    ap.add_argument("--same-dir", required=True, help="output/same_v1 目录")
    args = ap.parse_args(argv)

    root = Path(args.same_dir)
    senior_dir = root / "char_pack" / "senior"
    junior_dir = root / "char_pack" / "junior"

    print("=== 审查 senior 三张 ===", flush=True)
    senior_best, senior_review = pick_best(sorted(senior_dir.glob("senior_v*.png")))
    if senior_best:
        shutil.copy(senior_best, senior_dir / "senior_ref.png")
        print(f"[pick] senior 最佳: {senior_best.name} (score={senior_review.get('score')}) → senior_ref.png", flush=True)

    print("=== 审查 junior 三张 ===", flush=True)
    junior_best, junior_review = pick_best(sorted(junior_dir.glob("junior_v*.png")))
    if junior_best:
        shutil.copy(junior_best, junior_dir / "junior_ref.png")
        print(f"[pick] junior 最佳: {junior_best.name} (score={junior_review.get('score')}) → junior_ref.png", flush=True)

    # 写 char_bible.json
    char_bible = {
        "version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "characters": [],
    }
    if senior_best and senior_review:
        char_bible["characters"].append({
            "name": "学姐",
            "tag": "senior",
            "ref_image": str((senior_dir / "senior_ref.png").relative_to(root)).replace("\\", "/"),
            "appearance": "银白长发及腰，亮粉色瞳孔带星芒高光，皮肤白净，脸型瘦削，黑色哥特朋克系皮夹克，内搭骷髅图案T恤，黑色皮短裙，黑色长靴，骷髅耳坠与骷髅项链",
            "personality": "成熟冷艳，暗黑偶像气质，少言寡语",
            "speech_style": "语速慢，音调低沉磁性",
            "vlm_score": senior_review.get("score"),
            "vlm_verdict": senior_review.get("verdict"),
        })
    if junior_best and junior_review:
        char_bible["characters"].append({
            "name": "学妹",
            "tag": "junior",
            "ref_image": str((junior_dir / "junior_ref.png").relative_to(root)).replace("\\", "/"),
            "appearance": "黑色中长发，发尾微卷，暖棕色瞳孔，脸型圆润带婴儿肥，日系水手服白色衬衫搭配蓝色格纹百褶裙与红蓝领结，胸前挂小熊挂件（TEDDY CHARM），白色过膝袜，棕色学生皮鞋",
            "personality": "元气活泼，清纯可爱，表情丰富",
            "speech_style": "语速轻快，语调上扬",
            "vlm_score": junior_review.get("score"),
            "vlm_verdict": junior_review.get("verdict"),
        })
    bible_path = root / "char_bible.json"
    bible_path.write_text(json.dumps(char_bible, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pick] char_bible.json -> {bible_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
