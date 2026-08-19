# v3.4 VLM Gate Report

- Generated: 2026-08-19T19:32:26
- Mode: **FAKE INPUT (no real VLM)**
- Inputs: 1

## Overall

✅  Overall: **PASS**
- Score: 80/100
- Opinion: fake-input self-test passed (no real VLM invoked)

## Checks

| Check | Pass | Severity | Reason |
|-------|------|----------|--------|
| composition_integrity | ✅ | none | fake: subject framed fully, both eyes visible, no half-face cropping |
| motion_naturalness | ✅ | none | fake: motion snappy (~1.5s per action), no dragging |
| music_transition | ✅ | none | fake: 120 BPM grid aligned, no jarring whoosh at cuts |

## Inputs

- `output\pipeline_v33_line0\clips_v33_30s\shot01.mp4` (video #0)

## Method

v3.4 VLM 门禁审核（可复用工具）。本任务**只实现脚本**，不调用真实 VLM：

- `composition_integrity`（构图完整性）：终幅构图完整、主体不裁半
  - 与 P3 well-framed composition 对应（`the camera pulls back to frame the ENTIRE head and shoulders, both eyes clearly visible`）
- `motion_naturalness`（动作自然度）：无明显拖沓/僵硬
  - 与 P2 单动作链 + then 表对应（`fast, snappy, completes in ~1.5s`）
- `music_transition`（音乐衔接）：合并片视角节拍对齐、无刺耳突兀
  - 与 P1 BGM 后期化对应（`120 BPM beat grid, ≤41ms alignment`）

`--fake-input` 模式：跳过 VLM 调用，用确定性假结果验证脚本链路 + 报告格式。
