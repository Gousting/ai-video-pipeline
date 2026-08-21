# 视觉风格档案 — 参考视频 input_h3_pv_ref.mp4 (v3.6.7)

> 自动生成: 2026-08-21 14:00:16    VLM: `minimax-m3`  Frames: 12

> 任务书: oc_task_v367.txt §2.1 §2.2 (视觉识别 + 落盘)

---

## 0. 摘要
- **类型**: Anime music-video / fashion-editorial motion graphics
- **一句话风格**: Hyper-saturated CMYK pop-art anime with prismatic rainbow details, acid-neon streetwear and graphic color-field transitions, shot as macro close-ups in a 2.36:1 widescreen format.
- **适合 H3 R2V**: True
- **生成风险**: Eye color drift between rainbow and green-yellow across shots may confuse identity; Heavy abstract color overlays can wash out face if prompt weights face too low; 2.36:1 aspect ratio may be cropped to 16:9 by H3; Piercings and nail details are small and may be lost at lower fidelity; Rapid scene/costume changes (green outfit vs dark top) risk character inconsistency

## 1. 角色设定
### 角色 1: Color Riot Girl
- 发型: Long straight black hair with blunt bangs, loose flowing strands, occasional teal/blue underlayer and rainbow streaks visible at tips
- 发色: Black with cyan/teal underlayer and rainbow prismatic highlights
- 瞳色: Prismatic rainbow shifting (rainbow → emerald-green-yellow depending on scene)
- 肤色: Pale fair with soft peach blush
- 服装: Neon streetwear — acid-green cropped jacket/shorts with zipper detail, black thigh strap/garter belt, multicolored ear cuffs and piercings, long stiletto nails painted alternating cobalt-blue and hot-magenta
- 体型: Slender stylized anime proportions, long legs
- 默认表情: Cool, detached, fashion-editorial stare with subtle smirk
- 识别特征: Multi-pierced ear with rainbow triangle/charm stack; Prismatic rainbow eyes that shift hue per shot; Stiletto nail art alternating blue/magenta; Rainbow eyeliner / tear-drip under-eye marks; Acid-lime green base outfit; Black thigh strap with cyan stripe
- 首次出现: 帧 1
- 稳定帧: [1, 2, 4, 7, 11, 12]
- 一致性评分: 72/100

## 2. 背景与场景
- 主要场景: Abstract neon studio void; Pop-art graphic color-field backdrop; Editorial fashion close-up set
- 环境元素: Liquid paint splatter shapes; Halftone dot patterns; Cyan/teal paint ribbons; Solid color blocks (red/blue/yellow/cyan); Typography title cards (COLOR RIOT / NEON RUSH)
- 场景切换次数: 11
- 景深: Shallow with extreme macro segments, alternating with flat 2D graphic plates
- 明确背景: False

## 3. 色彩倾向
- 主色: Vivid red (#E8252A); Cobalt blue (#1A2FE0); Canary yellow (#F2EC2B)
- 辅色: Cyan / electric aqua (#25E6E2); Hot magenta / pink (#E61FB4); Acid lime green (#9FE02B)
- 色温: Neutral (balanced between warm reds and cool blues/cyans)
- 饱和度: High
- 调色风格: CMYK pop-art / Risograph neon / Y2K cyber-rave
- 主色近似 hex: #E8252A #1A2FE0 #F2EC2B #25E6E2 #E61FB4

## 4. 画风
- 渲染: Cel-shaded anime with painterly 3D hybrid close-ups and flat 2D graphic plates
- 线条: Crisp sharp black line, occasionally broken/scribbled, comic-influenced
- 阴影: Hard cel-shading with one or two tone steps, plus glossy wet highlights on skin/nails/eyes
- 质感: Mix of smooth gloss and halftone/Risograph grain
- 动漫: True
- 子风格: Fashion-editorial anime / 90s-2000s pop-art revival
- 动画师/工作室气质: no clear reference — resembles motion-graphics anime (e.g. Ryo-timo / Perfume MV directors, Eizin Suzuki, or Baha-style cel animation)

## 5. 构图与镜头语言
- 画幅: 2.36:1
- 景别分布: 特写 65%, 中景 10%, 远景 25%
- 运镜: Handheld micro-shake on close-ups, hard cuts and graphic wipes between shots
- 构图习惯: Extreme macro on eyes, hands, nails; Horizontal split-screen panels; Negative-space color bands across frame; Centered title-card typography overlay; Cut-out character over flat color-field

## 6. 光线 / 氛围 / 情绪
- 光源: Artificial studio / stylized neon
- 主光方向: Frontal key with strong rim/back light creating iridescent rainbow rim
- 时段分布: Indoor studio (undefined); Night club/rave implication
- 主情绪: Bold, chaotic, high-energy euphoria
- 次情绪: Rebellious; Sensual; Glitchy; Hyper-stimulated

## 7. 可复用的一致性描述词 (供 prompt 直接引用)

### 7.1 character_block_for_prompt
```
A young anime woman in her early twenties with long straight black hair, blunt bangs and flowing strands showing a teal/cyan underlayer and rainbow prismatic streaks. Skin is pale and fair with a soft peach blush. Eyes are large and prismatic — shifting between full rainbow spectrum and emerald-green-yellow — framed by sharp black lashes and a tiny rainbow tear mark beneath. She wears an acid-lime green cropped jacket and matching shorts with a visible zipper, a black thigh strap with a cyan stripe, and rainbow multi-pierced ear cuffs including a geometric triangle charm. Her long coffin/stiletto nails alternate cobalt-blue and hot-magenta polish. Expression is cool, detached, fashion-editorial with a subtle smirk. Body is slender with stylized anime proportions and long legs.
```

### 7.2 style_block_for_prompt
```
Anime cel-shading with hard 2-tone shading, glossy wet specular highlights on skin and nails, sharp black linework, mixed with flat 2D graphic color-field plates and painterly macro close-ups. Risograph halftone dots, CMYK registration mis-print feel, neon rave aesthetic, motion-graphics anime direction.
```

### 7.3 color_block_for_prompt
```
Hyper-saturated CMYK palette: vivid red, cobalt blue, canary yellow as primaries, with electric cyan, hot magenta and acid lime green as accents. No muted or desaturated tones anywhere.
```

### 7.4 lighting_block_for_prompt
```
Stylized artificial studio lighting with a strong frontal key, hard rim light producing iridescent rainbow edges on hair and skin, glossy wet highlights on eyes and nails. Studio void or flat color-field backgrounds. No naturalistic shadows.
```

### 7.5 banned_in_prompt (生成时禁止)
- realistic photography
- muted or pastel palette
- 3D Pixar render
- soft watercolor shading
- natural outdoor lighting
- historical or period costume
- low saturation
- photorealistic skin texture

---

## 附录 A: 原始 VLM 输出 (raw, 已 parse JSON)

```json
{
  "characters": [
    {
      "name": "Color Riot Girl",
      "appearance": {
        "hair_style": "Long straight black hair with blunt bangs, loose flowing strands, occasional teal/blue underlayer and rainbow streaks visible at tips",
        "hair_color": "Black with cyan/teal underlayer and rainbow prismatic highlights",
        "eye_color": "Prismatic rainbow shifting (rainbow → emerald-green-yellow depending on scene)",
        "skin_tone": "Pale fair with soft peach blush",
        "outfit": "Neon streetwear — acid-green cropped jacket/shorts with zipper detail, black thigh strap/garter belt, multicolored ear cuffs and piercings, long stiletto nails painted alternating cobalt-blue and hot-magenta",
        "body_shape": "Slender stylized anime proportions, long legs",
        "distinctive_features": [
          "Multi-pierced ear with rainbow triangle/charm stack",
          "Prismatic rainbow eyes that shift hue per shot",
          "Stiletto nail art alternating blue/magenta",
          "Rainbow eyeliner / tear-drip under-eye marks",
          "Acid-lime green base outfit",
          "Black thigh strap with cyan stripe"
        ],
        "expression_default": "Cool, detached, fashion-editorial stare with subtle smirk"
      },
      "first_seen_at_frame": 1,
      "stable_frames": [
        1,
        2,
        4,
        7,
        11,
        12
      ],
      "consistency_score": 72
    }
  ],
  "background_and_scenes": {
    "primary_settings": [
      "Abstract neon studio void",
      "Pop-art graphic color-field backdrop",
      "Editorial fashion close-up set"
    ],
    "environment_props": [
      "Liquid paint splatter shapes",
      "Halftone dot patterns",
      "Cyan/teal paint ribbons",
      "Solid color blocks (red/blue/yellow/cyan)",
      "Typography title cards (COLOR RIOT / NEON RUSH)"
    ],
    "scene_changes_count": 11,
    "depth_of_field": "Shallow with extreme macro segments, alternating with flat 2D graphic plates",
    "has_distinct_background": false
  },
  "color_palette": {
    "primary_colors": [
      "Vivid red (#E8252A)",
      "Cobalt blue (#1A2FE0)",
      "Canary yellow (#F2EC2B)"
    ],
    "secondary_colors": [
      "Cyan / electric aqua (#25E6E2)",
      "Hot magenta / pink (#E61FB4)",
      "Acid lime green (#9FE02B)"
    ],
    "color_temperature": "Neutral (balanced between warm reds and cool blues/cyans)",
    "saturation": "High",
    "color_grade_style": "CMYK pop-art / Risograph neon / Y2K cyber-rave",
    "color_anchors_hex_approx": [
      "#E8252A",
      "#1A2FE0",
      "#F2EC2B",
      "#25E6E2",
      "#E61FB4"
    ]
  },
  "art_style": {
    "rendering": "Cel-shaded anime with painterly 3D hybrid close-ups and flat 2D graphic plates",
    "linework": "Crisp sharp black line, occasionally broken/scribbled, comic-influenced",
    "shading": "Hard cel-shading with one or two tone steps, plus glossy wet highlights on skin/nails/eyes",
    "texture": "Mix of smooth gloss and halftone/Risograph grain",
    "is_anime": true,
    "sub_style": "Fashion-editorial anime / 90s-2000s pop-art revival",
    "specific_director_or_studio_vibe": "no clear reference — resembles motion-graphics anime (e.g. Ryo-timo / Perfume MV directors, Eizin Suzuki, or Baha-style cel animation)"
  },
  "composition_and_camera": {
    "aspect_ratio": "2.36:1",
    "framing_distribution": {
      "extreme_closeup_pct": 65,
      "medium_pct": 10,
      "wide_pct": 25
    },
    "camera_movement": "Handheld micro-shake on close-ups, hard cuts and graphic wipes between shots",
    "composition_habits": [
      "Extreme macro on eyes, hands, nails",
      "Horizontal split-screen panels",
      "Negative-space color bands across frame",
      "Centered title-card typography overlay",
      "Cut-out character over flat color-field"
    ]
  },
  "lighting_and_mood": {
    "lighting_source": "Artificial studio / stylized neon",
    "lighting_direction": "Frontal key with strong rim/back light creating iridescent rainbow rim",
    "time_of_day_distribution": [
      "Indoor studio (undefined)",
      "Night club/rave implication"
    ],
    "mood_primary": "Bold, chaotic, high-energy euphoria",
    "mood_secondary": [
      "Rebellious",
      "Sensual",
      "Glitchy",
      "Hyper-stimulated"
    ]
  },
  "reusable_consistency_descriptors": {
    "character_block_for_prompt": "A young anime woman in her early twenties with long straight black hair, blunt bangs and flowing strands showing a teal/cyan underlayer and rainbow prismatic streaks. Skin is pale and fair with a soft peach blush. Eyes are large and prismatic — shifting between full rainbow spectrum and emerald-green-yellow — framed by sharp black lashes and a tiny rainbow tear mark beneath. She wears an acid-lime green cropped jacket and matching shorts with a visible zipper, a black thigh strap with a cyan stripe, and rainbow multi-pierced ear cuffs including a geometric triangle charm. Her long coffin/stiletto nails alternate cobalt-blue and hot-magenta polish. Expression is cool, detached, fashion-editorial with a subtle smirk. Body is slender with stylized anime proportions and long legs.",
    "style_block_for_prompt": "Anime cel-shading with hard 2-tone shading, glossy wet specular highlights on skin and nails, sharp black linework, mixed with flat 2D graphic color-field plates and painterly macro close-ups. Risograph halftone dots, CMYK registration mis-print feel, neon rave aesthetic, motion-graphics anime direction.",
    "color_block_for_prompt": "Hyper-saturated CMYK palette: vivid red, cobalt blue, canary yellow as primaries, with electric cyan, hot magenta and acid lime green as accents. No muted or desaturated tones anywhere.",
    "lighting_block_for_prompt": "Stylized artificial studio lighting with a strong frontal key, hard rim light producing iridescent rainbow edges on hair and skin, glossy wet highlights on eyes and nails. Studio void or flat color-field backgrounds. No naturalistic shadows.",
    "banned_in_prompt": [
      "realistic photography",
      "muted or pastel palette",
      "3D Pixar render",
      "soft watercolor shading",
      "natural outdoor lighting",
      "historical or period costume",
      "low saturation",
      "photorealistic skin texture"
    ]
  },
  "summary": {
    "overall_genre": "Anime music-video / fashion-editorial motion graphics",
    "one_sentence_style": "Hyper-saturated CMYK pop-art anime with prismatic rainbow details, acid-neon streetwear and graphic color-field transitions, shot as macro close-ups in a 2.36:1 widescreen format.",
    "best_for_h3_r2v": true,
    "risks": [
      "Eye color drift between rainbow and green-yellow across shots may confuse identity",
      "Heavy abstract color overlays can wash out face if prompt weights face too low",
      "2.36:1 aspect ratio may be cropped to 16:9 by H3",
      "Piercings and nail details are small and may be lost at lower fidelity",
      "Rapid scene/costume changes (green outfit vs dark top) risk character inconsistency"
    ]
  },
  "_meta": {
    "vlm_model": "minimax-m3",
    "frames_sent": 12,
    "manifest": {
      "ref_video": "D:\\ai-video-pipeline\\input_h3_pv_ref.mp4",
      "extracted_at": "2026-08-21T13:58:50",
      "n_frames": 12,
      "times_sec": [
        0.2,
        1.37,
        3.5,
        8.0,
        10.6,
        12.27,
        14.03,
        15.67,
        20.87,
        24.97,
        27.27,
        30.5
      ],
      "frames": [
        {
          "idx": 1,
          "t_sec": 0.2,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_01_t0.20s.jpg",
          "size_bytes": 121301
        },
        {
          "idx": 2,
          "t_sec": 1.37,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_02_t1.37s.jpg",
          "size_bytes": 78477
        },
        {
          "idx": 3,
          "t_sec": 3.5,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_03_t3.50s.jpg",
          "size_bytes": 123968
        },
        {
          "idx": 4,
          "t_sec": 8.0,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_04_t8.00s.jpg",
          "size_bytes": 108288
        },
        {
          "idx": 5,
          "t_sec": 10.6,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_05_t10.60s.jpg",
          "size_bytes": 27814
        },
        {
          "idx": 6,
          "t_sec": 12.27,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_06_t12.27s.jpg",
          "size_bytes": 71370
        },
        {
          "idx": 7,
          "t_sec": 14.03,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_07_t14.03s.jpg",
          "size_bytes": 94601
        },
        {
          "idx": 8,
          "t_sec": 15.67,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_08_t15.67s.jpg",
          "size_bytes": 94646
        },
        {
          "idx": 9,
          "t_sec": 20.87,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_09_t20.87s.jpg",
          "size_bytes": 99694
        },
        {
          "idx": 10,
          "t_sec": 24.97,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_10_t24.97s.jpg",
          "size_bytes": 112425
        },
        {
          "idx": 11,
          "t_sec": 27.27,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_11_t27.27s.jpg",
          "size_bytes": 117850
        },
        {
          "idx": 12,
          "t_sec": 30.5,
          "path": "D:\\ai-video-pipeline\\ref_analysis_v367\\frames\\frame_12_t30.50s.jpg",
          "size_bytes": 91309
        }
      ],
      "filmstrip_path": "D:\\ai-video-pipeline\\ref_analysis_v367\\filmstrip_4x3.jpg",
      "task_book_section": "oc_task_v367.txt §2.1"
    },
    "generated_at": "2026-08-21T14:00:16"
  },
  "raw_response": "<think>Let me analyze this filmstrip carefully. It's a 4x3 grid of 12 frames from an anime/animated video with a \"Color Riot\" / \"Neon Rush\" theme - very vibrant, saturated, and stylistically bold.\n\nLet me examine each frame:\n\nFrame 1 (t=0.20s): Title card \"COLOR RIOT\" - shows an anime girl with rainbow/prismatic eyes, black hair with colorful highlights, hands framing her face with rainbow/iridescent nail art, dark outfit. Highly stylized.\n\nFrame 2 (t=1.37s): Extreme close-up of an eye with iridescent rainbow reflection, very glossy/3D look.\n\nFrame 3 (t=3.50s): \"NEON RUSH\" title text over split composition with character hair and abstract neon pink/cyan/yellow shapes with halftone dots.\n\nFrame 4 (t=8.0s): Two split panels showing anime girl face - one with green eye, blue hair highlights, geometric triangle earring; another with similar features and pink/magenta elements.\n\nFrame 5 (t=10.6s): Abstract color blocks - red, blue, yellow with cyan edges.\n\nFrame 6 (t=12.27s): Stylized yellow face/eyes with cyan pupils, red borders - very flat, graphic style.\n\nFrame 7 (t=14.03s): Anime character with teal/blue hair in green/yellow outfit, abstract color band overlay.\n\nFrame 8 (t=15.67s): Close-up of hand with alternating blue and pink/magenta nail polish on yellow background.\n\nFrame 9 (t=20.87s): Abstract flowing teal/blue and magenta paint shapes with yellow accents and dark leather-like element.\n\nFrame 10 (t=24.97s): \"COLOR RIOT\" title over red/yellow paint splatter abstract.\n\nFrame 11 (t=27.27s): Close-up of anime girl face - black hair with rainbow streaks, green-yellow prismatic eyes, multiple piercings/earrings with rainbow colors, pointing finger.\n\nFrame 12 (t=30.5s): Lower body shot - green skirt/shorts, black thigh strap, blue nails, against vibrant blue/pink/yellow background.\n\nKey observations:\n- Single main character: anime girl with black hair, rainbow/prismatic eyes (changing between green/yellow and multi-colored), punk/alt fashion style with piercings, colorful nail art\n- Style: Bold anime with strong cel-shading, mixed with hyper-saturated pop art / neon aesthetic\n- Lots of abstract color splash transitions\n- High saturation, primary colors (red, blue, yellow) + neon (cyan, magenta, pink)\n- 16:9 aspect ratio (1358x576 = approximately 2.36:1 actually... wait 1358/576 = 2.357 which is close to cinematic widescreen)\n\nActually 1358/576 ≈ 2.36, so it's 2.36:1 aspect ratio.\n\nLet me compose the JSON response now. I need to be thorough and accurate.\n\nFor the character - she appears in multiple frames:\n- Frame 1: face with rainbow eyes\n- Frame 2: extreme close-up of eye\n- Frame 4: face split (two views)\n- Frame 7: with teal hair\n- Frame 11: close-up face with green-yellow eyes and rainbow earring\n- Frame 12: lower body\n\nThe character has consistent identity:\n- Black hair with rainbow/colorful highlights/streaks\n- Prismatic rainbow eyes (can shift to green-yellow)\n- Fair skin\n- Multiple ear piercings with rainbow charms\n- Colorful nail art (blue, pink, magenta alternating)\n- Punk/alt fashion\n- Green outfit (visible in frames 7 and 12)\n\nconsistency_score: The character is recognizable across frames but the eye color shifts (rainbow → green-yellow), so I'd say around 70-75.\n\nStyle is a mix of anime with heavy pop art / neon / glitch / CMYK aesthetic. Very editorial fashion anime vibe.\n\nColor palette: Primary (red, blue, yellow) + secondary (cyan, magenta, pink, lime green)\n\nLet me write out the complete JSON.</think>\n\n{\n  \"characters\": [\n    {\n      \"name\": \"Color Riot Girl\",\n      \"appearance\": {\n        \"hair_style\": \"Long straight black hair with blunt bangs, loose flowing strands, occasional teal/blue underlayer and rainbow streaks visible at tips\",\n        \"hair_color\": \"Black with cyan/teal underlayer and rainbow prismatic highlights\",\n        \"eye_color\": \"Prismatic rainbow shifting (rainbow → emerald-green-yellow depending on scene)\",\n        \"skin_tone\": \"Pale fair with soft peach blush\",\n        \"outfit\": \"Neon streetwear — acid-green cropped jacket/shorts with zipper detail, black thigh strap/garter belt, multicolored ear cuffs and piercings, long stiletto nails painted alternating cobalt-blue and hot-magenta\",\n        \"body_shape\": \"Slender stylized anime proportions, long legs\",\n        \"distinctive_features\": [\n          \"Multi-pierced ear with rainbow triangle/charm stack\",\n          \"Prismatic rainbow eyes that shift hue per shot\",\n          \"Stiletto nail art alternating blue/magenta\",\n          \"Rainbow eyeliner / tear-drip under-eye marks\",\n          \"Acid-lime green base outfit\",\n          \"Black thigh strap with cyan stripe\"\n        ],\n        \"expression_default\": \"Cool, detached, fashion-editorial stare with subtle smirk\"\n      },\n      \"first_seen_at_frame\": 1,\n      \"stable_frames\": [1, 2, 4, 7, 11, 12],\n      \"consistency_score\": 72\n    }\n  ],\n  \"background_and_scenes\": {\n    \"primary_settings\": [\n      \"Abstract neon studio void\",\n      \"Pop-art graphic color-field backdrop\",\n      \"Editorial fashion close-up set\"\n    ],\n    \"environment_props\": [\n      \"Liquid paint splatter shapes\",\n      \"Halftone dot patterns\",\n      \"Cyan/teal paint ribbons\",\n      \"Solid color blocks (red/blue/yellow/cyan)\",\n      \"Typography title cards (COLOR RIOT / NEON RUSH)\"\n    ],\n    \"scene_changes_count\": 11,\n    \"depth_of_field\": \"Shallow with extreme macro segments, alternating with flat 2D graphic plates\",\n    \"has_distinct_background\": false\n  },\n  \"color_palette\": {\n    \"primary_colors\": [\n      \"Vivid red (#E8252A)\",\n      \"Cobalt blue (#1A2FE0)\",\n      \"Canary yellow (#F2EC2B)\"\n    ],\n    \"secondary_colors\": [\n      \"Cyan / electric aqua (#25E6E2)\",\n      \"Hot magenta / pink (#E61FB4)\",\n      \"Acid lime green (#9FE02B)\"\n    ],\n    \"color_temperature\": \"Neutral (balanced between warm reds and cool blues/cyans)\",\n    \"saturation\": \"High\",\n    \"color_grade_style\": \"CMYK pop-art / Risograph neon / Y2K cyber-rave\",\n    \"color_anchors_hex_approx\": [\"#E8252A\", \"#1A2FE0\", \"#F2EC2B\", \"#25E6E2\", \"#E61FB4\"]\n  },\n  \"art_style\": {\n    \"rendering\": \"Cel-shaded anime with painterly 3D hybrid close-ups and flat 2D graphic plates\",\n    \"linework\": \"Crisp sharp black line, occasionally broken/scribbled, comic-influenced\",\n    \"shading\": \"Hard cel-shading with one or two tone steps, plus glossy wet highlights on skin/nails/eyes\",\n    \"texture\": \"Mix of smooth gloss and halftone/Risograph grain\",\n    \"is_anime\": true,\n    \"sub_style\": \"Fashion-editorial anime / 90s-2000s pop-art revival\",\n    \"specific_director_or_studio_vibe\": \"no clear reference — resembles motion-graphics anime (e.g. Ryo-timo / Perfume MV directors, Eizin Suzuki, or Baha-style cel animation)\"\n  },\n  \"composition_and_camera\": {\n    \"aspect_ratio\": \"2.36:1\",\n    \"framing_distribution\": {\n      \"extreme_closeup_pct\": 65,\n      \"medium_pct\": 10,\n      \"wide_pct\": 25\n    },\n    \"camera_movement\": \"Handheld micro-shake on close-ups, hard cuts and graphic wipes between shots\",\n    \"composition_habits\": [\n      \"Extreme macro on eyes, hands, nails\",\n      \"Horizontal split-screen panels\",\n      \"Negative-space color bands across frame\",\n      \"Centered title-card typography overlay\",\n      \"Cut-out character over flat color-field\"\n    ]\n  },\n  \"lighting_and_mood\": {\n    \"lighting_source\": \"Artificial studio / stylized neon\",\n    \"lighting_direction\": \"Frontal key with strong rim/back light creating iridescent rainbow rim\",\n    \"time_of_day_distribution\": [\"Indoor studio (undefined)\", \"Night club/rave implication\"],\n    \"mood_primary\": \"Bold, chaotic, high-energy euphoria\",\n    \"mood_secondary\": [\"Rebellious\", \"Sensual\", \"Glitchy\", \"Hyper-stimulated\"]\n  },\n  \"reusable_consistency_descriptors\": {\n    \"character_block_for_prompt\": \"A young anime woman in her early twenties with long straight black hair, blunt bangs and flowing strands showing a teal/cyan underlayer and rainbow prismatic streaks. Skin is pale and fair with a soft peach blush. Eyes are large and prismatic — shifting between full rainbow spectrum and emerald-green-yellow — framed by sharp black lashes and a tiny rainbow tear mark beneath. She wears an acid-lime green cropped jacket and matching shorts with a visible zipper, a black thigh strap with a cyan stripe, and rainbow multi-pierced ear cuffs including a geometric triangle charm. Her long coffin/stiletto nails alternate cobalt-blue and hot-magenta polish. Expression is cool, detached, fashion-editorial with a subtle smirk. Body is slender with stylized anime proportions and long legs.\",\n    \"style_block_for_prompt\": \"Anime cel-shading with hard 2-tone shading, glossy wet specular highlights on skin and nails, sharp black linework, mixed with flat 2D graphic color-field plates and painterly macro close-ups. Risograph halftone dots, CMYK registration mis-print feel, neon rave aesthetic, motion-graphics anime direction.\",\n    \"color_block_for_prompt\": \"Hyper-saturated CMYK palette: vivid red, cobalt blue, canary yellow as primaries, with electric cyan, hot magenta and acid lime green as accents. No muted or desaturated tones anywhere.\",\n    \"lighting_block_for_prompt\": \"Stylized artificial studio lighting with a strong frontal key, hard rim light producing iridescent rainbow edges on hair and skin, glossy wet highlights on eyes and nails. Studio void or flat color-field backgrounds. No naturalistic shadows.\",\n    \"banned_in_prompt\": [\n      \"realistic photography\",\n      \"muted or pastel palette\",\n      \"3D Pixar render\",\n      \"soft watercolor shading\",\n      \"natural outdoor lighting\",\n      \"historical or period costume\",\n      \"low saturation\",\n      \"photorealistic skin texture\"\n    ]\n  },\n  \"summary\": {\n    \"overall_genre\": \"Anime music-video / fashion-editorial motion graphics\",\n    \"one_sentence_style\": \"Hyper-saturated CMYK pop-art anime with prismatic rainbow details, acid-neon streetwear and graphic color-field transitions, shot as macro close-ups in a 2.36:1 widescreen format.\",\n    \"best_for_h3_r2v\": true,\n    \"risks\": [\n      \"Eye color drift between rainbow and green-yellow across shots may confuse identity\",\n      \"Heavy abstract color overlays can wash out face if prompt weights face too low\",\n      \"2.36:1 aspect ratio may be cropped to 16:9 by H3\",\n      \"Piercings and nail details are small and may be lost at lower fidelity\",\n      \"Rapid scene/costume changes (green outfit vs dark top) risk character inconsistency\"\n    ]\n  }\n}"
}
```
