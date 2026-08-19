# ai-video-pipeline v3.0 架构说明

> 版本：v3.0
> 日期：2026-08-18
> 依据：`abtest_report.txt`（A/B 对照实验结论）+ `docs/v3-lora-verdict.md`（LoRA 路径否决）

## 1. 一句话架构

**`script → prompt-pack → video → overlay → audio → final`**

六阶段纯 T2V 流水线，**显式放弃 v1 的 R2V 约束链**（实验证负增益），**Plan B 强化 prompt 风格锚定**（替代不存在的 H3 LoRA），**强制 overlay 阶段**（消除 A 组 40 分根因）。

## 2. v3 vs v1 阶段对比

| v1 链路 | v3 链路 | 差异 |
|---|---|---|
| `script` | `script` | 不变 |
| `image`（Z-Image 定妆） | **删除** | abtest B 组证明：定妆图约束构图是负增益 |
| `character`（R2V ref2va） | **删除** | abtest B 组证明：R2V 锁角色+锁动作累加后严重压低 H3 上限 |
| —— | **`prompt-pack`**（新增） | 替代被删除的两阶段，纯文本强描述 + Plan B 风格锚定 |
| `video`（R2V base + ffmpeg 衍生） | `video`（H3 T2V 纯直出） | 8 段独立生成，独立 seed，无 ffmpeg 衍生 |
| `overlay`（optional） | `overlay`（required） | A 组 40 分根因就是没做 overlay；v3 强制 |
| `audio` | `audio` | 不变 |
| `final`（VLM 评分不强制） | `final`（VLM ≥ 60 硬指标） | 不达标则报告标红，不静默退出 |

## 3. 为什么弃 R2V（v1 的 character 阶段）

来自 `abtest_report.txt` §5.0 决策矩阵：

| 维度 | B 组（v1） | A 组（纯 T2V） | A - B |
|---|---:|---:|---:|
| 画面质量 | 35 | 62 | **+27** |
| 角色一致性 | 40 | 45 | +5 |
| 镜头语言 | 30 | 52 | **+22** |
| 动作流畅度 | 30 | 60 | **+30** |
| 风格与氛围 | 48 | 45 | -3 |
| 制作完成度 | 40 | 40 | 0 |
| 档次定位 | 25 | 48 | **+23** |
| **综合加权** | **36.0** | **50.1** | **+14.1** |

R2V 锁角色 + 锁动作的代价：
1. **R2V 单镜 6-7 分钟**：8 段视频累计耗时从 3.3h 涨到 5h+
2. **ffmpeg 衍生**：VLM 反复扣分"镜头语言 30"——画面内容重复
3. **三层约束累加**：H3 模型自由度被压没，反而比纯 prompt 直出低 14.1 分

**v3 决策**：纯 prompt 强描述（角色外貌/服装/气质逐段一致措辞）+ 独立 seed（10001-10010）维持角色一致性；不再用 R2V 锁角色。

## 4. 为什么弃 Z-Image 定妆（v1 的 image 阶段）

来自 `abtest_report.txt` §5.2：

> **R2V 单镜 6-7 分钟 + Z-Image 6 张定妆** 耗时巨大（v1 整个流程 5 小时），但产出质量反向。

Z-Image 定妆目的是"锁角色"，但实验证明：
- **A 组（无定妆）角色一致性 45 > B 组（Z-Image + R2V 锁）40**——锁角色反而更低
- 原因是：定妆图把角色锁死在生成时的诠释上，反而让后续 R2V 失去风格自由度
- 时间成本 6 张定妆 + 选优 ≈ 30-45 分钟，性价比为负

**v3 替代**：纯 prompt 角色描述块逐段绝对一致，详见 `pipeline.yaml` `prompt-pack.gate` 校验规则。

## 5. 为什么用 Plan B 强化 prompt（替代 LoRA）

来自 `docs/v3-lora-verdict.md`：

| 项 | 现状 |
|---|---|
| H3 LoRA 节点 | ✅ ComfyUI 支持（LoraLoaderModelOnly / HunyuanVideoBlockLoraSelect / LoraModelLoader） |
| H3 二次元/赛璐璐 LoRA | ❌ 公开生态 = 0 |
| HV-1.0 二次元 LoRA | ⚠️ 存在但架构不兼容（H3 是 flat blocks.X，HV-1.0 是 double_blocks + single_blocks） |
| 时间预算 | 训练/键映射 hack 风险 > 收益 |

**Plan B 核心思路**：
1. **STYLE_BLOCK 前置**：6-8 个风格关键词放 prompt 最前面（H3 文本编码器对开头敏感）
2. **重复 + 反向**：mid 段复述 + ANTI_BLOCK 显式排除（no CGI / no photorealism）
3. **场景模板约束**：用具体可识别二次元模板名（"Studio Ghibli pastel wash"）缩小自由度

## 6. v3 各阶段职责清单

### Stage 1: `script`
- 输入：`user_brief.md`
- 输出：`sb/storyboard.json`（shots[]、camera、duration、narration）
- v3 新增字段：`char_blocks`（角色引用）、`scene_blocks`（场景锚定）、`style_strategy: "plan_b_prompt_reinforcement"`
- Gate：`script-review`（双 agent 互评）

### Stage 2: `prompt-pack`（v3 新增，替代 v1 image+character）
- 输入：`storyboard.json` + A 组角色块（`output/abtest/prompts_a.md`）
- 输出：每段 H3 官方三段式 prompt（integrated_multimodal_description + overall_soundscape + non_diegetic_music）
- 强约束：
  - STYLE_BLOCK（Plan B 强化版）每段绝对一致
  - CHAR_BLOCK（学姐/学妹）每段绝对一致
  - SCENE_BLOCK 按 shot 差异化
  - ANTI_BLOCK 每段绝对一致
- Gate：`prompt-pack-consistency`（字符数 1700-2500 + diff = 0 校验）

### Stage 3: `video`
- 输入：`clips/shot{NN}_prompt.txt` + `shot{NN}_meta.json`
- 输出：`clips/shot{NN}.mp4`（H3 T2V 直出，768×1344, 8s, 24fps）
- ComfyUI 8188 排队，独立 seed (10001-10010)
- **不**接入 LoRA（已验证不可行）
- Gate：`video-selfcheck`（filmstrip 自查；写实质感偏离 ≥ 30% 换 seed 重跑 1 次）

### Stage 4: `overlay`（v3 强制）
- 输入：`storyboard.json` + `clips/shot{NN}.mp4`
- 输出：`overlays/` + `clips/shot{NN}_with_overlay.mp4`
- 必做：标题卡"选学姐还是学妹？" + 角色名标签 + 字幕条 + 片尾卡
- Gate：`overlay-render`（时长与原视频 ±0.1s）

### Stage 5: `audio`
- 输入：`storyboard.json` + `clips/shot{NN}.mp4` + `overlays/`
- 输出：`audio/shot{NN}_mix.wav` + `final_mix.wav`
- edge-tts 中文配音 + J-pop pastel BGM + 响度归一 -16 LUFS
- Gate：`audio-check`（时长/响度/音画同步）

### Stage 6: `final`
- 输入：`clips/shot{NN}_with_overlay.mp4` + `audio/final_mix.wav` + `input_douyin_ref.mp4`
- 输出：`final_v3.mp4`（720×1280, h264+aac, 60-80s）+ `qa/*` + 顶层 `v3_report.txt`
- VLM 七维度评分（口径与 abtest 完全一致：`minimax-m3` + 同权重 + 7 维）
- Gate：`content-rules`（**综合分 ≥ 60 硬指标**，不达标则报告标红）

## 7. 评分口径继承

v3 评分与 `abtest_report.txt` §1.3 完全一致：
- 七维度：画面质量 / 角色一致性 / 镜头语言 / 动作流畅度 / 风格与氛围 / 制作完成度 / 档次定位
- 权重：1.0 / 1.5 / 1.5 / 1.0 / 1.0 / 1.0 / 1.0（合计 8.0）
- VLM 模型：`minimax-m3`（与 v1 报告同模型）
- 评分方式：单次调用同时发参考视频 + v3 成片 filmstrip 对比打分（与 abtest 同口径）

## 8. 时间预算与风险

| 阶段 | 预估耗时 |
|---|---|
| Stage 1-2 脚本 + prompt-pack | 30 分钟 |
| Stage 3 T2V 8 段（含 GPU 排队） | 3.3 小时（与 abtest A 组持平） |
| Stage 4 overlay | 30 分钟 |
| Stage 5 audio | 30 分钟 |
| Stage 6 final + VLM 评分 | 30 分钟 |
| **总计** | **5-6 小时** |

主要风险：
- **H3 GPU 排队时间不可控**：A 组 3.3h 中实际 GPU 仅 70-90 分钟，2.5h 是排队
- **VLM 评分 ±5 分波动**：本次目标 ≥ 60 应理解为 55-65 区间
- **单段重跑 ≤ 1 次**：避免无限递归

## 9. 下一轮（v4）升级路径

| 实验 | 内容 | 预期分 |
|---|---|---:|
| v3（本轮） | 纯 T2V + Plan B prompt + overlay + audio | 60-65 |
| v4 | 等 MiniMax 官方发布 H3 anime LoRA → 接入 Plan B | 65-70 |
| v5 | v4 + 关键动作 R2V 局部替换（abtest §6.3 实验 E） | 70-75 |

## 10. 关键文件路径速查

```
D:\ai-video-pipeline\
├── pipeline.yaml                                # v3 主架构（已重构）
├── docs\
│   ├── v3-lora-verdict.md                       # LoRA 不可行验证报告
│   └── pipeline-v3.md                           # 本文件
├── output\abtest\                               # A 组基线（已存在，复用 prompt 块）
│   ├── final_a.mp4                              # 50.1 分基线产物
│   └── prompts_a.md                             # 角色块来源
├── output\pipeline_v3\                          # v3 产物（待生成）
│   ├── sb\storyboard.json
│   ├── clips\
│   │   ├── style_block.txt
│   │   ├── char_blocks.json
│   │   ├── shot{NN}_prompt.txt
│   │   ├── shot{NN}_meta.json
│   │   ├── shot{NN}.mp4
│   │   └── shot{NN}_with_overlay.mp4
│   ├── overlays\
│   ├── audio\
│   ├── final_v3.mp4
│   └── qa\
└── v3_report.txt                                # 顶层报告（待生成）
```