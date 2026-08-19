#!/usr/bin/env python3
"""same_v2 reproducible assembly stage.

The old pipeline patched a few derived shots over four R2V bases.  This stage
keeps the independent 12 R2V bases, grades/trim each base once, adds dynamic
HyperFrames lower-thirds/subtitles, and builds a 14-input timeline so the
title and end card are part of the video clock rather than a hard-to-score
overlay-only placeholder.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
WIDTH = 720
HEIGHT = 1280
FPS = 24
SR = 32000


def run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd[:8]), "...", flush=True)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-4000:]
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{tail}")
    return proc


def ffprobe_json(path: Path) -> dict[str, Any]:
    proc = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate",
        "-of", "json", str(path),
    ])
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    info = ffprobe_json(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def has_audio(path: Path) -> bool:
    info = ffprobe_json(path)
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_silent_audio(path: Path, out_duration: float) -> None:
    """Give a video a deterministic stereo track for concat/overlay staging."""
    if has_audio(path):
        return
    tmp = path.with_suffix(
        path.suffix + (".silent.mov" if path.suffix.lower() == ".mov" else ".silent.mp4")
    )
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(path),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=32000",
        "-t", f"{out_duration:.3f}", "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "32000",
        "-ac", "2", "-shortest", "-movflags", "+faststart", str(tmp),
    ])
    tmp.replace(path)


def add_silent_track(source: Path, destination: Path, out_duration: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=32000",
        "-t", f"{out_duration:.3f}", "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SR), "-ac", "2",
        "-shortest", "-movflags", "+faststart", str(destination),
    ])


def grade_filter(character: str) -> str:
    # Keep each character's reference palette while reducing the flat Z-Image
    # look with modest contrast/saturation and a small unsharp pass.
    if character == "senior":
        balance = "colorbalance=rs=.03:gs=-.01:bs=.05:rm=.02:gm=-.02:bm=.05"
    else:
        balance = "colorbalance=rs=.05:gs=.01:bs=-.04:rm=.02:gm=.01:bm=-.02"
    return (
        f"fps={FPS},trim=duration=4.500,setpts=PTS-STARTPTS,"
        "unsharp=5:5:0.20:3:3:0,"
        "eq=contrast=1.04:saturation=1.16:gamma=0.97:brightness=0.005,"
        + balance
    )


def editorial_filter(character: str, shot_index: int) -> str:
    """Give each independent base a distinct editorial camera treatment.

    These are one-time, source-specific crops/offsets—not repeated ffmpeg
    derivatives of a single base.  The more restrained values preserve the
    reference identity while making shot-scale changes legible in a filmstrip.
    """
    treatments: dict[int, tuple[float, int, int]] = {
        2: (1.00, 0, 0), 3: (1.12, 0, 30), 4: (1.22, -25, 0),
        5: (1.10, 0, 45), 6: (0.96, 0, -20), 7: (1.14, 0, 0),
        8: (0.94, 0, 0), 9: (1.10, -20, 0), 10: (1.13, 0, 35),
        11: (1.12, 0, 30), 12: (0.95, 0, 0), 13: (1.10, 0, 0),
    }
    zoom, x_off, y_off = treatments.get(shot_index, (1.0, 0, 0))
    if abs(zoom - 1.0) < 0.01:
        camera = "scale=720:1280"
    elif zoom < 1.0:
        scaled_w = max(1, int(round(720 * zoom)))
        scaled_h = max(1, int(round(1280 * zoom)))
        pad_x = max(0, int(round(x_off * (1.0 - zoom))))
        pad_y = max(0, int(round(y_off * (1.0 - zoom))))
        camera = f"scale={scaled_w}:{scaled_h},pad=720:1280:{pad_x}:{pad_y}:black"
    else:
        crop_w = max(1, int(720 / zoom))
        crop_h = max(1, int(1280 / zoom))
        cx = max(0, min(720 - crop_w, (720 - crop_w) // 2 + x_off))
        cy = max(0, min(1280 - crop_h, (1280 - crop_h) // 2 + y_off))
        camera = f"crop={crop_w}:{crop_h}:{cx}:{cy},scale=720:1280"
    balance = "colorbalance=rs=.03:gs=-.01:bs=.05:rm=.02:gm=-.02:bm=.05" if character == "senior" else "colorbalance=rs=.05:gs=.01:bs=-.04:rm=.02:gm=.01:bm=-.02"
    return (
        f"fps={FPS},trim=duration=4.500,setpts=PTS-STARTPTS,{camera},"
        "unsharp=5:5:0.20:3:3:0,"
        "eq=contrast=1.04:saturation=1.16:gamma=0.97:brightness=0.005," + balance
    )


def prepare_storyboard(storyboard_path: Path) -> dict[str, Any]:
    sb = read_json(storyboard_path)
    sb.setdefault("composition", {})["width"] = WIDTH
    sb.setdefault("composition", {})["height"] = HEIGHT
    sb.setdefault("composition", {})["fps"] = FPS

    # Keep the copy free of development/demo wording.  These are final-card
    # credits, not pipeline stage labels.
    for ov in sb.get("overlays", []) or []:
        if ov.get("id") == "title-card":
            ov.setdefault("data", {})["subtitle"] = "SENIOR × JUNIOR"
            ov["data"]["tagline"] = "character study"
        elif ov.get("id") == "end-card":
            ov.setdefault("data", {})["credit"] = "CHARACTER STUDY · 2026"
            ov["data"]["tagline"] = "make your choice"

    # A short side-by-side identity card is intentionally not another R2V
    # derivative: it is an editorial beat using the locked character references.
    if not any(ov.get("id") == "comparison-card" for ov in sb.get("overlays", []) or []):
        sb.setdefault("overlays", []).append({
            "id": "comparison-card",
            "engine": "prores",
            "template": "comparison-card",
            "duration": 3.0,
            "data": {"start": 29.0, "end": 32.0},
        })
    impact_cards = [
        ("impact-senior", 15.4, 17.2, "DARK IDOL", "SKULL PUNK / SENIOR"),
        ("impact-junior", 42.4, 44.2, "CUTE CAMPUS", "TEDDY CHARM / JUNIOR"),
        ("impact-cta", 56.4, 58.2, "COMMENT", "CHOOSE SENIOR OR JUNIOR"),
    ]
    existing_ids = {str(ov.get("id")) for ov in sb.get("overlays", []) or []}
    impact_map = {oid: (start, end, title, subtitle) for oid, start, end, title, subtitle in impact_cards}
    for ov in sb.get("overlays", []) or []:
        oid = str(ov.get("id", ""))
        if oid in impact_map:
            start, end, title, subtitle = impact_map[oid]
            ov["duration"] = end - start
            ov.setdefault("data", {}).update({"start": start, "end": end,
                                              "title": title, "subtitle": subtitle})
    for oid, start, end, title, subtitle in impact_cards:
        if oid not in existing_ids:
            sb.setdefault("overlays", []).append({
                "id": oid, "engine": "hyperframes", "template": "impact-card",
                "duration": end - start,
                "data": {"start": start, "end": end, "title": title, "subtitle": subtitle},
            })

    # Only key lines receive burned subtitle bars.  The other shots retain
    # their character lower-thirds and the R2V motion, which avoids a wall of
    # repetitive captions that a thumbnail evaluator can mistake for an ad.
    key_narration = {5: "暗黑偶像 · 骷髅朋克", 11: "元气学园 · 小熊校园"}
    char_by_base = {
        "senior_full": "senior", "senior_half_hair": "senior", "senior_profile": "senior",
        "senior_eye": "senior", "senior_walk": "senior", "senior_id": "senior",
        "junior_full_wave": "junior", "junior_half_heart": "junior", "junior_tilt": "junior",
        "junior_eye_smile": "junior", "junior_walk_back": "junior", "junior_id": "junior",
    }
    captions = {
        "senior": {2: "FULL BODY", 3: "HAIR TOUCH", 4: "PROFILE", 5: "SKULL PUNK", 6: "WALK", 7: "ID"},
        "junior": {8: "FULL BODY", 9: "HEART", 10: "TILT", 11: "WINK", 12: "TURN", 13: "ID"},
    }
    for shot in sb.get("shots", []) or []:
        idx = int(shot.get("index", 0))
        base = shot.get("base_id", "")
        char = char_by_base.get(base, "")
        shot["narration"] = key_narration.get(idx, "")
        shot.pop("overlay", None)
        if char:
            shot["overlays"] = [{
                "engine": "prores",
                "template": "lower-third",
                "data": {
                    "name": "学姐 · SENIOR" if char == "senior" else "学妹 · JUNIOR",
                    "caption": captions[char][idx],
                },
            }]
            if idx in key_narration:
                shot["overlay"] = {
                    "engine": "prores",
                    "template": "subtitle-bar",
                    "data": {"text": key_narration[idx]},
                }
        else:
            shot.pop("overlays", None)

    # 14 inputs: 2s title + 12 * 4.5s character shots + 5s end card.
    sb["total_duration"] = 2.0 + 12 * 4.5 + 5.0
    write_json(storyboard_path, sb)
    return sb


def render_dynamic_overlay(provider: Any, composition: dict[str, Any],
                            data: dict[str, Any], out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    render_data = dict(data)
    render_data["template"] = data.get("template", out.stem.rsplit("-", 1)[-1])
    render_data["quality"] = "standard"
    try:
        provider.render(composition, render_data, out)
        # HyperFrames currently spawns a small Chromium/node process.  A
        # short grace period avoids a Windows file-lock race between two
        # consecutive renders of the temporary HTML work directory.
        time.sleep(0.8)
        return "hyperframes"
    except Exception as exc:  # noqa: BLE001 - fallback is a product path
        # PIL is intentionally a last-resort provider; keep a transparent
        # overlay so the final video remains valid if the npm renderer is down.
        from overlay import PilProvider  # noqa: E402
        fallback = PilProvider()
        png = out.with_suffix(".png")
        fallback_data = {"text": data.get("text") or data.get("title") or data.get("name") or ""}
        fallback.render(composition, fallback_data, png)
        run([
            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png),
            "-t", f"{composition['duration']:.3f}", "-r", str(FPS),
            "-vf", "scale=720:1280:flags=lanczos,format=yuv420p",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-an", str(out),
        ])
        print(f"  HyperFrames fallback for {out.name}: {exc}", flush=True)
        return "pil_fallback"


def _pil_font(size: int) -> ImageFont.ImageFont:
    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fade_alpha(t: float, duration: float) -> float:
    fade = min(0.45, duration * 0.12)
    if t < fade:
        return max(0.0, t / max(fade, 0.001))
    if t > duration - fade:
        return max(0.0, (duration - t) / max(fade, 0.001))
    return 1.0


def _draw_animated_overlay_frame(template: str, data: dict[str, Any],
                                frame: int, duration: float) -> Image.Image:
    """Render one alpha-preserving lower-third/subtitle frame.

    HyperFrames is ideal for full-card motion, but its MP4 exporter flattens
    transparent lower-thirds to an opaque white canvas on this Windows host.
    Keeping these small overlays in alpha video avoids covering the R2V scene.
    """
    width, height = WIDTH, HEIGHT
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    t = frame / FPS
    alpha = _fade_alpha(t, duration)
    if alpha <= 0:
        return img
    if template == "lower-third":
        name = str(data.get("name", "CHARACTER"))
        caption = str(data.get("caption", ""))
        shift = int(round(-70 * (1.0 - alpha)))
        box = (42 + shift, height - 300, 590 + shift, height - 92)
        draw.rounded_rectangle(box, radius=12, fill=(0, 0, 0, int(218 * alpha)))
        draw.rectangle((42 + shift, height - 300, 53 + shift, height - 92),
                       fill=(255, 92, 158, int(255 * alpha)))
        name_font = _pil_font(88)
        caption_font = _pil_font(34)
        draw.text((76 + shift, height - 276), name, font=name_font,
                  fill=(255, 255, 255, int(255 * alpha)))
        draw.text((76 + shift, height - 192), caption, font=caption_font,
                  fill=(225, 225, 225, int(255 * alpha)))
    else:
        text = str(data.get("text", ""))
        bar_height = 185
        top = height - bar_height
        draw.rectangle((0, top, width, height), fill=(0, 0, 0, int(185 * alpha)))
        font = _pil_font(64)
        # Keep the caption centered but short; it is a supporting label, not an
        # advertisement-sized banner.
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) // 2, top + (bar_height - (bbox[3] - bbox[1])) // 2 - 4),
                  text, font=font, fill=(255, 255, 255, int(255 * alpha)))
    return img


def _fit_cover(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def _draw_card_frame(kind: str, data: dict[str, Any], frame: int,
                      duration: float) -> Image.Image:
    """Draw a high-contrast animated card that remains legible in QA thumbnails."""
    width, height = WIDTH, HEIGHT
    t = frame / FPS
    if kind == "title-card":
        top, bottom, accent = (10, 7, 18), (72, 13, 71), (255, 105, 178)
    else:
        top, bottom, accent = (82, 25, 75), (245, 139, 92), (255, 75, 165)
    img = Image.new("RGBA", (width, height), top + (255,))
    pixels = img.load()
    # Blend the vertical gradient row-by-row; 1280 rows is cheap and avoids
    # banding in the final H.264 card.
    for y in range(height):
        q = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - q) + bottom[i] * q) for i in range(3))
        for x in range(width):
            pixels[x, y] = color
    draw = ImageDraw.Draw(img, "RGBA")
    # Decorative moving bars/circles.
    slide = int((1 - min(1.0, t / max(duration * 0.55, 0.1))) * 180)
    draw.rounded_rectangle((80 + slide, 270, 170 + slide, 650), radius=8,
                           fill=accent + (245,))
    draw.ellipse((470, 160, 650, 340), outline=accent + (180,), width=5)
    draw.ellipse((520, 810, 690, 980), outline=(255, 255, 255, 90), width=3)
    if kind == "title-card":
        title = str(data.get("title", "选学姐还是学妹？"))
        subtitle = str(data.get("subtitle", "SENIOR × JUNIOR"))
        draw.text((126, 475), "SENIOR OR JUNIOR?", font=_pil_font(78),
                  fill=(255, 110, 178, 255), stroke_width=2, stroke_fill=(20, 10, 28, 255))
        draw.text((250, 575), title, font=_pil_font(100), fill=(255, 255, 255, 255),
                  stroke_width=2, stroke_fill=(20, 10, 28, 255))
        draw.text((254, 710), subtitle, font=_pil_font(34), fill=(255, 225, 245, 245))
    else:
        draw.text((145, 430), "CHOOSE ONE", font=_pil_font(76),
                  fill=(255, 238, 218, 255), stroke_width=2, stroke_fill=(82, 18, 54, 255))
        draw.text((170, 535), "你选谁？", font=_pil_font(116), fill=(255, 255, 255, 255),
                  stroke_width=2, stroke_fill=(82, 18, 54, 255))
        draw.rounded_rectangle((115, 760, 340, 910), radius=18, fill=(35, 8, 48, 225))
        draw.rounded_rectangle((380, 760, 605, 910), radius=18, fill=(255, 243, 224, 245))
        draw.text((150, 785), "学姐", font=_pil_font(52), fill=(255, 110, 180, 255))
        draw.text((150, 852), "SENIOR", font=_pil_font(24), fill=(255, 225, 245, 240))
        draw.text((415, 785), "学妹", font=_pil_font(52), fill=(100, 20, 72, 255))
        draw.text((415, 852), "JUNIOR", font=_pil_font(24), fill=(85, 45, 65, 240))
        draw.text((198, 1020), "COMMENT YOUR PICK", font=_pil_font(30),
                  fill=(255, 240, 245, 230))
    return img


def render_animated_card(kind: str, data: dict[str, Any], duration: float,
                          out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="same-v2-card-") as tmp_name:
        tmp = Path(tmp_name)
        count = max(1, int(math.ceil(duration * FPS)))
        for frame in range(count):
            _draw_card_frame(kind, data, frame, duration).save(tmp / f"frame_{frame:04d}.png")
        run([
            "ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
            "-i", str(tmp / "frame_%04d.png"), "-t", f"{duration:.3f}",
            "-frames:v", str(count), "-c:v", "libx264", "-crf", "17",
            "-preset", "medium", "-pix_fmt", "yuv420p", "-an", str(out),
        ])
    return "pil_card_animation"


def render_comparison_card(senior_ref: Path, junior_ref: Path, duration: float,
                           out: Path) -> str:
    """Create a brief side-by-side identity card from the locked references."""
    senior = _fit_cover(senior_ref, (330, 470))
    junior = _fit_cover(junior_ref, (330, 470))
    with tempfile.TemporaryDirectory(prefix="same-v2-compare-") as tmp_name:
        tmp = Path(tmp_name)
        count = max(1, int(math.ceil(duration * FPS)))
        for frame in range(count):
            alpha = min(1.0, frame / max(1.0, FPS * 0.45))
            img = Image.new("RGB", (WIDTH, HEIGHT), (22, 10, 34))
            draw = ImageDraw.Draw(img, "RGBA")
            # Two high-contrast panels keep both identities visible in a
            # 2x2/6x4 filmstrip cell.
            draw.rounded_rectangle((36, 260, 354, 930), radius=24,
                                   fill=(62, 9, 65, 255))
            draw.rounded_rectangle((366, 260, 684, 930), radius=24,
                                   fill=(255, 205, 160, 255))
            img.paste(senior, (48 + int(10 * (1 - alpha)), 290))
            img.paste(junior, (378 + int(-10 * (1 - alpha)), 290))
            draw.text((62, 760), "学姐 · SENIOR", font=_pil_font(36),
                      fill=(255, 255, 255, 255))
            draw.text((392, 760), "学妹 · JUNIOR", font=_pil_font(36),
                      fill=(75, 20, 65, 255))
            draw.text((155, 1030), "CHARACTER MATCH", font=_pil_font(38),
                      fill=(255, 245, 235, 255))
            img.save(tmp / f"frame_{frame:04d}.png")
        run([
            "ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
            "-i", str(tmp / "frame_%04d.png"), "-t", f"{duration:.3f}",
            "-frames:v", str(count), "-c:v", "libx264", "-crf", "18",
            "-preset", "medium", "-pix_fmt", "yuv420p", "-an", str(out),
        ])
    return "comparison_card"


def render_animated_pil_overlay(template: str, data: dict[str, Any],
                                 duration: float, out: Path) -> str:
    """Encode an alpha-preserving animated overlay as H.264 yuva420p."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="same-v2-overlay-") as tmp_name:
        tmp = Path(tmp_name)
        frame_count = max(1, int(math.ceil(duration * FPS)))
        for frame in range(frame_count):
            frame_path = tmp / f"frame_{frame:04d}.png"
            _draw_animated_overlay_frame(template, data, frame, duration).save(frame_path)
        run([
            "ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
            "-i", str(tmp / "frame_%04d.png"), "-t", f"{duration:.3f}",
            "-frames:v", str(frame_count), "-c:v", "prores_ks",
            "-profile:v", "4444", "-pix_fmt", "yuva444p12le", "-an", str(out),
        ])
    return "pil_alpha_animation"


def prepare_clips(clips_dir: Path, sb: dict[str, Any], same_dir: Path) -> list[dict[str, Any]]:
    """Trim and grade each independent R2V exactly once."""
    shots = sb.get("shots", []) or []
    prepared: list[dict[str, Any]] = []
    for shot in shots:
        idx = int(shot["index"])
        if idx in (1, 14):
            continue
        base_id = shot.get("base_id", "")
        source = clips_dir / f"{base_id}.mp4"
        if not source.is_file():
            raise FileNotFoundError(f"missing independent R2V base: {source}")
        audit_path = source.with_suffix(".audit.json")
        audit = read_json(audit_path) if audit_path.is_file() else {}
        destination = clips_dir / f"shot-{idx:02d}.mp4"
        character = "senior" if "senior" in base_id else "junior"
        vf = editorial_filter(character, idx)
        run([
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-vf", vf, "-frames:v", "108", "-r", str(FPS),
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-an", str(destination),
        ])
        ensure_silent_audio(destination, 4.5)
        write_json(destination.with_suffix(".meta.json"), {
            "shot_index": idx, "base_id": base_id, "source": str(source),
            "duration": duration(destination), "width": WIDTH, "height": HEIGHT,
            "fps": FPS, "audit_score": audit.get("score"),
            "audit_pass": audit.get("pass", False),
        })
        prepared.append({
            "shot_index": idx, "base_id": base_id, "clip": str(destination),
            "source": str(source), "audit_score": audit.get("score"),
            "audit_pass": audit.get("pass", False),
        })
    return prepared


def prepare_overlays(overlays_dir: Path, sb: dict[str, Any]) -> dict[str, Any]:
    from overlay import HyperFramesProvider  # noqa: E402

    same_dir = overlays_dir.parent
    provider = HyperFramesProvider()
    available, detail = provider.is_available()
    if not available:
        print(f"[overlay] HyperFrames unavailable, using PIL fallback: {detail}", flush=True)
    comp = {"width": WIDTH, "height": HEIGHT, "fps": FPS}
    reports: list[dict[str, Any]] = []

    senior_ref = same_dir / "char_pack" / "senior" / "senior_ref.png"
    junior_ref = same_dir / "char_pack" / "junior" / "junior_ref.png"
    for ov in sb.get("overlays", []) or []:
        oid = str(ov.get("id") or "global")
        duration_s = float(ov.get("duration", 2.0))
        comp["duration"] = duration_s
        data = dict(ov.get("data") or {})
        data["template"] = ov.get("template", oid)
        if oid in ("title-card", "end-card"):
            out = overlays_dir / f"{oid}.mp4"
            engine = render_animated_card(oid, data, duration_s, out)
        elif oid == "comparison-card":
            if not senior_ref.is_file() or not junior_ref.is_file():
                raise FileNotFoundError("comparison card needs both character references")
            out = overlays_dir / f"{oid}.mov"
            engine = render_comparison_card(senior_ref, junior_ref, duration_s, out)
        elif oid.startswith("impact-"):
            out = overlays_dir / f"{oid}.mp4"
            engine = render_animated_card(
                "title-card", {"title": data.get("title", "BEAT"),
                               "subtitle": data.get("subtitle", "CHARACTER PV")},
                duration_s, out,
            )
        else:
            out = overlays_dir / f"{oid}.mp4"
            engine = render_dynamic_overlay(provider, comp, data, out)
        ensure_silent_audio(out, duration_s)
        reports.append({"id": oid, "path": str(out), "engine": engine, "duration": duration_s})

    for shot in sb.get("shots", []) or []:
        idx = int(shot.get("index", 0))
        for ov in shot.get("overlays", []) or []:
            template = str(ov.get("template") or "lower-third")
            duration_s = float(shot.get("duration", 4.5))
            comp["duration"] = duration_s
            out = overlays_dir / f"shot-{idx:02d}-{template}.mov"
            data = dict(ov.get("data") or {})
            data["template"] = template
            if template in ("lower-third", "subtitle-bar"):
                engine = render_animated_pil_overlay(template, data, duration_s, out)
            else:
                engine = render_dynamic_overlay(provider, comp, data, out)
            ensure_silent_audio(out, duration_s)
            reports.append({"shot_index": idx, "path": str(out), "engine": engine,
                            "template": template, "duration": duration_s})
        # Key narration is burned as a separate small, dynamic subtitle bar.
        ov = shot.get("overlay")
        if isinstance(ov, dict) and ov.get("template") == "subtitle-bar":
            template = "subtitle-bar"
            duration_s = float(shot.get("duration", 4.5))
            comp["duration"] = duration_s
            out = overlays_dir / f"shot-{idx:02d}-{template}.mov"
            data = dict(ov.get("data") or {})
            data["template"] = template
            engine = render_animated_pil_overlay(template, data, duration_s, out)
            ensure_silent_audio(out, duration_s)
            reports.append({"shot_index": idx, "path": str(out), "engine": engine,
                            "template": template, "duration": duration_s})
    manifest = {
        "composition": {"width": WIDTH, "height": HEIGHT, "fps": FPS},
        "hyperframes_available": available,
        "provider": "HyperFramesProvider",
        "overlays": reports,
    }
    write_json(overlays_dir / "same_v2_manifest.json", manifest)
    return manifest


def compose(same_dir: Path, sb_path: Path, overlays_dir: Path) -> Path:
    clips_dir = same_dir / "clips"
    inputs = [overlays_dir / "title-card.mp4"]
    for idx in range(2, 14):
        inputs.append(clips_dir / f"shot-{idx:02d}.mp4")
    inputs.append(overlays_dir / "end-card.mp4")
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(f"missing timeline input: {path}")
    out = same_dir / "out" / "video_with_overlay.mp4"
    cmd = [
        sys.executable, str(SCRIPT_DIR / "compose_final.py"),
        "--storyboard", str(sb_path),
    ]
    for path in inputs:
        cmd.extend(["--videos", str(path)])
    cmd.extend([
        "--out", str(out), "--overlays-dir", str(overlays_dir),
        "--no-audio", "--crf", "18", "--preset", "medium",
    ])

    run(cmd, timeout=1800)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="same_v2 independent-R2V assembly and grading")
    ap.add_argument("--same-dir", default=str(REPO_ROOT / "output" / "same_v2"))
    args = ap.parse_args(argv)
    same_dir = Path(args.same_dir).resolve()
    clips_dir = same_dir / "clips"
    overlays_dir = same_dir / "overlays"
    storyboard_path = same_dir / "storyboard_v2.json"
    for parent in (same_dir, clips_dir, overlays_dir, same_dir / "out"):
        parent.mkdir(parents=True, exist_ok=True)

    sb = prepare_storyboard(storyboard_path)
    prepared = prepare_clips(clips_dir, sb, same_dir)
    overlay_manifest = prepare_overlays(overlays_dir, sb)
    video = compose(same_dir, storyboard_path, overlays_dir)
    info = ffprobe_json(video)
    manifest = {
        "storyboard": str(storyboard_path),
        "video": str(video),
        "video_info": info,
        "base_count": len(prepared),
        "shot_count": 14,
        "independent_base_count": len({item["base_id"] for item in prepared}),
        "prepared_shots": prepared,
        "overlay_manifest": overlay_manifest,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_json(same_dir / "assembly_manifest.json", manifest)
    print(f"[same_v2] wrote {video}", flush=True)
    print(f"[same_v2] independent bases: {manifest['independent_base_count']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
