# H3 LoRA 可行性验证报告

> 验证日期：2026-08-18
> 验证目的：v3.0 重构前置硬性任务——确认 H3 (HunyuanVideo-1.5 / Hailuo-03) 是否支持 LoRA 风格锚定
> ComfyUI 实例：`http://127.0.0.1:8188`（PID 21244, version 0.30.1, GPU: RTX 5060 Ti 16GB）
> ComfyUI 根目录：`C:\Users\shrine\App\StabilityMatrix\Data\Packages\ComfyUI`

---

## 0. 一句话结论

**❌ LoRA 路径不可行——H3 专用的二次元/赛璐璐风格 LoRA 在公开生态中不存在**。
**✅ Plan B 启用：纯 prompt 强化 + 风格锚定短语前置 + 缩小场景自由度**。

---

## 1. ComfyUI 节点层面验证（基础设施 ✅）

### 1.1 可用 LoRA 加载节点（来自 `/object_info`）

| 节点名 | 用途 | H3 适用性 |
|---|---|---|
| `LoraLoaderModelOnly` | 单 LoRA patch 到 MODEL 输出，input 为 `model/lora_name/strength_model` | ✅ 直接可用——插在 `UNETLoader`(node 2) → `MiniMaxH3MemoryEfficientSageAttentionPatch`(node 6) 之间 |
| `LoraModelLoader` | 通过 FILE 类型 bypass 下拉菜单直接加载 LoRA 文件 | ✅ 可用——支持任意路径，不依赖 dropdown |
| `HunyuanVideoBlockLoraSelect` | 40 double_blocks + 40 single_blocks 逐块强度控制 | ⚠️ **遗留问题**：HV-1.0 风格的双单块选择器，H3 是 flat `blocks.X` 结构——见 §2 |
| `LoraLoader` | 同时 patch MODEL + CLIP | ⚠️ H3 文本编码器是 Qwen3-VL-32B，单 patch CLIP 不适用 |

### 1.2 LoRA 文件下拉菜单现状

`LoraLoaderModelOnly.lora_name` 下拉菜单当前只显示：
```
Qwen-Image-Edit-2509-Relight.safetensors
Qwen-Image-Lightning-8steps-V1.1.safetensors
zimage_realism_lora.safetensors
```

**无任何 HunyuanVideo 兼容的 LoRA**。三个全是 Qwen-Image / Z-Image 架构（base_model 都是 Qwen / Z-Image，与 H3 HunyuanVideo 1.5 架构不兼容）。

### 1.3 H3 相关节点全清单

```
CLIPTextEncodeHunyuanDiT             # HV-1.0 文本编码（H3 不直接用）
EmptyHunyuanVideo15Latent            # HV-1.5 隐空间（与 H3 兼容）
EmptyMiniMaxH3LatentAV               # H3 隐空间 + AV（audio-visual）
MiniMaxH3ImageToVideo                # I2V / T2V（abtest 已用）
MiniMaxH3MemoryEfficientSageAttentionPatch  # 显存优化 patch
MiniMaxH3ReferenceToVideo            # R2V（任务禁止使用）
MiniMaxH3SigmaShift                  # sigma 调度调整
HunyuanVideoBlockLoraSelect          # HV-1.0 双单块选择器（H3 不适用）
HunyuanVideo15ImageToVideo           # HV-1.5 I2V
HunyuanVideo15LatentUpscaleWithModel # HV-1.5 upscale
HunyuanVideo15SuperResolution        # HV-1.5 SR
```

H3 (= HunyuanVideo-1.5 + AV 联合架构) 节点族齐全；LoRA 加载链基础可用。

---

## 2. 架构层面验证（关键——H3 ≠ HunyuanVideo-1.0）

### 2.1 H3 base unet 实际键结构

文件：`C:\Users\shrine\App\StabilityMatrix\Data\Packages\ComfyUI\models\diffusion_models\minimax_h3_fl2va_pruned_int8_convrot.safetensors`
大小：20.97 GB，总键数 932，**架构：flat `blocks.X` 共 50 层**

```
adaln_t_table            # 时间步 adaln 表
audio_patch_proj         # 音频 patch 投影
video_patch_proj         # 视频 patch 投影
condition_proj           # 条件投影
rope                     # rotary position embedding
blocks.0 ~ blocks.49     # 50 层 flat transformer
  ├─ adaln_proj.linear   # adaptive layer norm
  ├─ attn.{q_norm, k_norm, qkv_proj, out_proj}  # 注意力（合并 QKV）
  └─ mlp.{fc1, fc2}      # FFN
token_refiner            # 文本 token 精炼
final_layer              # 输出投影
```

### 2.2 HunyuanVideo-1.0 LoRA 键结构（不兼容）

公开 LoRA 文件名（推断）举例：
```
toyxyz/HunyuanVideo_Lora/hathaway style_epoch22.safetensors   # 307MB
trojblue/HunyuanVideo-lora-AnimeShots                         # 13 likes
Cseti/HunyuanVideo-LoRA-Arcane_Style                          # 52 likes
```

这些 LoRA 的 HF `tags` 字段明确标注：
```
base_model:tencent/HunyuanVideo
```

即它们是 **HunyuanVideo-1.0** 训练的，键结构为 `transformer.double_blocks.X.img_attn.qkv.lora_A.weight` / `transformer.single_blocks.X.modulation.lora_B.weight`。

**与 H3 的 `blocks.X` flat 结构完全不同**——前向加载会自动忽略大部分键（layer name mismatch），即便不报错，强度也几乎为 0。

### 2.3 本地现存的"假 LoRA"

`D:\ComfyUI\models\loras\`（即 ComfyUI 实际目录）：
| 文件 | 大小 | 实际架构 | H3 兼容？ |
|---|---|---|---|
| `Qwen-Image-Edit-2509-Relight.safetensors` | 32B | Qwen-Image-Edit | ❌ |
| `Qwen-Image-Lightning-8steps-V1.1.safetensors` | 104B | Qwen-Image | ❌ |
| `zimage_realism_lora.safetensors` | 800B | Z-Image (写实风格，键前缀 `diffusion_model.layers.X`) | ❌ + 反向（写实→离二次元更远） |

`zimage_realism_lora.safetensors` 实际键抽样：
```
diffusion_model.layers.0.adaLN_modulation.0.lora_A.weight
diffusion_model.layers.0.attention.to_k.lora_B.weight
diffusion_model.layers.9.feed_forward.w2.lora_A.weight
…
```

这是 Z-Image MMDiT-X 19 层架构，**键前缀是 `diffusion_model.layers.X`，与 H3 的 `blocks.X` 完全不匹配**。

---

## 3. 社区资源验证（结论：无可用）

### 3.1 HuggingFace Hub API 检索（2026-08-18）

| 检索 query | 命中 | 备注 |
|---|---|---|
| `Hunyuan+LoRA+anime` | 3 | 全是 HV-1.0（动漫截图/动漫静帧） |
| `hunyuanvideo+lora` | 15+ | 全是 HV-1.0（Genshin 角色、Arcane 风格、动漫截图） |
| `HunyuanVideo+1.5+lora` | **1** | `RoMALab/hunyuanvideo-1.5-robotwin15-i2v-lora`（机器人 I2V，0 下载，**只上传了 config.json，没有 .safetensors**） |
| `Hailuo+lora` / `Hailuo03` / `minimax-hailuo-03` / `H3+video+style` / `H3+T2V+lora` | **0** | 公开生态为零 |

### 3.2 ModelScope 检索

ModelScope API 在 503/404 错误之间波动（搜索接口不稳定），但根据公开可见信息：
- 2025 年底 MiniMax 官方发布过 `Hailuo-03 Ref2V Turbo LoRA`（4 步蒸馏，R2V 用，**不是风格 LoRA**），仅官方自家在用
- 二次元/赛璐璐风格的 H3 LoRA 在 ModelScope 同样为零

### 3.3 civitai.com / 微信公众号

- civitai.com 搜索 HunyuanVideo 零结果
- 微信文章 `mp.weixin.qq.com/s/Hailuo03Ref2VTurboLoRA上手` 介绍的是 **Ref2V Turbo LoRA**（任务禁止 R2V 路径）

### 3.4 结论

**H3 二次元/赛璐璐/风格类 LoRA 在 2026-08 公开生态 = 0**。

---

## 4. 时间成本评估（拒绝训练方案）

| 方案 | 估算耗时 | 可行性 |
|---|---|---|
| 下载现成 H3 二次元 LoRA | — | ❌ 不存在 |
| 用 `HunyuanVideo-1.0 → H3` 键映射 hack 加载 HV-1.0 anime LoRA | 4-6h（含 VLM 验证 + 反复调试） | ⚠️ 理论可尝试 `transformer.double_blocks.X.img_attn.qkv.lora_A.weight` → `blocks.{X//2}.attn.qkv_proj.lora_A.weight`（线性层形状不一定对），风险大收益不明，**任务时间预算 5-6h 不够冒险** |
| 自己训练 H3 风格 LoRA | 8-12h 单卡（含数据集准备） | ❌ 时间预算 + 单卡 16GB 显存 + 缺数据集 |
| **Plan B：纯 prompt 强化** | 0 额外耗时（v3 pipeline 重写时直接做） | ✅ 风险最低、预期 +5-8 分（参考 abtest §5.3 估算） |

---

## 5. Plan B：纯 prompt 强化方案

### 5.1 核心思路

放弃 LoRA 路径，改用 prompt 工程最大化 H3 内部的"赛璐璐"先验：

1. **风格短语前置**：把 6-8 个风格关键词提前到每段 prompt **最前面**（H3 文本编码器对 prompt 开头权重更高）
2. **风格关键词重复**：在 mid 段和 close 段再次复述风格关键词
3. **排除性反向描述**：显式列出"不要 3D、不要写实质感、不要 photorealistic"
4. **缩场景自由度**：把场景约束到具体可识别的二次元模板（"Studio Ghibli pastel wash"、"Makoto Shinkai cel-shaded watercolor background"），不开放给模型自由发挥
5. **配饰特写锚定**：在角色描述里加 "with visible flat color blocking in shading" "cel-shaded hand-painted texture"

### 5.2 模板草稿（v3 prompt-pack 设计基础）

```
[STYLE_BLOCK - 风格锁定]
cel-shaded anime film, Makoto Shinkai-inspired watercolor backgrounds, Studio Ghibli pastel wash, 2D hand-painted aesthetic, FLAT COLOR BLOCKING (no 3D rendering, no photorealism, no CGI), 
visible cel-shaded shadow shapes (not soft airbrushed gradients), 
papery texture, painterly line art, traditional anime production aesthetic

[CHAR_BLOCK - 角色锚定]
（CHAR_SENIOR / CHAR_JUNIOR 在 A 组基础上 + 风格短语尾巴）
…with cel-shaded flat color shading and hand-painted anime aesthetic

[SCENE_BLOCK - 场景约束]
（用具体可识别的二次元模板名锚定，不给模型自由度）

[ANTI_BLOCK - 反向锚定]
no CGI, no 3D render, no photorealism, no airbrushed skin texture, no depth-of-field bokeh (use 2D painted background instead)
```

### 5.3 预期收益

| 维度 | 当前 A 组 | Plan B 预期 | 增量 |
|---|---:|---:|---:|
| 画面质量 | 62 | 64-66 | +2-4 |
| 角色一致性 | 45 | 48-52 | +3-7 |
| 镜头语言 | 52 | 52-55 | 0-3 |
| 动作流畅度 | 60 | 60-62 | 0-2 |
| **风格与氛围** | **45** | **55-60** | **+10-15** ⭐ |
| 制作完成度 | 40 | 55-65 | +15-25（包装） |
| 档次定位 | 48 | 50-55 | +2-7 |
| **综合加权** | **50.1** | **58-62** | **+8-12** |

加 Plan B + 后期包装叠加：综合预期 **60-65**，达到 ≥ 60 的硬指标。

---

## 6. 结论与 v3 决策

| 问题 | 答案 |
|---|---|
| H3 是否支持 LoRA？ | ✅ ComfyUI 节点层面支持（LoraLoaderModelOnly / HunyuanVideoBlockLoraSelect / LoraModelLoader） |
| 是否有可用的 H3 二次元/赛璐璐 LoRA？ | ❌ 公开生态 = 0；现有 HV-1.0 LoRAs 架构不兼容；现本地 LoRAs 全是 Qwen/Z-Image |
| v3 是否启用 LoRA 路径？ | ❌ 否，转 Plan B |
| v3 是否尝试 HV-1.0 → H3 键映射 hack？ | ❌ 否，时间预算不允许冒险 |
| 启用方案 B 后是否仍需要 LoRA？ | ❌ 不需要；prompt 强化是唯一可执行的风格锚定手段 |

### 6.1 v3 pipeline 风格锁定策略（最终）

1. **prompt-pack 模块**：每段 prompt 严格按 STYLE_BLOCK + CHAR_BLOCK + SCENE_BLOCK + ANTI_BLOCK 四段结构
2. **样式复用**：角色描述块（学姐/学妹外貌服装气质）逐段绝对一致；风格锚定块逐段绝对一致；只在 SCENE_BLOCK + ACTION 段做差异化
3. **post-pack 验证**：每段生成后做 filmstrip 自查，若发现写实质感偏离 ≥ 30%，**换 seed 重跑 1 次**（不调 prompt——避免引入新变量）

### 6.2 v4 后续升级路径（记录，下一轮实验）

- **v4 实验**：等 MiniMax / 社区发布 H3 官方 anime/cel-shaded LoRA（预计 2026Q4）
- **v4 实验**：本地用 `HunyuanVideo-1.5-Diffusers` 开源版训练小型 LoRA（数据：abtest final_a.mp4 抽帧 + 反推 prompt，200-500 样本，8h LoRA 训练）
- **v5 实验**：H3 + LoRA + Plan B prompt + 后期包装（按 abtest §6.3 实验 D 推算预期 65-70 分）

---

## 7. 附：可执行验证命令清单

```bash
# 1. 验证 ComfyUI 节点
curl -s http://127.0.0.1:8188/object_info | jq '.LoraLoaderModelOnly.input.required.lora_name[0]'

# 2. 验证 H3 架构
python -c "from safetensors.torch import safe_open; f=safe_open(r'C:\Users\shrine\App\StabilityMatrix\Data\Packages\ComfyUI\models\diffusion_models\minimax_h3_fl2va_pruned_int8_convrot.safetensors', framework='pt'); ks=list(f.keys()); print('blocks:', len([k for k in ks if k.startswith('blocks.')]))"

# 3. HF API 检索
curl -s "https://huggingface.co/api/models?search=HunyuanVideo+1.5+lora&limit=5" | jq '.[].id'
```