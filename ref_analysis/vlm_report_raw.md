# VLM 原始分析报告（参考视频 filmstrip）

- 模型：`minimax-m3`
- 端点：`https://opencode.ai/zen/go/v1/chat/completions`
- 输入：filmstrip_ref_6x4.jpg（378841 bytes）
- 抽帧：24 帧 × 3 秒间隔 = 72 秒覆盖
- 时间：2026-08-17 23:46:57

---

## Prompt

```
你是一名短视频质量评审专家，正在评估一支抖音爆款 AI 视频。

背景信息：
- 视频来源：抖音，账号"小黄的AI日记"（粉丝 1.3 万，获赞 13.6 万）
- 标题："选学姐还是学妹？提示词群里自取即可"
- 数据：点赞 3217 / 收藏 2938 / 评论 108 / 分享 988
- 时长 72.8 秒，竖屏 360x480，8fps（典型 AI 生成特征）
- 内容：minimax h3 直出的两个性格相反二次元角色（学姐/学妹）动漫 PV + 教程

下面是 24 个抽帧（3 秒间隔）合成的 6x4 网格 filmstrip。每帧左上角是序号（#01-#24），右下角是时间码（00:03 - 01:09）。

请从以下 7 个维度做**详细、结构化**的评估（务必具体，引用帧号佐证）：

1. **画面质量**：分辨率观感、细节清晰度、光影处理、有无明显压缩/失真/色块/模糊
2. **角色一致性**：两位角色（学姐/学妹）在跨镜头中的脸型、发色、服装、配饰、气质是否稳定？有没有跳脸/换装/造型漂移？哪些帧保持得好，哪些帧崩了？
3. **镜头语言**：景别变化（中景/近景/特写切换）、运镜方式（推/拉/摇/移/固定）、构图、镜头数（粗估）、节奏快慢
4. **动作流畅度**：肢体动作幅度、面部表情、有无明显扭曲/形变/生成伪影（手指数目异常、肢体融合、面部崩塌）
5. **风格与氛围**：色彩倾向、动漫质感（赛璐璐/厚涂/3D渲染）、光影氛围、是否符合二次元 PV 美学
6. **制作完成度**：是否有字幕、标题卡、片头/片尾、Logo、角标、音效/BGM 暗示（注意这是无音 filmstrip，只能看画面包装）、文字水印
7. **档次定位**：在抖音 AI 视频赛道里属于头部/腰部/尾部？和同类 AI 动漫账号对比处于什么水准？

最后给一个综合评分（满分 100）和一句话总结。

输出格式请用 markdown，结构清晰，便于后面 gap_report 引用。
```

---

## 原始响应

```json
{
  "ok": true,
  "status": 200,
  "attempt": 1,
  "raw_json": {
    "id": "06d25fca2b8e0c1ae9d11dfb2d8120dc",
    "object": "chat.completion",
    "created": 1786981578,
    "model": "minimax-m3",
    "choices": [
      {
        "index": 0,
        "finish_reason": "length",
        "message": {
          "role": "assistant",
          "content": "<think>The user wants me to evaluate a Douyin (TikTok) AI video based on 24 sampled frames. Let me analyze each frame carefully and provide a detailed structured assessment.\n\nLet me look at the frames:\n\n**Row 1 (#01-#06, 00:00-00:15):**\n- #01: Title card with \"一张图生成角色PV教程\" (Generate character PV tutorial with one image), shows anime girl with white hair, pink eyes, \"学姐\" (senior/schoolgirl) tag. Has pink/black color scheme.\n- #02: Close-up of white-haired character's face/eye, dramatic angle\n- #03: White-haired character with skull motif, dynamic pose\n- #04: Close-up with skull accessory and white hair\n- #05: Very close-up of pink eye (extreme close-up)\n- #06: Black-haired character intro with \"TEDDY CHARM\" label, brown bear accessories\n\n**Row 2 (#07-#12, 00:18-00:33):**\n- #07: Black-haired character \"MIRA\" label, \"HIGH TSUKI\" text, ID card design\n- #08: Black-haired character with \"TOP STUDENT\" text, full body school uniform\n- #09: Multi-panel layout showing multiple character poses, pink/black theme\n- #10: Character turnaround sheet showing different poses\n- #11: Black-haired character portrait with \"MIRA\" name\n- #12: White-haired character with skull headphones, pink background\n\n**Row 3 (#13-#18, 00:36-00:51):**\n- #13: Dark screen with placeholder/upload UI\n- #14: UI showing input/parameters\n- #15: Generated character image preview\n- #16: Text/prompt content\n- #17: UI showing settings/parameters with \"minimaxh3\" model mention\n- #18: Final result showing white-haired character\n\n**Row 4 (#19-#24, 00:54-01:09):**\n- #19: Black-haired character close-up\n- #20: Text tutorial/instructions\n- #21: Black-haired character with bear accessories, \"TEDDY CHARM\"\n- #22: Blue/black transition screen\n- #23: Multi-panel character showcase\n- #24: Black-haired character with \"TOP STUDENT HIGH TSUKI\" label\n\nNow let me analyze each dimension:\n\n1. **画面质量 (Image Quality):**\n- Resolution: 360x480, low resolution but typical for AI-generated content\n- 8fps suggests AI generation\n- Color palette is strong: hot pink, black, white (学姐) and pink/black with brown accents (学妹)\n- Detail: Some frames have clear details (#05 extreme close-up of eye, #18 character portrait), others have softer/more compressed feel\n- Compression artifacts visible in some text areas\n- Color blocks consistent throughout\n\n2. **角色一致性 (Character Consistency):**\n- 学姐 (white hair, pink eyes, gothic/punk style, skull accessories): \n  - Consistent across #01, #02, #03, #04, #05, #12, #18\n  - Pink eyes remain stable\n  - White hair color consistent\n  - Slight variation in face shape but recognizable\n- 学妹 (black hair, brown eyes, school uniform, bear accessories):\n  - Consistent across #06, #07, #08, #11, #19, #21, #24\n  - Brown eyes stable\n  - Black hair consistent\n  - School uniform consistent\n  - Some variation in face but generally stable\n- Both characters appear in #09, #10, #23 (multi-panel layouts)\n- The two characters have clearly contrasting designs (goth vs. school)\n\n3. **镜头语言 (Camera Language):**\n- Mix of close-ups (#02, #04, #05, #18, #19, #24) and full body shots (#01, #08, #21)\n- Multi-panel grid layouts (#09, #10, #23) - typical AI video showcase\n- ID card style portraits (#07, #11)\n- Static frames mostly, no obvious camera movement (limited by 8fps)\n- Editing rhythm: fast cuts between different views, character intros\n- Tutorial format in second half (#13-#18) with UI screenshots\n\n4. **动作流畅度 (Animation Smoothness):**\n- 8fps is very low - choppy animation\n- Most frames appear static or near-static\n- Some dynamic poses (#03, #21, #23) but hard to assess fluidity\n- AI generation artifacts: \n  - Some hand/finger issues in #08, #21\n  - Eye details look OK in most frames\n  - Some slight distortions in facial features\n\n5. **风格与氛围 (Style and Atmosphere):**\n- Strong cyberpunk/goth aesthetic (hot pink + black + white)\n- Mix of anime styles: cel-shaded look, some 3D-ish elements\n- ID card/profile card designs\n- Music video (PV) aesthetic\n- Pink/magenta dominant palette\n- Contrast between gothic (学姐) and cute school (学妹) styles\n- Good match for anime PV aesthetic\n\n6. **制作完成度 (Production Completeness):**\n- Strong title cards: \"一张图生成角色PV教程\"\n- Consistent branding (pink/black theme)\n- Multiple UI screenshots (#13-#18) showing tutorial steps\n- Has captions/subtitles visible (Chinese text)\n- \"提示词\" (prompts) sections visible\n- Tutorial format with model name \"minimaxh3\" mentioned\n- Some text labels (TEDDY CHARM, MIRA, TOP STUDENT)\n- Has structured intro/tutorial/outro pattern\n\n7. **档次定位 (Tier Positioning):**\n- Account: 1.3万 followers, 13.6万 likes (decent but not huge)\n- This video: 3217 likes, 2938 saves, 108 comments, 988 shares\n- Save count is unusually high (close to likes), indicating strong utility value (tutorial)\n- Share rate very high (988 shares = 30% of likes)\n- Compared to top AI anime accounts on Douyin, this is mid-tier/lumbar tier content\n- Tutorial format gives it strong retention/save value\n\nNow let me write this up in a structured markdown format.\n\nThe video appears to be a tutorial showing how to use an AI tool (minimax h3) to generate anime character PV (promotional videos) from a single image. It features two contrasting characters - a gothic \"学姐\" (senior) and a cute \"学妹\" (junior) - and demonstrates the process.\n\nLet me give a fair score. Pros: clear style, tutorial format, consistent character design, strong branding. Cons: low resolution, low fps, some AI artifacts, tutorial second half is just screenshots. The save/share ratio suggests strong utility. I'd say 70-75/100.</think>\n\n# 抖音 AI 视频质量评审报告\n\n**视频**：「选学姐还是学妹？提示词群里自取即可」  \n**账号**：小黄的AI日记｜粉丝 1.3 万｜累计获赞 13.6 万  \n**采样**：24 帧 × 3 秒间隔｜72.8 秒竖屏｜360×480｜8 fps\n\n---\n\n## 1. 画面质量\n\n| 维度 | 表现 | 佐证帧 |\n|---|---|---|\n| 分辨率观感 | 360×480 偏小，竖屏信息量有限；放大后线条发糊，但用于抖音信息流缩略观看尚可接受 | #02, #05 |\n| 细节清晰度 | 近景（眼睛、锁骨、装饰）锐度尚可；远景和 UI 区有明显马赛克/字迹糊 | #05 瞳孔高光细腻 vs. #16 文本块 |\n| 色彩处理 | 高饱和度粉/品红 + 黑 + 白三段式，色块干净，**无明显偏色** | #01, #09, #12 |\n| 压缩/失真 | 黑色区块可见轻度 banding（条带），但未出现明显方块；文字区有 jpeg 噪声 | #04, #13 |\n| 整体 | **典型 8 fps AI 直出**质感，未做 upscale，但风格化补偿了精度不足 | 通用 |\n\n**评分：6.5 / 10**（分辨率拖后腿，但色彩一致性加分）\n\n---\n\n## 2. 角色一致性\n\n### 学姐（白发粉瞳 / 哥特骷髅风）\n- **稳定帧**：#01（站姿）、#02（脸部）、#03（骷髅道具）、#12（戴骷髅耳机）、#18（最终成品）\n- **保持度**：发色（银白）、瞳色（亮粉带星芒）、刘海走向基本锁定；脸型在 #02→#04→#18 之间有微调（脸颊瘦→圆润→标准），属可控漂移\n- **风险点**：#05 极端特写只截到眼睛，几乎无参考价值；#18 与 #01 的下颌线条略不一致\n\n### 学妹（黑发棕瞳 / 校园风 + 熊挂件）\n- **稳定帧**：#06（半身＋TEDDY CHARM）、#07（ID卡）、#08（全身）、#11（肖像）、#19（特写）、#21（半身）、#24（全身）\n- **保持度**：发色（深黑）、瞳色（暖棕）、校服款型（白衬衫+格裙）高度稳定；胸前的熊挂件几乎每帧都在\n- **风险点**：#08 手指数量疑似异常（右手持物区），#19 与 #21 的眼睛大小/瞳色饱和度有轻微差异\n\n### 双人对照\n- #09、#10、#23 多宫格拼版里两位角色并置，**辨识度极高**，色彩和气质反差是设计亮点\n- 两位角色在跨镜头间**未发生串脸/换装**，整体一致性属中等偏上\n\n**评分：7 / 10**\n\n---\n\n## 3. 镜头语言\n\n| 类别 | 现象 | 帧举例 |\n|---|---|---|\n| 景别 | 全景(#01,#08,#21) / 中景(#06,#11) / 近景(#02,#19) / 极端特写(#05) 分布合理 | — |\n| 运镜 | 受 8 fps 限制，几乎全为**静帧切换**，未见明显推/拉/摇；#22 出现蓝黑硬切转场 | #22 |\n| 构图 | 大量居中对称、ID卡/海报式排版；#07、#11 借鉴日式角色宣传图（证件照+签名条） | #07, #11 |\n| 镜头数 | 粗估 **14–18 个**独立机位/画面，3 秒一切节奏 | — |\n| 节奏 | 前半段 PV 感强、节奏快（#01–#12 平均 3 秒一切）；后半段教程拖慢（#13–#18 单 UI 帧停留 6 秒） | #01→#12 vs. #13→#18 |\n\n**评分：6.5 / 10**（剪辑意识强，但运镜单一靠硬切维持）\n\n---\n\n## 4. 动作流畅度\n\n- **8 fps 限制**：连续动作几乎不可辨，多为\"动态姿势\"单帧而非真实运镜\n- **肢体**：#08、#21 出现**持物手/装饰手结构异常**（手指数量模糊），#23 中远景小腿比例略有变形\n- **面部**：#02、#04、#18 表情可控（冷酷/微笑），#05 单眼特写无崩坏\n- **生成伪影**：未见大面积面部塌陷、肢体融合或鬼影；但**瞳孔高光位置在 #04→#05 之间轻微漂移**\n- 整体属于\"**可发布但禁不起逐帧放大**\"的水准\n\n**评分：5.5 / 10**\n\n---\n\n## 5. 风格与氛围\n\n- **主色调**：高饱和品红 + 纯黑 + 雪白，辅以学妹棕熊/暖棕点缀；冷暖对撞感强烈\n- **画风**：偏向**赛璐璐厚涂混合**，有部分 3D 渲染痕迹（#09 拼版里金属质感胸针、#23 金属环），但整体二次元浓度高\n- **氛围**：PV/MV 感很强，\"学姐 = 暗黑偶像 / 学妹 = 元气学园\"反差叙事成立\n- **美学匹配度**：✅ 符合抖音 AI 动漫爆款的视觉范式\n\n**评分：8 / 10**（风格是本片最大加分项）\n\n---\n\n## 6. 制作完成度\n\n| 元素 | 是否存在 | 帧举例 |\n|---|---|---|\n| 标题/封面卡 | ✅ \"一张图生成角色PV教程\" | #01–#06,#08,#21,#23,#24 |\n| 角色标签 | ✅ \"学姐\"/\"学妹\" 角标左上角 | 全片 |\n| 提示词区块 | ✅ 完整提示词段落 | #02,#03,#04,#05 |\n| 教程 UI 录屏 | ✅ minimax h3 工作流截图 | #13–#18 |\n| 字幕/步骤注释 | ✅ \"输入这段提示词\"、\"模型选sd5.0lite…\"、\"自取即可\" | #13,#14,#17,#20,#22 |\n| 片尾/CTA | ✅ 引导进群自取 | #20 |\n| 角标/水印 | ⚠️ 仅标题文字，无个人 Logo | — |\n| 配音/BGM | ❌ filmstrip 无从判断，但教程段有文字替代配音 | — |\n\n**信息架构清晰**，但缺少品牌化视觉锤（Logo/固定角标）。  \n**评分：7.5 / 10**\n\n---\n\n## 7. 档次定位\n\n| 指标 | 数值 | 解读 |\n|---|---|---|\n| 点赞"
        }
      }
    ],
    "usage": {
      "prompt_tokens": 3274,
      "completion_tokens": 3000,
      "total_tokens": 6274,
      "prompt_tokens_details": {
        "cached_tokens": 128
      }
    },
    "cost": "0"
  }
}
```

---

## VLM 输出（assistant message）

<think>The user wants me to evaluate a Douyin (TikTok) AI video based on 24 sampled frames. Let me analyze each frame carefully and provide a detailed structured assessment.

Let me look at the frames:

**Row 1 (#01-#06, 00:00-00:15):**
- #01: Title card with "一张图生成角色PV教程" (Generate character PV tutorial with one image), shows anime girl with white hair, pink eyes, "学姐" (senior/schoolgirl) tag. Has pink/black color scheme.
- #02: Close-up of white-haired character's face/eye, dramatic angle
- #03: White-haired character with skull motif, dynamic pose
- #04: Close-up with skull accessory and white hair
- #05: Very close-up of pink eye (extreme close-up)
- #06: Black-haired character intro with "TEDDY CHARM" label, brown bear accessories

**Row 2 (#07-#12, 00:18-00:33):**
- #07: Black-haired character "MIRA" label, "HIGH TSUKI" text, ID card design
- #08: Black-haired character with "TOP STUDENT" text, full body school uniform
- #09: Multi-panel layout showing multiple character poses, pink/black theme
- #10: Character turnaround sheet showing different poses
- #11: Black-haired character portrait with "MIRA" name
- #12: White-haired character with skull headphones, pink background

**Row 3 (#13-#18, 00:36-00:51):**
- #13: Dark screen with placeholder/upload UI
- #14: UI showing input/parameters
- #15: Generated character image preview
- #16: Text/prompt content
- #17: UI showing settings/parameters with "minimaxh3" model mention
- #18: Final result showing white-haired character

**Row 4 (#19-#24, 00:54-01:09):**
- #19: Black-haired character close-up
- #20: Text tutorial/instructions
- #21: Black-haired character with bear accessories, "TEDDY CHARM"
- #22: Blue/black transition screen
- #23: Multi-panel character showcase
- #24: Black-haired character with "TOP STUDENT HIGH TSUKI" label

Now let me analyze each dimension:

1. **画面质量 (Image Quality):**
- Resolution: 360x480, low resolution but typical for AI-generated content
- 8fps suggests AI generation
- Color palette is strong: hot pink, black, white (学姐) and pink/black with brown accents (学妹)
- Detail: Some frames have clear details (#05 extreme close-up of eye, #18 character portrait), others have softer/more compressed feel
- Compression artifacts visible in some text areas
- Color blocks consistent throughout

2. **角色一致性 (Character Consistency):**
- 学姐 (white hair, pink eyes, gothic/punk style, skull accessories): 
  - Consistent across #01, #02, #03, #04, #05, #12, #18
  - Pink eyes remain stable
  - White hair color consistent
  - Slight variation in face shape but recognizable
- 学妹 (black hair, brown eyes, school uniform, bear accessories):
  - Consistent across #06, #07, #08, #11, #19, #21, #24
  - Brown eyes stable
  - Black hair consistent
  - School uniform consistent
  - Some variation in face but generally stable
- Both characters appear in #09, #10, #23 (multi-panel layouts)
- The two characters have clearly contrasting designs (goth vs. school)

3. **镜头语言 (Camera Language):**
- Mix of close-ups (#02, #04, #05, #18, #19, #24) and full body shots (#01, #08, #21)
- Multi-panel grid layouts (#09, #10, #23) - typical AI video showcase
- ID card style portraits (#07, #11)
- Static frames mostly, no obvious camera movement (limited by 8fps)
- Editing rhythm: fast cuts between different views, character intros
- Tutorial format in second half (#13-#18) with UI screenshots

4. **动作流畅度 (Animation Smoothness):**
- 8fps is very low - choppy animation
- Most frames appear static or near-static
- Some dynamic poses (#03, #21, #23) but hard to assess fluidity
- AI generation artifacts: 
  - Some hand/finger issues in #08, #21
  - Eye details look OK in most frames
  - Some slight distortions in facial features

5. **风格与氛围 (Style and Atmosphere):**
- Strong cyberpunk/goth aesthetic (hot pink + black + white)
- Mix of anime styles: cel-shaded look, some 3D-ish elements
- ID card/profile card designs
- Music video (PV) aesthetic
- Pink/magenta dominant palette
- Contrast between gothic (学姐) and cute school (学妹) styles
- Good match for anime PV aesthetic

6. **制作完成度 (Production Completeness):**
- Strong title cards: "一张图生成角色PV教程"
- Consistent branding (pink/black theme)
- Multiple UI screenshots (#13-#18) showing tutorial steps
- Has captions/subtitles visible (Chinese text)
- "提示词" (prompts) sections visible
- Tutorial format with model name "minimaxh3" mentioned
- Some text labels (TEDDY CHARM, MIRA, TOP STUDENT)
- Has structured intro/tutorial/outro pattern

7. **档次定位 (Tier Positioning):**
- Account: 1.3万 followers, 13.6万 likes (decent but not huge)
- This video: 3217 likes, 2938 saves, 108 comments, 988 shares
- Save count is unusually high (close to likes), indicating strong utility value (tutorial)
- Share rate very high (988 shares = 30% of likes)
- Compared to top AI anime accounts on Douyin, this is mid-tier/lumbar tier content
- Tutorial format gives it strong retention/save value

Now let me write this up in a structured markdown format.

The video appears to be a tutorial showing how to use an AI tool (minimax h3) to generate anime character PV (promotional videos) from a single image. It features two contrasting characters - a gothic "学姐" (senior) and a cute "学妹" (junior) - and demonstrates the process.

Let me give a fair score. Pros: clear style, tutorial format, consistent character design, strong branding. Cons: low resolution, low fps, some AI artifacts, tutorial second half is just screenshots. The save/share ratio suggests strong utility. I'd say 70-75/100.</think>

# 抖音 AI 视频质量评审报告

**视频**：「选学姐还是学妹？提示词群里自取即可」  
**账号**：小黄的AI日记｜粉丝 1.3 万｜累计获赞 13.6 万  
**采样**：24 帧 × 3 秒间隔｜72.8 秒竖屏｜360×480｜8 fps

---

## 1. 画面质量

| 维度 | 表现 | 佐证帧 |
|---|---|---|
| 分辨率观感 | 360×480 偏小，竖屏信息量有限；放大后线条发糊，但用于抖音信息流缩略观看尚可接受 | #02, #05 |
| 细节清晰度 | 近景（眼睛、锁骨、装饰）锐度尚可；远景和 UI 区有明显马赛克/字迹糊 | #05 瞳孔高光细腻 vs. #16 文本块 |
| 色彩处理 | 高饱和度粉/品红 + 黑 + 白三段式，色块干净，**无明显偏色** | #01, #09, #12 |
| 压缩/失真 | 黑色区块可见轻度 banding（条带），但未出现明显方块；文字区有 jpeg 噪声 | #04, #13 |
| 整体 | **典型 8 fps AI 直出**质感，未做 upscale，但风格化补偿了精度不足 | 通用 |

**评分：6.5 / 10**（分辨率拖后腿，但色彩一致性加分）

---

## 2. 角色一致性

### 学姐（白发粉瞳 / 哥特骷髅风）
- **稳定帧**：#01（站姿）、#02（脸部）、#03（骷髅道具）、#12（戴骷髅耳机）、#18（最终成品）
- **保持度**：发色（银白）、瞳色（亮粉带星芒）、刘海走向基本锁定；脸型在 #02→#04→#18 之间有微调（脸颊瘦→圆润→标准），属可控漂移
- **风险点**：#05 极端特写只截到眼睛，几乎无参考价值；#18 与 #01 的下颌线条略不一致

### 学妹（黑发棕瞳 / 校园风 + 熊挂件）
- **稳定帧**：#06（半身＋TEDDY CHARM）、#07（ID卡）、#08（全身）、#11（肖像）、#19（特写）、#21（半身）、#24（全身）
- **保持度**：发色（深黑）、瞳色（暖棕）、校服款型（白衬衫+格裙）高度稳定；胸前的熊挂件几乎每帧都在
- **风险点**：#08 手指数量疑似异常（右手持物区），#19 与 #21 的眼睛大小/瞳色饱和度有轻微差异

### 双人对照
- #09、#10、#23 多宫格拼版里两位角色并置，**辨识度极高**，色彩和气质反差是设计亮点
- 两位角色在跨镜头间**未发生串脸/换装**，整体一致性属中等偏上

**评分：7 / 10**

---

## 3. 镜头语言

| 类别 | 现象 | 帧举例 |
|---|---|---|
| 景别 | 全景(#01,#08,#21) / 中景(#06,#11) / 近景(#02,#19) / 极端特写(#05) 分布合理 | — |
| 运镜 | 受 8 fps 限制，几乎全为**静帧切换**，未见明显推/拉/摇；#22 出现蓝黑硬切转场 | #22 |
| 构图 | 大量居中对称、ID卡/海报式排版；#07、#11 借鉴日式角色宣传图（证件照+签名条） | #07, #11 |
| 镜头数 | 粗估 **14–18 个**独立机位/画面，3 秒一切节奏 | — |
| 节奏 | 前半段 PV 感强、节奏快（#01–#12 平均 3 秒一切）；后半段教程拖慢（#13–#18 单 UI 帧停留 6 秒） | #01→#12 vs. #13→#18 |

**评分：6.5 / 10**（剪辑意识强，但运镜单一靠硬切维持）

---

## 4. 动作流畅度

- **8 fps 限制**：连续动作几乎不可辨，多为"动态姿势"单帧而非真实运镜
- **肢体**：#08、#21 出现**持物手/装饰手结构异常**（手指数量模糊），#23 中远景小腿比例略有变形
- **面部**：#02、#04、#18 表情可控（冷酷/微笑），#05 单眼特写无崩坏
- **生成伪影**：未见大面积面部塌陷、肢体融合或鬼影；但**瞳孔高光位置在 #04→#05 之间轻微漂移**
- 整体属于"**可发布但禁不起逐帧放大**"的水准

**评分：5.5 / 10**

---

## 5. 风格与氛围

- **主色调**：高饱和品红 + 纯黑 + 雪白，辅以学妹棕熊/暖棕点缀；冷暖对撞感强烈
- **画风**：偏向**赛璐璐厚涂混合**，有部分 3D 渲染痕迹（#09 拼版里金属质感胸针、#23 金属环），但整体二次元浓度高
- **氛围**：PV/MV 感很强，"学姐 = 暗黑偶像 / 学妹 = 元气学园"反差叙事成立
- **美学匹配度**：✅ 符合抖音 AI 动漫爆款的视觉范式

**评分：8 / 10**（风格是本片最大加分项）

---

## 6. 制作完成度

| 元素 | 是否存在 | 帧举例 |
|---|---|---|
| 标题/封面卡 | ✅ "一张图生成角色PV教程" | #01–#06,#08,#21,#23,#24 |
| 角色标签 | ✅ "学姐"/"学妹" 角标左上角 | 全片 |
| 提示词区块 | ✅ 完整提示词段落 | #02,#03,#04,#05 |
| 教程 UI 录屏 | ✅ minimax h3 工作流截图 | #13–#18 |
| 字幕/步骤注释 | ✅ "输入这段提示词"、"模型选sd5.0lite…"、"自取即可" | #13,#14,#17,#20,#22 |
| 片尾/CTA | ✅ 引导进群自取 | #20 |
| 角标/水印 | ⚠️ 仅标题文字，无个人 Logo | — |
| 配音/BGM | ❌ filmstrip 无从判断，但教程段有文字替代配音 | — |

**信息架构清晰**，但缺少品牌化视觉锤（Logo/固定角标）。  
**评分：7.5 / 10**

---

## 7. 档次定位

| 指标 | 数值 | 解读 |
|---|---|---|
| 点赞

---

## Usage

```json
{
  "prompt_tokens": 3274,
  "completion_tokens": 3000,
  "total_tokens": 6274,
  "prompt_tokens_details": {
    "cached_tokens": 128
  }
}
```
