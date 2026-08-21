# H3 AI 视频管线进度存档（v3.6.x）

> 最后更新：2026-08-21 | 分支：v367 | 仓库：D:\ai-video-pipeline（Gousting/ai-video-pipeline）

## 1. 一句话现状

v3.6.7（v367）已跑通"参考视频视觉识别 + ReferenceToVideo 参考直出"路线，方向经用户确认正确，色彩/人物对齐参考视频（直方图余弦 0.92 / mean RGB 0.98）。**待办：解决分段清晰度不均问题（shot04/06 偏糊）。**

## 2. 版本迭代历史（v3.1 → v3.6.7）

| 版本 | commit | 方向 | 结果 |
|------|--------|------|------|
| v3.1 | - | 段间引导（尾帧I2V锚定） | 62.2 分，角色一致性 56（不足，需 IP-Adapter/LoRA） |
| v3.2 | - | 抖音 AI 赛先生 H3 PV 还原 | 参考基准 68.5，米山舞风格+快切转场进 prompt |
| v3.6.0 | bddfd42 | 节奏治理代码（rhythm_planner/speed_segment） | V1-V5 全 PASS |
| v3.6.1 | c18ba52 | 60s 成片实测 + prompt_pack_v35 | - |
| v3.6.3 | 8ea892d | 方案B 修复 xfade 边界 bug | 成片重拼 + VLM 评审 |
| v3.6.4 | 8095a99 | 全流程重构：竖屏 768x1344/yuv420p/快慢呼吸/无白淡入 | 重构成功 |
| v3.6.5 | be33600 | 缝回 v3.2 生成层转场特效（5 个转场全片去重） | 转场净面 95，**生成层转场可见仅 45（H3 接不住抽象转场词）** |
| v3.6.6 | 6de8aa0 | 链式衔接 + 统一配音（横屏 1344x576） | **失败：人物漂移/色彩风格与首帧无关** |
| **v3.6.7** | **9976a88** | **参考视频视觉识别 + ReferenceToVideo 参考直出** | **方向正确，色彩/人物对齐参考** |

## 3. v367 成果详情（当前主分支）

**核心修复**（相对 v366 的两个根子错误）：
1. **先看图再写 prompt**：opencode 用 minimax-m3 VLM 分析 12 个参考帧，产出 345 行视觉风格档案 `ref_analysis_v367/v367_style_profile.md`。识别出参考视频实为**单角色 Color Riot Girl 高饱和 CMYK pop-art 时尚编辑 MV**（v366 凭空虚构"学姐学妹校园恋爱"，内容完全错误）。
2. **换用 `MiniMaxH3ReferenceToVideo` 节点**：4 张 ref_images + `ref_image_size=max`，参考 token 贯穿每个采样步（v366 用 `MiniMaxH3ImageToVideo` 首帧续写锁不住）。

**成片**：`output/pipeline_v36/final_v36_60s_v367.mp4`，40.96s / 1344x576 / yuv420p / 24fps

**验证数据**：
- 格式/分辨率/pix_fmt/YAVG：6/6 PASS
- 色彩直方图 vs 参考：mean cosine **0.9219**（阈值 0.65）
- mean RGB vs 参考：**0.9826**（ΔRGB=[5,5,3]）
- 遗留：shot05 split-screen 余弦 0.819（8 对最低）

## 4. 已知问题：分段清晰度不均（待优化）

用户反馈"有的帧很糊，有的帧很清晰"。**客观量化诊断（Laplacian 锐度）**：

| 片段 | 锐度均值 | 中位数 | 状态 |
|------|---------|--------|------|
| shot01 | 1193 | 1265 | 清晰 |
| shot02 | 1189 | 1244 | 清晰 |
| shot03 | 1302 | 1305 | 清晰 |
| **shot04** | **551** | **346** | **很糊**（集中在成片 20-26s） |
| shot05 | 1246 | 1242 | 清晰 |
| **shot06** | 684 | 704 | 偏糊 |

**根因判断**：shot04 运动度 4.56 与清晰段相当（排除动态模糊），是 **H3 分段生成的不稳定发挥**——同参数下部分段渲染质量差。shot06 偏糊因运动度全场最高（5.2）。

**待办方案（用户已认可方向未选型）**：
- A. 重投 shot04/06（换 seed，快）
- B. **清晰度门控**（生成后自动算 Laplacian 锐度，低于阈值自动重投，治本）

## 5. Git 状态

- `origin/main` = 2648847（v3.6.4→v3.6.7 全代码 + docs 经验文档）
- `origin/v367` = 9976a88（v367 独立分支）
- v366/v365 已包含在 main 历史中（作为 v367 祖先）
- 当前 checkout：v367 分支（清晰度优化待续）

## 5.5 项目边界（2026-08-21 明确）

**当前唯一主项目：ai-video-pipeline**（Gousting/ai-video-pipeline，宿主机 D:\ai-video-pipeline，Python 脚本自行构造 MiniMaxH3ReferenceToVideo workflow，零 wind-comic 代码依赖）。

**wind-comic 已废弃归档**（2026-08-21）：
- 宿主机：D:\wind-comic → D:\_archive\wind-comic-archived_20260821
- 本地：~/reference/ai-video-pipeline（⚠️ 此目录名起得混乱，内部实为 wind-comic 源码，Next.js 应用）→ ~/reference/_archive/wind-comic-archived_20260821
- 废弃理由：其 I2V（首尾帧参考生成）能力已被 ai-video-pipeline 的 v367 ReferenceToVideo 参考直出全面覆盖

⚠️ **目录命名提醒**：本地 ~/reference/ai-video-pipeline 曾误装 wind-comic 源码。真正的 avp 在宿主机 D:\ai-video-pipeline。以后涉及"ai-video-pipeline"一律指宿主机 D:\ai-video-pipeline（远端 Gousting/ai-video-pipeline），本地 reference 下的同名目录已归档清理。

## 6. 关键经验（防止重蹈覆辙）

1. **H3 生成 prompt 前必须先用 VLM 看图**，识别参考视频真实风格，不能凭空写
2. **用 `MiniMaxH3ReferenceToVideo`（参考直出）而非 `MiniMaxH3ImageToVideo`（首帧续写）**
3. 链式尾帧衔接会**放大漂移**，段间应独立以参考为准
4. 直方图余弦只能证明"色彩分布连续"，**不能证明"人物一致"**，需 VLM 视觉复核
5. 抽象转场特效词（halftone/fabric wipe/分屏）H3 接不住，v3.6.5 已证明（可见度仅 45）
6. 分段生成存在**质量波动**，需清晰度门控（Laplacian 锐度阈值 + 自动重投）
