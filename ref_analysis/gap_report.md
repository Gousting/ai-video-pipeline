# 差距评估报告：参考爆款 vs 我们 final_v6.mp4

> **任务**：用 minimax-m3 VLM 分析抖音爆款 `input_douyin_ref.mp4`（"选学姐还是学妹？"），评估我们项目（`D:\ai-video-pipeline`）当前产出 `final_v6.mp4` 与之差距，并给出具体追赶清单。
>
> **方法**：ffmpeg 抽帧 → PIL 合成 filmstrip → VLM chat completion 多维度评分 → 对比 + 落到模块的改进项。
>
> **时间**：2026-08-17  |  **VLM**：`minimax-m3`（https://opencode.ai/zen/go/v1/chat/completions）

---

## 0. 基本数据对比

| 维度 | 参考视频 `input_douyin_ref.mp4` | 我们 `final_v6.mp4` | 差距方向 |
|---|---|---|---|
| 时长 | **72.875s** | 15.5s | -57.4s（**4.7 倍**） |
| 分辨率 | 360×480 竖屏 9:16 | 1920×1080 横屏 16:9 | 比例**完全相反** |
| 帧率 | **8 fps**（AI 直出特征） | 24 fps（正常） | 参考反而是低 fps |
| 编码 | h264 | h264 + aac | 持平 |
| 大小 | 1.83 MB / 201 kbps | 5.65 MB / 2.92 Mbps | — |
| 抽帧覆盖 | 24 帧 × 3s 间隔 = 72s | 16 帧 × 1s 间隔 = 16s | — |

> **关键发现 1**：时长差距（72.8s vs 15.5s）意味着**叙事密度差 4.7 倍**——参考视频能塞进 14-18 个独立镜头 + 教程段，我们只能塞 3 个镜头。
>
> **关键发现 2**：**画面比例完全错位**——参考是 9:16 竖屏（抖音信息流原生），我们是 16:9 横屏（PC/横屏播放器原生）。在抖音信息流里横屏视频会被上下加黑边，体验先天掉档。

---

## 1. VLM 七维度评分对比

> VLM 输出完整版见：
> - 参考：`ref_analysis/vlm_report_raw.md`（8066 字，max_tokens 截断在"档次定位"小节）
> - 我们：`ref_analysis/vlm_report_our_raw.md`（8689 字，max_tokens 截断在"档次"小节）

| # | 维度 | 参考视频 (/100) | 我们 (/100) | 差距 | 谁赢 |
|---|---|---:|---:|---:|:---:|
| 1 | 画面质量 | 65 | 68 | +3 | **我们**略好（分辨率高） |
| 2 | 角色一致性 | 70 | 70 | 0 | **持平** |
| 3 | 镜头语言 | 65 | **48** | **-17** | 参考大胜 |
| 4 | 动作流畅度 | 55 | 58 | +3 | **我们**略好 |
| 5 | 风格与氛围 | 80 | 74 | -6 | 参考略胜 |
| 6 | 制作完成度 | 75 | **50** | **-25** | 参考大胜 |
| 7 | 档次定位 | 70-75 | 55-65 | ~-10 | 参考胜 |

**加权综合**（画面×1 + 角色×1.5 + 镜头×1.5 + 动作×1 + 风格×1.2 + 包装×1.5 + 档次×1）：
- 参考：(65×1 + 70×1.5 + 65×1.5 + 55×1 + 80×1.2 + 75×1.5 + 72×1) / 8.7 ≈ **68.5**
- 我们：(68×1 + 70×1.5 + 48×1.5 + 58×1 + 74×1.2 + 50×1.5 + 60×1) / 8.7 ≈ **59.3**

**综合差距：-9.2 分**，档次大致是"AI 短视频 demo / 工程样本" vs "AI 短视频腰部爆款"。

---

## 2. 参考视频的 VLM 关键结论（原文引用）

### 1) 画面质量 — 65/100
> "360×480 偏小，竖屏信息量有限；放大后线条发糊，但用于抖音信息流缩略观看尚可接受"
> "近景（眼睛、锁骨、装饰）锐度尚可；远景和 UI 区有明显马赛克/字迹糊"
> "高饱和度粉/品红 + 黑 + 白三段式，色块干净，**无明显偏色**"
> "**典型 8 fps AI 直出**质感，未做 upscale，但风格化补偿了精度不足"

### 2) 角色一致性 — 70/100
> "学姐（白发粉瞳 / 哥特骷髅风）稳定帧：#01 / #02 / #03 / #12 / #18"
> "学妹（黑发棕瞳 / 校园风 + 熊挂件）稳定帧：#06 / #07 / #08 / #11 / #19 / #21 / #24"
> "两位角色在跨镜头间**未发生串脸/换装**，整体一致性属中等偏上"
> 风险点：#08 手指数量疑似异常，#19 与 #21 眼睛大小有轻微差异

### 3) 镜头语言 — 65/100（**我们的最大短板参照**）
> "全景/中景/近景/极端特写分布合理"
> "受 8 fps 限制，几乎全为**静帧切换**，未见明显推/拉/摇"
> "粗估 **14–18 个**独立机位/画面，3 秒一切节奏"
> "前半段 PV 感强、节奏快（#01–#12 平均 3 秒一切）；后半段教程拖慢"

### 4) 动作流畅度 — 55/100
> "**8 fps 限制**：连续动作几乎不可辨，多为'动态姿势'单帧而非真实运镜"
> "#08、#21 出现**持物手/装饰手结构异常**"
> "整体属于'**可发布但禁不起逐帧放大**'的水准"

### 5) 风格与氛围 — 80/100（**参考的最大加分项**）
> "高饱和品红 + 纯黑 + 雪白，辅以学妹棕熊/暖棕点缀；冷暖对撞感强烈"
> "偏向**赛璐璐厚涂混合**，有部分 3D 渲染痕迹"
> "PV/MV 感很强，'学姐 = 暗黑偶像 / 学妹 = 元气学园'反差叙事成立"
> "✅ 符合抖音 AI 动漫爆款的视觉范式"

### 6) 制作完成度 — 75/100
> "标题/封面卡'一张图生成角色PV教程' ✅"
> "角色标签'学姐'/'学妹'角标左上角 ✅"
> "提示词区块 + 教程 UI 录屏（minimax h3 工作流截图） ✅"
> "字幕/步骤注释'输入这段提示词'、'模型选sd5.0lite…'、'自取即可' ✅"
> "片尾/CTA 引导进群自取 ✅"
> ⚠️ **缺**：仅标题文字，无个人 Logo
> ❌ **缺**：filmstrip 无从判断 BGM/配音，但教程段有文字替代配音

### 7) 档次定位 — 70-75（VLM 截断但有结论性段落）
> "Tutorial format gives it strong retention/save value"
> "Save count is unusually high (close to likes), indicating strong utility value"
> "Compared to top AI anime accounts on Douyin, this is mid-tier/lumbar tier content"

---

## 3. 我们 final_v6.mp4 的 VLM 关键结论（原文引用）

### 1) 画面质量 — 68/100
> "1920×1080 横屏，整体清晰度可接受，未见明显低分辨率瑕疵"
> "傍晚暖橙色调与店内冷白光形成对比（#05、#12），氛围感不错"
> "中等偏上，达到 AI 动画基线水准，但缺乏高级质感的颗粒、景深虚化与色调分级"

### 2) 角色一致性 — 70/100
> "同场景内（#06 → #10）：主角黑色发色、方框眼镜、深色大衣、面部轮廓高度稳定 ✅"
> "跨场景：#05（撑伞室内门廊）vs #06–#11（柜台）— 主体特征保持，但**#05 脸型略显圆润、#06 更瘦削，存在轻微脸型漂移**"
> "#12（室外公交站雨夜）vs #11（店内）— 发色、眼镜一致，但**#12 角色面部更平面化、轮廓简化明显，疑似独立生成镜头**"

### 3) 镜头语言 — 48/100（**全片最低分**）
> "约 **4 个**独立镜头（#05/#06–#11/#12/黑屏卡片），**分镜极度稀疏**"
> "**基本固定机位**为主，未观察到明显的推/拉/摇/移"
> "节奏：黑屏标题占 4s（#01–#04）、重复内容占 5s（#06–#10）、结尾黑屏又占 4s（#13–#16）。**实际叙事时间仅约 7 秒**，有效画面密度低"

### 4) 动作流畅度 — 58/100
> "#06 → #10（5 秒内）姿态几乎无差异，**动作幅度极小**，疑似'以静帧驱动'的低运动生成"
> "未观察到典型的手指数量异常、肢体融合、面部崩塌"
> "缺点：连续抽帧 #07–#09 几乎完全一致，存在**'PPT 循环'风险**"

### 5) 风格与氛围 — 74/100
> "明确的**日式赛璐璐动漫风**（cel-shaded），与脚本'夜晚/电车'的题材契合度高"
> "暖橙 + 冷青 + 中性灰，三段式色彩叙事 ✓"
> "风格统一是本片最大亮点"

### 6) 制作完成度 — 50/100（**全片次低分**）
> "片头/片尾合计占 **8/15.5 ≈ 52%**，有效内容被严重挤压"
> "**'Phase 8 · Demo' 与 'Rendered by ai-video-pipeline' 字样直接暴露研发阶段与 AI 生成来源，作为'成片'严重掉档**"
> "无 Logo / 角标 / 水印 / 品牌包装"
> "无版权信息"

### 7) 档次定位 — 55-65（VLM 截断）
> "Many frames are black title cards (waste of time)"
> "Limited shot variety"
> "Visible 'Demo' and 'AI pipeline' labels reduce production value"
> "This looks like a rough cut or demo, not a finished product"

---

## 4. 逐维度差距表（含证据帧号 + 差距归因）

### 4.1 画面质量（差距 +3，我们略好）

| 项 | 参考（65） | 我们（68） | 备注 |
|---|---|---|---|
| 分辨率 | 360×480 | 1920×1080 | 我们**绝对像素高 11.4 倍**，但参考用风格化补偿 |
| 细节锐度 | 近景锐度尚可；远景/UI 糊 | 中景可接受；蒸汽粒子稍糊 | 持平 |
| 色彩一致 | 高饱和粉/黑/白三段式，无偏色 | 暖橙/冷青/灰，氛围感不错 | 持平 |
| 压缩 | 黑色区块可见 banding | 帧 #06-#10 构图重复 5s | 各有瑕疵 |

**结论**：硬素质我们略胜，但优势主要来自分辨率。**单帧质量不构成追赶瓶颈**。

### 4.2 角色一致性（差距 0，持平）

| 项 | 参考（70） | 我们（70） | 备注 |
|---|---|---|---|
| 同场景稳定度 | 学姐 #01/02/03/12/18 一致；学妹 #06/07/08/11/19/21/24 一致 | #06-#10 主角高稳定 | 持平 |
| 跨场景漂移 | "可控漂移"（脸型微调） | "**#05 圆 vs #06 瘦**" + "#12 平面化疑似独立生成" | 我们**漂移更明显**，是潜在隐患 |
| 换装/换脸 | 无 | 无 | 持平 |

**结论**：绝对分相同，但**我们的跨镜头漂移更刺眼**——5 秒剪辑里有"换脸"风险。参考是双角色×多场景靠角色互衬稀释漂移感，我们是单角色×少场景，漂移一眼可见。

### 4.3 镜头语言（差距 -17，**最大短板**）

| 项 | 参考 | 我们 | 差距倍数 |
|---|---|---|---|
| 镜头数（独立机位/画面） | **14-18 个** | **约 4 个**（#05 / #06-#11 / #12 / 黑屏卡片） | **3.5-4.5 倍** |
| 景别 | 全景/中景/近景/极端特写 4 档 | 中景/中近景/中远景 3 档（无特写） | 缺极端特写 |
| 运镜 | 几乎全静帧切换（受 8fps 限制） | **基本固定机位** | 持平（都很弱） |
| 构图 | 大量居中对称、ID 卡/海报式 | 三分法构图基本到位 | 持平 |
| 节奏 | 前段 3 秒一切；教程段 6 秒一切 | **黑屏占 8/15.5s ≈ 52%；有效叙事仅 7s** | **信息密度 -3 倍** |

**根因**：故事板只有 3 个 shot × 5 秒（`output/sb/storyboard.json` 第 17/29/41 行），shot 数本身就是问题。

### 4.4 动作流畅度（差距 +3，我们略好）

| 项 | 参考 | 我们 | 备注 |
|---|---|---|---|
| 帧率 | 8 fps（低） | 24 fps | 我们帧间过渡更平滑 |
| 同帧重复 | 多为静帧切换 | #06-#10 5 秒几乎不动（**PPT 循环风险**） | 我们更差 |
| 手部伪影 | #08/#21 手指模糊 | 未见明显异常 | 我们略好 |
| 肢体融合 | 未见 | 未见 | 持平 |

**结论**：我们的"低运动"是 R2V 的**已知短板**（参考视频用静态姿势撑场，我们硬要真人运动），需要靠**分镜切换**绕开。

### 4.5 风格与氛围（差距 -6，参考略胜）

| 项 | 参考 | 我们 |
|---|---|---|
| 调性统一 | "学姐暗黑偶像 / 学妹元气学园"反差叙事成立 | "夜晚/电车"主题贴合 |
| 配色饱和度 | **高饱和品红 + 纯黑 + 雪白** | 暖橙 + 冷青 + 中性灰（饱和度偏低） |
| 二次元浓度 | 赛璐璐厚涂混合 + 3D 渲染痕迹，PV 感强 | 明确的赛璐璐动漫风 |

**结论**：参考用**饱和三色**建立强记忆点，我们用的是**环境配色**更内敛——风格选择问题，谈不上对错，但**对抖音观众，饱和度+反差是流量密码**。

### 4.6 制作完成度（差距 -25，**最致命短板**）

| 项 | 参考 | 我们 | 文件/行号 |
|---|---|---|---|
| 片头标题 | "一张图生成角色PV教程" + 角色立绘 | "城市最后一班电车" + 黑屏 | `compose_final.py:220` |
| 片头副标题 | — | "**Phase C · Compose**"（暴露研发阶段） | `compose_final.py:221` |
| 角色角标 | "学姐"/"学妹"左上角 | ❌ 无 | storyboard 字段缺失 |
| 提示词区块 | ✅ 完整提示词段落 | ❌ 无 | storyboard 字段缺失 |
| 教程 UI 录屏 | ✅ minimax h3 工作流截图（#13-#18） | ❌ 无 | pipeline 缺 stage |
| 字幕/步骤注释 | ✅ 多段中文 | ✅ overlay 字幕（仅 shot3 一句） | p5_v6_audio.py:40 |
| 片尾/CTA | ✅ 引导进群自取 | "**Rendered by ai-video-pipeline / Phase C**" + "END" | `compose_final.py:231-232` |
| Logo / 角标 | ⚠️ 无个人 Logo | ❌ 无 | storyboard 字段缺失 |
| 配音/BGM | filmstrip 不可见 | 元数据声明有 piano + 底噪 + narration | — |

**根因 1**：`compose_final.py` 第 220-232 行**硬编码**"Phase C · Compose" / "Rendered by ai-video-pipeline" 这类**demo 调试信息**作为兜底文案，导致任何没自定义 overlay 的视频都会被自动注入这段掉档文字。

**根因 2**：`storyboard.json` 缺 `overlays` 字段，触发 `--auto-overlay`（`compose_final.py:817-822` 默认开启），把 demo 文案当默认值注入。

**根因 3**：storyboard 完全没设计"教程/工作流截图/角色名角标"这类**信息密度型 overlay**——参考视频的 6 个制作完成度亮点我们一个都没有。

### 4.7 档次定位（差距 ~-10）

参考：抖音 AI 视频**腰部爆款**（点赞 3217 / 收藏 2938 / 分享 988，**收藏率 91%** 异常高 = 教程/工具价值高）。
我们：**AI 短视频 demo / 工程样本**（Demo 标签暴露研发阶段，未做发布准备）。

---

## 5. 追赶清单（按模块 + 工作量 + 优先级）

### 🔴 P0 — 不补就永远追不上（必须做）

| # | 缺口 | 改进点 | 涉及模块/文件 | 工作量 |
|---|---|---|---|---|
| **P0-1** | **画面比例错位**（横屏 vs 竖屏） | 给 `compose_final.py` 加 `--aspect 9:16` / `--width 1080 --height 1920` 快捷支持；默认值改成可配置 storyboard 里的 `composition` 字段；`render_overlays.py` 同步支持 9:16 画布 | `compose_final.py:278/841-847`、`render_overlays.py`、storyboard schema | **M**（2-3 天） |
| **P0-2** | **Demo 标签暴露**（"Phase C · Compose" / "Rendered by ai-video-pipeline / Phase C"） | 把 demo 兜底文案改成中性的"AI Generated Short"或留空；或**强制 storyboard 必须显式定义 title-card/end-card**，没有就报错而非注入 demo | `compose_final.py:213-236`、增加校验逻辑 | **S**（0.5 天） |
| **P0-3** | **时长过短**（15.5s vs 72.8s） | 把默认 `total_duration` 从 15s 提到 **45-60s**；storyboard 模板默认 shot 数从 3 个提到 **10-14 个**（每 shot 3-5s）；R2V 单 shot 长度从 124 帧（5.16s）维持，但 shot 数量翻 3-4 倍 | `examples/sample_storyboard.json`、`output/sb/storyboard.json`、pipeline.yaml 第 9-17 行 stage `script` | **M**（2-3 天改 prompt + 1 天跑通） |
| **P0-4** | **有效画面密度低**（黑屏占 52%） | 把 title-card 时长从 4.0s 降到 **1.5-2.0s**；end-card 同理降到 2.0s；空 shot 不叠黑屏 | `compose_final.py:222/234`、`render_overlays.py` 默认值 | **S**（0.5 天） |

> **P0 合计**：~1 周工作量。完成后理论上能将"制作完成度"从 50 提到 70+，整体综合分从 59 提到 65+。

---

### 🟡 P1 — 不补就差一档（强烈建议）

| # | 缺口 | 改进点 | 涉及模块/文件 | 工作量 |
|---|---|---|---|---|
| **P1-1** | **镜头数过少**（4 vs 14-18） | storyboard 自动建议器（基于 `total_duration` 算推荐 shot 数 = 时长/3 + 2）；prompt 里加约束："minimum shots = ceil(duration/3)"；validate_storyboard.py 加校验 | `scripts/validate_storyboard.py`、新增 `scripts/shot_planner.py` | **M**（3 天） |
| **P1-2** | **跨镜头角色漂移**（#05 vs #06 脸型差，#12 平面化） | 提高 R2V ref2va 的 `ref_image_size` 参数（`r2v_video_gen.py:71` 当前 "match"，可试 "cover"）；多机位复用同一 `char/ref_front.png` 强制 ID 一致；每 shot 第一帧参考图固定为同一张 | `scripts/r2v_video_gen.py`、`storyboard.json` 的 `first_frame_ref` 字段 | **M**（2-3 天，含 R2V 重跑验证） |
| **P1-3** | **运镜/景别单调**（基本固定机位） | storyboard 加 `camera` 字段（"pan_left" / "tilt_up" / "zoom_in" / "dolly_forward" / "static"），R2V prompt 里强制带上；ComfyUI workflow 加 KSampler 节点控制运镜 | `storyboard` schema、`r2v_video_gen.py:70-71` prompt 拼接 | **L**（1 周） |
| **P1-4** | **缺乏角色名角标**（"学姐"/"学妹"标签） | storyboard 加 `character_tags: [{name, color, position}]` 字段；render_overlays.py 新增 `character-tag` 模板；按 shot 切换显示 | storyboard schema、`render_overlays.py` | **S**（1 天） |
| **P1-5** | **缺乏教程/工具价值包装**（教程 UI 截图、提示词区块、CTA 进群） | 新增 pipeline stage：`script-tutorial`（脚本生成阶段产出"工作流截图"列表）；render_overlays.py 新增 `tutorial-step` / `prompt-card` / `cta-card` 模板 | pipeline.yaml、`render_overlays.py` | **L**（1-2 周） |
| **P1-6** | **同帧重复/PPT 循环**（#06-#10 5s 几乎不动） | R2V 加 `motion_intensity` 参数（建议 ≥0.3）；validate_storyboard.py 加校验：相邻 shot 的 visual 描述相似度阈值（<0.7 才放行） | `r2v_video_gen.py`、`validate_storyboard.py` | **M**（3 天） |

> **P1 合计**：~3-4 周工作量。完成后镜头语言从 48 提到 60+，制作完成度从 50 提到 75+，综合分从 65 提到 70+。

---

### 🟢 P2 — 锦上添花（资源允许再做）

| # | 缺口 | 改进点 | 涉及模块/文件 | 工作量 |
|---|---|---|---|---|
| **P2-1** | **风格饱和度偏弱**（参考饱和三色 vs 我们环境配色） | storyboard 加 `color_palette` 字段（{primary, secondary, accent}），传给 image-prompt-engineer 作为约束 | storyboard schema、`stage image` 的 prompt | **S**（0.5 天） |
| **P2-2** | **缺 Logo / 角标 / 水印** | storyboard 加 `brand` 字段（{logo_path, watermark_text, watermark_position}）；render_overlays.py 新增 `logo-overlay` / `watermark` 模板 | storyboard schema、`render_overlays.py` | **S**（1 天） |
| **P2-3** | **缺乏 ID 卡 / 海报式排版**（参考 #07/#11/#24） | render_overlays.py 新增 `id-card` 模板（角色名 + 签名条 + 角色立绘）；storyboard 加 `id_card_shots: [shot_index, ...]` 字段 | `render_overlays.py`、storyboard schema | **M**（2 天） |
| **P2-4** | **配音单一**（仅 shot3 一句） | `p5_v6_audio.py:40` 当前硬编码 `NARRATION_TEXT = "雨还在下，他走得不快。"`，改成读 storyboard.shots[].narration 列表；多 shot 多配音 + 不同 voice | `p5_v6_audio.py` 及所有 v* 版本 | **S**（0.5 天） |
| **P2-5** | **缺乏反差/双角色叙事**（"学姐 vs 学妹"对比钩子） | storyboard 加 `comparison_hook` 字段；visual-storyteller prompt 注入"二选一对比"钩子模板 | storyboard schema、`stage script` 的 prompt | **M**（2-3 天） |
| **P2-6** | **低帧率反而是 AI 直出特征** | 可选：把 final 输出降到 12-15 fps 模拟"AI 直出"质感（参考 8fps），在抖音信息流里反而是垂直标识 | `compose_final.py` 加 `--fps` 参数 | **S**（0.5 天） |

> **P2 合计**：~1-2 周工作量。完成后整体综合分可能从 70 提到 75+，逼近参考腰部爆款水平。

---

## 6. 优先级总结

```
                          P0（必补，1 周）        P1（强烈建议，3-4 周）        P2（锦上添花，1-2 周）
                          ─────────────────      ─────────────────────────      ──────────────────────
覆盖维度                  包装 + 时长 + 比例      镜头 + 角色 + 运镜            风格 + Logo + 钩子
预期综合分                59 → 65+                65 → 70+                       70 → 75+
触及模块                  compose_final /         storyboard / r2v_video_gen /   storyboard schema /
                          storyboard schema       validate_storyboard /          render_overlays /
                                                  pipeline.yaml                  p5_v6_audio
```

**最小可行追赶路径**（如果只能选 P0）：
1. **加 9:16 支持 + 改 demo 文案**（P0-1 + P0-2） → 立刻把"画面比例错位"和"demo 标签暴露"两个**致命扣分项**修掉；
2. **storyboard 默认 10-14 shot**（P0-3） → 镜头数翻 3 倍，镜头语言分从 48 拉到 60+；
3. **缩 title/end 时长到 2s**（P0-4） → 有效画面密度从 47% 提到 70%+。

这 4 项改完，单条视频就有资格从"工程 demo"升级到"可发布的 AI 短视频"，距离参考爆款差距从 -9 分缩到 -3 分左右。

---

## 7. 附录：本次评估的产物清单

| 文件 | 用途 | 大小 |
|---|---|---|
| `ref_analysis/frames/ref_001.jpg` ... `ref_024.jpg` | 参考视频 24 个抽帧（3 秒间隔） | ~50 KB × 24 |
| `ref_analysis/filmstrip_ref_6x4.jpg` | 参考视频 6×4 网格 filmstrip（VLM 输入） | 379 KB |
| `ref_analysis/vlm_report_raw.md` | 参考视频 VLM 原始输出 + prompt + 响应 | 8066 字 |
| `ref_analysis/frames_our/our_01.jpg` ... `our_16.jpg` | 我们 final_v6.mp4 16 个抽帧（1 秒间隔） | ~50 KB × 16 |
| `ref_analysis/filmstrip_our_4x4.jpg` | 我们 final_v6 4×4 网格 filmstrip（VLM 输入） | 200 KB |
| `ref_analysis/vlm_report_our_raw.md` | 我们视频 VLM 原始输出 + prompt + 响应 | 8689 字 |
| `ref_analysis/gap_report.md` | **本文件**：差距评估 + 追赶清单 | — |
| `make_filmstrip_ref.py` / `make_filmstrip_our.py` | 合成 filmstrip 的脚本（保留可复跑） | — |
| `analyze_ref_vlm.py` / `analyze_our_vlm.py` | 调 VLM 的脚本（保留可复跑） | — |

> **复跑命令**：
> - 重新合成 filmstrip：`python make_filmstrip_ref.py` / `python make_filmstrip_our.py`
> - 重新调 VLM：`python analyze_ref_vlm.py` / `python analyze_our_vlm.py`
>
> **注意事项**：
> - VLM 端点是 Cloudflare 网关，**必须带 User-Agent**（脚本已加上 Chrome UA），否则 403 错误码 1010。
> - VLM `max_tokens=3000` 限制下"档次定位"小节被截断，分析已足够；如需完整段落可拆成两次调用或调高 max_tokens。
> - 所有抽取帧、VLM 输入图、报告均保留在 `D:\ai-video-pipeline\ref_analysis\`，**未修改任何 pipeline 现有代码**。