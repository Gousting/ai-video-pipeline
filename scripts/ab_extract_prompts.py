#!/usr/bin/env python3
"""从 output/abtest/prompts_a.md 提取 8 段 prompt 到 a_shot{1-8}.txt。

策略：
- 找 "## 1. 段 01 — 开场建立镜头" 等 8 个标题
- 每段内容到下一个 "## N." 或 "## 9." 之前
- 去掉 "**首帧 prompt**" 行（占位说明）
- 去掉 "视频 prompt" 提示行
- 写到 output/abtest/a_shot{n:02d}.txt
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
SRC = ROOT / "output" / "abtest" / "prompts_a.md"
OUT_DIR = ROOT / "output" / "abtest"


def extract() -> dict[int, Path]:
    src = SRC.read_text(encoding="utf-8")
    # 匹配 "## N. 段 XX — 标题" 段标题
    pattern = re.compile(r"^## (\d+)\. 段 (\d+)\s*—\s*(.+?)$", re.MULTILINE)
    headings = []
    for m in pattern.finditer(src):
        section_num = int(m.group(1))
        shot_num = int(m.group(2))
        title = m.group(3).strip()
        headings.append((section_num, shot_num, title, m.start(), m.end()))
    # 找段 9 之前的最后位置
    stop_match = re.search(r"^## 9\. 段元数据", src, re.MULTILINE)
    stop_pos = stop_match.start() if stop_match else len(src)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = {}
    for i, (sec, shot, title, start, hm_end) in enumerate(headings):
        # 段内容 = 从当前标题末尾到下一段标题（或 stop_pos）
        if i + 1 < len(headings):
            content_end = headings[i + 1][3]
        else:
            content_end = stop_pos
        body = src[hm_end:content_end].strip()
        # 去掉 "**首帧 prompt**：..." 这行（占位说明）
        body = re.sub(r"^\*\*首帧 prompt\*\*[^\n]*\n", "", body, flags=re.MULTILINE)
        # 去掉 "**视频 prompt**（完整 ...）" 这行
        body = re.sub(r"^\*\*视频 prompt\*\*[^\n]*\n", "", body, flags=re.MULTILINE)
        # 去掉单独成行的 "**视频 prompt**：" 或 "**视频 prompt**:"
        body = re.sub(r"^\*\*视频 prompt\*\*[：:]\s*\n", "", body, flags=re.MULTILINE)
        body = body.strip()
        # 去掉 markdown 围栏：
        # - 开头的 ```... 单独一行（含 ``` 或 ```markdown）
        body = re.sub(r"^```[^\n]*\n", "", body)
        # - 末尾的 ``` 单独一行及其后随所有空白（DOTALL 让 . 匹配换行）
        body = re.sub(r"\n```.*\Z", "", body, flags=re.DOTALL)
        body = body.strip()
        out = OUT_DIR / f"a_shot{shot:02d}.txt"
        out.write_text(body, encoding="utf-8")
        written[shot] = out
        print(f"[extract] shot{shot:02d} -> {out} ({len(body)} chars)")
    return written


def main() -> int:
    extract()
    return 0


if __name__ == "__main__":
    sys.exit(main())
