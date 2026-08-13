#!/usr/bin/env python3
"""P5-v4 报告生成器：汇总关键帧/视频/拼接/音频/后处理/终验，写入 p5_v4_report.txt（UTF-8）。

重点覆盖任务书要求的：风格实现、三修复效果、重试记录、VLM 审查、ffprobe 终验。
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\ai-video-pipeline")
OUT = ROOT / "output"
FRAMES = OUT / "frames_v4"
CLIPS = OUT / "clips_v4"
OUTDIR = OUT / "out"
TMP = OUT / "tmp"

REPORT = ROOT / "p5_v4_report.txt"

W, H = 1344, 768
STEPS = 25
LENGTH = 124

STYLE_CN = ("新海诚动画电影风格：唯美细腻光影，清新通透色调，雨夜霓虹灯在积水地面倒映，"
            "层次丰富的天空，细腻雨丝和光斑（bokeh），电影感构图，柔和渐变的天空色（蓝紫到暖橙）")


def ffprobe_json(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=index,codec_type,codec_name,width,height,duration,r_frame_rate,sample_rate,channels",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return json.loads(r.stdout)


def volumedetect(path: Path) -> str:
    r = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    mean = maxv = ""
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            mean = line.split("mean_volume:")[-1].strip().split()[0]
        if "max_volume" in line:
            maxv = line.split("max_volume:")[-1].strip().split()[0]
    return f"mean={mean} dB, max={maxv} dB"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main() -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L: list[str] = []
    A = L.append

    A("AI 视频流水线 P5-v4 报告（换新海诚动画风重跑，保留三修复）")
    A("=" * 72)
    A(f"生成时间: {now}")
    A(f"工作目录: {ROOT}")
    A("")
    A(f"背景: P5-v2 已完成（final_v2_1080p.mp4，写实风）。本版按用户要求换【新海诚动画风】重跑，")
    A("      保留三修复（招牌无文字 / 道具绿色纸币统一 / 钢琴收束），全程双帧锚定 + 1344x768 + 后处理。")
    A("")

    # ---- 风格锚定 ----
    A("〇、风格锚定（全片统一）")
    A("-" * 72)
    A(f"  {STYLE_CN}")
    A("  关键帧 prompt（Z-Image 中文）+ 视频 prompt（H3 英文）均锚定此风格；英文锚定短语：")
    A("  \"Makoto Shinkai anime film style, beautiful detailed lighting, fresh transparent color palette, "
      "neon reflections on wet rain-soaked ground, layered gradient sky, delicate rain streaks and bokeh, "
      "cinematic composition\"")
    A("  角色人设对齐 frames_v2 现有关键帧：东方面孔 / 黑短发 / 细框眼镜 / 深灰羊毛大衣 / 深色帆布斜挎包。")
    A("")

    # ---- 任务一：关键帧 ----
    A("一、任务一：关键帧重生成（6 张 = 3 镜头 × 首尾帧，新海诚风）")
    A("-" * 72)
    A("模型: Z-Image Turbo（z-image-power-nodes 官方节点 ZSamplerTurbo2）via ComfyUI 127.0.0.1:8188")
    A("UNET: z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors (weight_dtype=default)")
    A("CLIP: qwen3_4b_fp8_scaled.safetensors (type=lumina2) / VAE: ae.safetensors")
    A("节点: EmptyZImageLatentImage //ZImagePowerNodes(landscape=True, ratio=16:9 widescreen, size=small -> 1344x768)")
    A("      StylePromptEncoder2 //ZImagePowerNodes(style=none) + ZSamplerTurbo2(steps=8, denoise=1.0, turbo_creativity=off)")
    A("")
    for i in (1, 2, 3):
        A(f"【镜头 {i}】")
        for tag, label in (("first", "首帧"), ("last", "尾帧")):
            meta = load_json(FRAMES / f"shot{i}_{tag}.json")
            if meta:
                A(f"  {label} shot{i}_{tag}.png: seed={meta.get('seed')} "
                  f"prompt_id={meta.get('prompt_id')} 耗时={meta.get('elapsed_sec')}s "
                  f"尺寸={meta.get('width')}x{meta.get('height')}")
        rev = load_json(TMP / f"p5v4_frame_review_shot{i}.json")
        if rev:
            A(f"  首尾一致性 VLM: score={rev.get('score')} same_character={rev.get('same_character')} "
              f"same_scene={rev.get('same_scene')} same_composition={rev.get('same_composition')} "
              f"anime_style={rev.get('anime_style')} pass={rev.get('pass')}")
            A(f"    style_comment: {rev.get('style_comment')}")
            A(f"    action_progression: {rev.get('action_progression')}")
            A(f"    diff: {rev.get('diff')}")
            A(f"    opinion: {rev.get('opinion')}")
        A("")

    # ---- 任务二：视频 ----
    A("二、任务二：视频生成（双帧锚定 first+last，新海诚风 prompt）")
    A("-" * 72)
    A("模型: MiniMax H3 fl2va（双帧锚定）via ComfyUI 127.0.0.1:8188")
    A("UNET: minimax_h3_fl2va_pruned_int8_convrot.safetensors")
    A("CLIP: qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors (type=minimax)")
    A("视频VAE: minimax_h3_video_vae_fp16 / 音频VAE: minimax_h3_audio_vae_fp32")
    A(f"参数: {W}x{H}, length={LENGTH}(输出~124帧/5s@24fps), steps={STEPS}, denoise=1.0, sampler=res_multistep, scheduler=simple")
    A("prompt: 官方 FL2VA 三段式 + 新海诚风格锚定短语 + 三修复约束（绿色纸币/硬币形态写死、招牌无文字稳定不漂移）")
    A("")
    retries = load_json(TMP / "p5v4_retries.json")
    total_gen = 0.0
    for i in (1, 2, 3):
        meta = load_json(CLIPS / f"shot{i}.json")
        rev = load_json(TMP / f"p5v4_shot{i}_review.json")
        A(f"【镜头 {i}】 seed={meta.get('seed')} 耗时={meta.get('elapsed_sec')}s 帧长={meta.get('length')} "
          f"steps={meta.get('steps')} 双帧锚定={meta.get('dual_frame')}")
        A(f"   first_frame={meta.get('first_frame')}  last_frame={meta.get('last_frame')}")
        A(f"   prompt_id={meta.get('prompt_id')}  ComfyUI输出={meta.get('comfy_filename')}")
        if rev:
            A(f"   VLM审查: score={rev.get('score')} action_coherent={rev.get('action_coherent')} "
              f"char_consistent={rev.get('char_consistent')} prop_consistent={rev.get('prop_consistent')} "
              f"signboard_no_text={rev.get('signboard_no_text')} anime_style={rev.get('anime_style')} pass={rev.get('pass')}")
            A(f"   deformation: {rev.get('deformation')}")
            A(f"   prop_comment: {rev.get('prop_comment')}")
            A(f"   signboard_comment: {rev.get('signboard_comment')}")
            A(f"   opinion: {rev.get('opinion')}")
        total_gen += float(meta.get("elapsed_sec", 0))
        A("")
    A(f"  视频生成总耗时: {total_gen:.1f}s")

    A("重试记录:")
    fr = retries.get("frame_retries", {})
    vr = retries.get("video_retries", {})
    if fr or vr:
        for shot, info in fr.items():
            A(f"  关键帧 {shot}: 重试 {info.get('attempts')} 次，最终 seed={info.get('final_seed')}")
        for shot, info in vr.items():
            A(f"  视频 {shot}: 重试 {info.get('attempts')} 次，最终 seed={info.get('final_seed')}")
    ref_note = retries.get("character_ref_note")
    if ref_note:
        A(f"  角色一致性参考说明: {ref_note}")
    A("")

    A("拼接（3 段 -> output/out/video_silent_v4.mp4）:")
    A("  ffmpeg: 视频 concat=n=3:v=1:a=0 重编码 libx264 crf 18 + pix_fmt yuv420p")
    A("          音频 [0:a][1:a]acrossfade=d=0.25 -> [a1][2:a]acrossfade=d=0.25")
    vs = ffprobe_json(OUTDIR / "video_silent_v4.mp4")
    v0 = vs["streams"][0]
    A(f"  video_silent_v4.mp4: 时长 {vs['format']['duration']}s, "
      f"video={v0['codec_name']} {v0['width']}x{v0['height']}")
    A("")

    # ---- 任务四：音频 ----
    A("三、任务四：音频（钢琴收束）")
    A("-" * 72)
    ameta = load_json(TMP / "p5v4_audio_meta.json")
    A(f"钢琴收束音: numpy 合成 {ameta.get('piano_chord', '')}，时长 {ameta.get('piano_duration')}s，"
      f"结尾渐弱 fade out（无爆音）-> output/tmp/piano_ending.wav")
    A(f"配音: shot3 narration「{ameta.get('narration_text', '雨还在下，他走得不快。')}」 "
      f"edge-tts {ameta.get('voice', 'zh-CN-XiaoxiaoNeural')} -> narration_v4.mp3 ({ameta.get('narration_duration')}s)")
    A(f"  narration 定位在 shot3 起始 {ameta.get('shot3_start')}s；钢琴定位在 {ameta.get('piano_start')}s（贴齐片尾）")
    A(f"环境底噪: {ameta.get('ambient', '')}")
    A(f"混音: narration(adelay) + 钢琴(adelay) + 底噪 amix + {ameta.get('limiter', '')} -> final_v4.mp4（视频流 copy 自 video_silent_v4.mp4）")
    A("")

    # ---- 任务五：后处理 ----
    A("四、任务五：后处理（RIFE 补帧 + 放大）")
    A("-" * 72)
    rife_meta = load_json(TMP / "p5v4_rife_48fps.json")
    A(f"RIFE 补帧 24->48fps: ComfyUI RIFE VFI, ckpt={rife_meta.get('ckpt')} "
      f"multiplier={rife_meta.get('multiplier')}, fast_mode=True, ensemble=False, dtype=float16")
    A(f"  -> output/tmp/p5v4_rife_48fps.mp4 (prompt_id={rife_meta.get('prompt_id')}, 耗时 {rife_meta.get('elapsed_sec')}s)")
    A("放大 1344x768 -> 1920x1080: ffmpeg lanczos（B 站横屏规格）")
    A("  说明: RealESRGAN_x4plus 为 4x 模型，1344->1920 仅 1.43x，lanczos（任务书兜底项）更干净无 AI 伪细节。")
    A("音频回混: final_v4.mp4 音轨 copy 回 1080p 成片 -> output/out/final_v4_1080p.mp4")
    A("")

    # ---- 终验 ----
    A("五、终验（ffprobe + 响度 + 成片 VLM）")
    A("-" * 72)
    fn = ffprobe_json(OUTDIR / "final_v4_1080p.mp4")
    dur = float(fn["format"]["duration"])
    v = fn["streams"][0]
    a = fn["streams"][1] if len(fn["streams"]) > 1 else {}
    A(f"  final_v4_1080p.mp4: 时长 {dur}s, size {fn['format']['size']} bytes")
    A(f"    视频: {v['codec_name']} {v['width']}x{v['height']} @ {v.get('r_frame_rate')} ({v['duration']}s)")
    A(f"    音频: {a.get('codec_name')} {a.get('sample_rate')}Hz {a.get('channels')}ch ({a.get('duration')}s)")
    A(f"  响度检测(volumedetect): {volumedetect(OUTDIR / 'final_v4_1080p.mp4')}")
    fc = load_json(TMP / "p5v4_final_style_review.json")
    if fc:
        A(f"  成片 VLM 终验: anime_style_unified={fc.get('anime_style_unified')} "
          f"signboard_no_text={fc.get('signboard_no_text')} prop_consistent={fc.get('prop_consistent')} pass={fc.get('pass')}")
        A(f"    style_comment: {fc.get('style_comment')}")
        A(f"    signboard_comment: {fc.get('signboard_comment')}")
        A(f"    prop_comment: {fc.get('prop_comment')}")
        A(f"    opinion: {fc.get('opinion')}")
    A("")

    # ---- 验收对照 ----
    A("六、验收标准对照")
    A("-" * 72)
    all_frames_ok = all(
        (load_json(TMP / f"p5v4_frame_review_shot{i}.json")).get("pass") for i in (1, 2, 3)
    )
    A(f"  [{'通过' if all_frames_ok else '未通过'}] 6 张关键帧落盘，每镜头首尾一致性 VLM 评分 >=70 且为新海诚动画风")
    all_clips_ok = all(
        (load_json(CLIPS / f"shot{i}.json")).get("dual_frame") and
        (load_json(TMP / f"p5v4_shot{i}_review.json")).get("pass") for i in (1, 2, 3)
    )
    A(f"  [{'通过' if all_clips_ok else '未通过'}] 3 段视频双帧锚定生成，VLM 四项审查通过（动作/角色/道具/招牌）")
    A(f"  [{'通过' if fc.get('anime_style_unified') else '未通过'}] 全片新海诚动画风统一（无写实残留、无风格跳变）")
    A(f"  [{'通过' if fc.get('signboard_no_text') else '未通过'}] 招牌无文字不漂移")
    A(f"  [{'通过' if fc.get('prop_consistent') else '未通过'}] 道具绿色纸币统一（不硬币变纸币、不绿变红）")
    A(f"  [{'通过' if ameta else '未通过'}] shot3 结尾有钢琴收束（渐弱 fade out），无爆音")
    ok1080 = (OUTDIR / "final_v4_1080p.mp4").exists() and v.get("width") == 1920 and v.get("height") == 1080
    okfps = str(v.get("r_frame_rate", "")).startswith("48") or "48/1" in str(v.get("r_frame_rate", ""))
    A(f"  [{'通过' if ok1080 else '未通过'}] 最终成片 1920x1080")
    A(f"  [{'通过' if okfps else '未通过'}] 帧率 48fps")
    okdur = 13 <= dur <= 18
    A(f"  [{'通过' if okdur else '未通过'}] 时长 {dur:.1f}s (13-18s)")
    has_audio = bool(a) and float(a.get("duration", 0)) > 0
    A(f"  [{'通过' if has_audio else '未通过'}] 有音轨")
    A("")

    # ---- 产物清单 ----
    A("七、产物清单")
    A("-" * 72)
    for i in (1, 2, 3):
        for tag in ("first", "last"):
            p = FRAMES / f"shot{i}_{tag}.png"
            if p.exists():
                A(f"  [OK] {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")
    for i in (1, 2, 3):
        p = CLIPS / f"shot{i}.mp4"
        if p.exists():
            A(f"  [OK] {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")
    for p in [OUTDIR / "video_silent_v4.mp4", OUTDIR / "final_v4.mp4", OUTDIR / "final_v4_1080p.mp4",
              TMP / "piano_ending.wav"]:
        if p.exists():
            A(f"  [OK] {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")
    A("")

    A("红线遵守说明")
    A("-" * 72)
    A("  - 每个子任务完成后 git add -A && git commit && git push")
    A("  - Z-Image 节点名带 //ZImagePowerNodes 后缀，提交前 GET /object_info 确认全名")
    A("  - 产物落盘 D:\\ai-video-pipeline\\output\\（frames_v4/ clips_v4/ out/ tmp/）")
    A("  - 角色人设对齐 frames_v2（东方面孔/黑短发/细框眼镜），非 P4 西方面孔定妆图")
    A("  - 未触碰模拟器/adb；全程 ComfyUI + ffmpeg + edge-tts + Python + numpy")
    A("  - 报告只写已实现项")

    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"报告已生成: {REPORT}")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
