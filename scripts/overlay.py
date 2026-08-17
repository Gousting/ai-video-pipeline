#!/usr/bin/env python3
"""AI 视频流水线 Phase B：overlay 包装层（providers）。

定义 `OverlayProvider` 抽象（typing.Protocol），以及三个实现：
  - PilProvider：纯 PIL 渲染底部半透明字幕条 → PNG。可作为兜底 provider。
  - HyperFramesProvider：subprocess 调 `npx hyperframes`，把 HTML 模板渲染成 MP4 叠加层。
  - RemotionProvider：空壳占位，将来对接数据驱动视频时再实现。

约定：
  - `composition` dict 至少包含 `width` / `height`（像素）；HyperFrames 还会用 `duration`（秒）、`fps`。
  - `data` dict 是模板需要的占位符（如 title / subtitle / text / name 等）。
  - `out` 是目标文件路径（.png for PIL，.mp4 for HyperFrames）。
  - 所有路径都限定在 D:\\ai-video-pipeline 内；临时目录走系统 TMP。

不引入任何 npm 包到 Python 进程；HyperFrames 走 CLI subprocess。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Protocol

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - 依赖缺失时的友好提示
    sys.stderr.write("错误：缺少 Pillow 依赖，请先执行 pip install pillow\n")
    sys.exit(2)

# 项目根目录：所有读写的锚点。
REPO_ROOT = Path(__file__).resolve().parent.parent
OVERLAYS_DIR = REPO_ROOT / "overlays"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "overlays"


# ---------------------------------------------------------------------------
# OverlayProvider 抽象
# ---------------------------------------------------------------------------


class OverlayProvider(Protocol):
    """overlay provider 接口。所有实现必须提供 `id` 和 `render` 两个成员。"""

    @property
    def id(self) -> str:  # pragma: no cover - Protocol 抽象
        ...

    def render(
        self,
        composition: Mapping[str, object],
        data: Mapping[str, object],
        out: Path,
    ) -> Path:  # pragma: no cover - Protocol 抽象
        ...


# ---------------------------------------------------------------------------
# PilProvider：纯 PIL 字幕条
# ---------------------------------------------------------------------------


class PilProvider:
    """用 Pillow 画底部半透明黑底字幕条，输出 PNG。"""

    id = "pil"

    # 默认参数；调用方可在 data 里覆盖 font_size / bar_ratio / text_color / bg_color。
    DEFAULTS = {
        "font_size": 48,
        "bar_ratio": 0.18,         # 字幕条高度占画面高度的比例
        "text_color": (255, 255, 255, 255),
        "bg_color": (0, 0, 0, 170),  # 65% 透明度的黑底
        "padding_x": 48,           # 左右内边距（像素）
    }

    def render(
        self,
        composition: Mapping[str, object],
        data: Mapping[str, object],
        out: Path,
    ) -> Path:
        """渲染字幕 PNG 到 `out`。

        composition: 至少 width / height。
        data: 至少 text；可选 font_size / bar_ratio / text_color / bg_color / padding_x / font_path。
        """
        width = int(composition.get("width", 1920))
        height = int(composition.get("height", 1080))
        text = str(data.get("text", ""))
        if not text:
            raise ValueError("PilProvider.render: data['text'] 不能为空")

        font_size = int(data.get("font_size", self.DEFAULTS["font_size"]))
        bar_ratio = float(data.get("bar_ratio", self.DEFAULTS["bar_ratio"]))
        text_color = tuple(data.get("text_color", self.DEFAULTS["text_color"]))
        bg_color = tuple(data.get("bg_color", self.DEFAULTS["bg_color"]))
        padding_x = int(data.get("padding_x", self.DEFAULTS["padding_x"]))
        font_path = data.get("font_path")  # 可显式指定字体路径

        # 全透明背景，字幕条用半透明矩形画在最下方。
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        bar_height = max(int(height * bar_ratio), font_size + 24)
        bar_top = height - bar_height
        # 半透明黑底铺满底部条
        draw.rectangle([(0, bar_top), (width, height)], fill=bg_color)

        # 字体：优先用显式 font_path，否则尝试常见中文字体，最后退回 PIL 默认字体。
        font = self._load_font(font_path, font_size)

        # 自动换行：按可用宽度测量行宽，超出就换行。
        max_text_width = width - 2 * padding_x
        lines = self._wrap_text(draw, text, font, max_text_width)
        line_height = int(font_size * 1.4)
        total_text_h = line_height * len(lines)
        y = bar_top + (bar_height - total_text_h) // 2
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            x = (width - tw) // 2  # 居中
            draw.text((x, y), line, font=font, fill=text_color)
            y += line_height

        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 强制 .png 后缀，避免误传别的扩展名。
        if out.suffix.lower() != ".png":
            out = out.with_suffix(".png")
        img.save(out, format="PNG")
        return out

    @staticmethod
    def _load_font(font_path: object, font_size: int) -> ImageFont.ImageFont:
        """加载字体：优先显式路径 → 常见系统字体 → PIL 默认。"""
        if isinstance(font_path, str) and font_path and Path(font_path).is_file():
            return ImageFont.truetype(font_path, font_size)
        # 常见 Windows 中文字体候选（按可用性回退）
        for cand in (
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\msyh.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ):
            if Path(cand).is_file():
                try:
                    return ImageFont.truetype(cand, font_size)
                except OSError:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _wrap_text(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        """按 max_width 把 text 切成多行；中英文混合按字符切，简单稳定。"""
        lines: list[str] = []
        current = ""
        for ch in text:
            candidate = current + ch
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines


# ---------------------------------------------------------------------------
# HyperFramesProvider：HTML→MP4（subprocess 调 npx hyperframes）
# ---------------------------------------------------------------------------


class HyperFramesProvider:
    """通过 `npx hyperframes` 把 HTML 模板渲染成 MP4 叠加层。

    工作流：
      1) 在临时目录跑 `hyperframes init --example blank --non-interactive`，
         生成 index.html / hyperframes.json / package.json。
      2) 用 data 字典把模板里的 `{{key}}` 占位符和 `data-*="__KEY__"` 占位符
         替换成实际值，覆盖 index.html。
      3) 跑 `hyperframes render <tmpdir> -o <out.mp4> --quality draft`。
    """

    id = "hyperframes"

    # 渲染参数默认
    DEFAULTS = {
        "quality": "draft",     # draft/standard/high；CI/agent 默认 draft 更快
        "fps": 30,
        "timeout_s": 600,       # 单次 render 的 subprocess 超时
    }

    def __init__(self, npx_cmd: str | None = None, npm_cache_dir: Path | None = None):
        # 允许测试时注入假的 npx 路径；默认在 PATH 里找一个真正可执行的 npx(.cmd)。
        self.npx_cmd = npx_cmd or self._find_npx()
        self.npm_cache_dir = npm_cache_dir

    @staticmethod
    def _find_npx() -> str:
        """探测可执行的 npx。Windows 上 `npx`（无扩展名）是 npm 提供的 POSIX 脚本，
        直接 spawn 会被 OS 拒绝，因此优先 npx.cmd / npx.exe / npx.bat。"""
        import shutil

        for cand in ("npx.cmd", "npx.exe", "npx", "npx.bat"):
            found = shutil.which(cand)
            if found:
                return found
        return "npx"  # 实在找不到就让上层 FileNotFoundError 暴露

    def is_available(self) -> tuple[bool, str]:
        """探测 npx / node 是否可用。返回 (是否可用, 说明)。"""
        try:
            res = subprocess.run(
                [self.npx_cmd, "--version"],
                capture_output=True,
                timeout=15,
            )
            out = res.stdout.decode("utf-8", errors="replace").strip() if res.stdout else ""
            err = res.stderr.decode("utf-8", errors="replace").strip() if res.stderr else ""
            if res.returncode == 0:
                return True, f"npx 可用: {out} (cmd={self.npx_cmd})"
            return False, f"npx 返回非零: {err or out}"
        except FileNotFoundError:
            return False, f"未找到 {self.npx_cmd}（请安装 Node.js）"
        except subprocess.TimeoutExpired:
            return False, "npx --version 超时"
        except OSError as exc:
            return False, f"npx 调用失败: {exc}"

    def render(
        self,
        composition: Mapping[str, object],
        data: Mapping[str, object],
        out: Path,
    ) -> Path:
        """渲染 HyperFrames MP4 到 `out`。

        composition: 至少 width / height / duration / fps。
        data: 模板需要的占位符。
        out: 输出 .mp4 路径。
        """
        width = int(composition.get("width", 1920))
        height = int(composition.get("height", 1080))
        duration = float(composition.get("duration", 5.0))
        fps = int(composition.get("fps", self.DEFAULTS["fps"]))
        quality = str(data.get("quality", composition.get("quality", self.DEFAULTS["quality"])))
        timeout_s = int(data.get("timeout_s", self.DEFAULTS["timeout_s"]))

        template_path = self._resolve_template(data.get("template"))
        if template_path is None:
            raise ValueError(
                "HyperFramesProvider.render: data['template'] 必须指定模板名 "
                "(title-card / subtitle-bar / lower-third / end-card / transition)"
            )

        out = Path(out)
        if out.suffix.lower() != ".mp4":
            out = out.with_suffix(".mp4")
        out.parent.mkdir(parents=True, exist_ok=True)

        # 准备一个干净的工作目录：先 init（产生 index.html 等脚手架），再覆写 index.html
        with tempfile.TemporaryDirectory(prefix="hf-overlay-") as tmp:
            workdir = Path(tmp) / "composition"
            workdir.mkdir(parents=True, exist_ok=True)
            self._init_scaffold(workdir)
            html_text = self._render_template(
                template_path,
                data=dict(data),
                width=width,
                height=height,
                duration=duration,
            )
            (workdir / "index.html").write_text(html_text, encoding="utf-8")

            cmd = [
                self.npx_cmd,
                "--yes",
                "hyperframes",
                "render",
                str(workdir),
                "-o",
                str(out),
                "--format",
                "mp4",
                "--quality",
                quality,
                "--fps",
                str(fps),
                "--quiet",
            ]
            env = os.environ.copy()
            if self.npm_cache_dir is not None:
                env["npm_config_cache"] = str(self.npm_cache_dir)
            # init 阶段会联网检测 AI skills；CI/agent 场景直接跳过。
            env.setdefault("HYPERFRAMES_SKIP_SKILLS", "1")
            # Windows 上 node 的 child process 输出可能是 GBK/CP936；强制 PYTHONIOENCODING 不是
            # 帮 node 解码，但能把我们的 stdout 收集保持 utf-8。Node 自己打印走原始字节流。
            env.setdefault("PYTHONUNBUFFERED", "1")

            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout_s,
                env=env,
            )
            # 用 errors='replace' 安全解码，Windows 上 hyperframes 把 ANSI/UTF-8 混在 stderr 里。
            stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
            stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
            if proc.returncode != 0 or not out.is_file():
                raise RuntimeError(
                    "hyperframes render 失败 "
                    f"(rc={proc.returncode})\nstdout: {stdout}\nstderr: {stderr}"
                )

        return out

    # ---------- 内部辅助 ----------

    def _resolve_template(self, name: object) -> Path | None:
        """根据模板名定位 overlays/<name>.html。"""
        if not isinstance(name, str) or not name:
            return None
        candidate = OVERLAYS_DIR / f"{name}.html"
        if not candidate.is_file():
            raise FileNotFoundError(f"HyperFrames 模板不存在: {candidate}")
        return candidate

    def _init_scaffold(self, workdir: Path) -> None:
        """在 workdir 里跑 `hyperframes init --example blank --non-interactive`。

        超时兜底：如果 npx 在沙箱里拉不到包（CI/无网络），改为手工写最小脚手架，
        保证 render 命令能找到 hyperframes.json / package.json / index.html。
        """
        try:
            proc = subprocess.run(
                [
                    self.npx_cmd,
                    "--yes",
                    "hyperframes",
                    "init",
                    workdir.name,
                    "--example",
                    "blank",
                    "--non-interactive",
                ],
                cwd=str(workdir.parent),
                capture_output=True,
                timeout=180,
                env={**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"},
            )
            if proc.returncode == 0 and (workdir / "index.html").is_file():
                return
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # 兜底：手工写脚手架。hyperframes render 至少需要 package.json + hyperframes.json + index.html。
        (workdir / "package.json").write_text(
            json.dumps(
                {
                    "name": workdir.name,
                    "private": True,
                    "type": "module",
                    "scripts": {
                        "render": "npx --yes hyperframes@latest render",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workdir / "hyperframes.json").write_text(
            json.dumps(
                {
                    "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
                    "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
                    "paths": {
                        "blocks": "compositions",
                        "components": "compositions/components",
                        "assets": "assets",
                    },
                    "media": {"autoProxy": True},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workdir / "index.html").write_text(
            (
                "<!doctype html><html><head><meta charset='UTF-8'/>"
                "<meta name='viewport' content='width=1920, height=1080'/>"
                "<script src='https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js'></script>"
                "<style>* { margin:0; padding:0; box-sizing:border-box; }"
                "html,body { width:1920px; height:1080px; overflow:hidden; background:#000; }</style>"
                "</head><body><div id='root' data-composition-id='main'"
                " data-start='0' data-duration='5' data-width='1920' data-height='1080'></div>"
                "</body></html>"
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _render_template(
        template_path: Path,
        data: dict,
        width: int,
        height: int,
        duration: float,
    ) -> str:
        """读取模板，做两层替换：`{{key}}` 占位符 + `data-*="__KEY__"` 占位符。

        模板顶端允许用 `<!-- @meta width=... duration=... -->` 风格的注释，
        这里不强约束；只做字符串替换。
        """
        html = template_path.read_text(encoding="utf-8")
        # 先把 data-* 属性里的 __KEY__ 替换成实际字符串（避免被 {{}} 替换二次解析）。
        for key, value in data.items():
            if not isinstance(value, (str, int, float, bool)):
                continue
            token = f"__{key.upper()}__"
            html = html.replace(token, str(value))

        # 然后做 {{key}} 占位符替换（仅当 data 里对应值是基本类型）。
        def _replace_brace(m: re.Match[str]) -> str:
            key = m.group(1).strip()
            value = data.get(key, "")
            if isinstance(value, (str, int, float, bool)):
                return str(value)
            return m.group(0)

        html = re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", _replace_brace, html)

        # 把 width / height / duration 三个 meta 数据同步进根容器（如果根上有占位符）。
        html = html.replace("__WIDTH__", str(width))
        html = html.replace("__HEIGHT__", str(height))
        html = html.replace("__DURATION__", _format_duration(duration))
        return html


def _format_duration(seconds: float) -> str:
    """把秒数格式化成 GSAP / data-duration 能吃的字符串。"""
    if abs(seconds - round(seconds)) < 1e-6:
        return str(int(round(seconds)))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# RemotionProvider：占位
# ---------------------------------------------------------------------------


class RemotionProvider:
    """Remotion provider 占位。Remotion 是 React 生态的视频框架，目前未对接。

    预留接口：`render` 抛 NotImplementedError，供后续按数据驱动视频接入。
    """

    id = "remotion"

    def render(
        self,
        composition: Mapping[str, object],
        data: Mapping[str, object],
        out: Path,
    ) -> Path:
        raise NotImplementedError(
            "RemotionProvider 尚未实现：将来对接数据驱动视频时再补（需 Node.js + Remotion 项目脚手架）"
        )


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def get_provider(engine: str) -> OverlayProvider:
    """按 engine 名返回对应 provider 实例。"""
    engine = (engine or "").lower()
    if engine == "pil":
        return PilProvider()
    if engine == "hyperframes":
        return HyperFramesProvider()
    if engine == "remotion":
        return RemotionProvider()
    raise ValueError(f"未知的 overlay engine: {engine!r}（可选: pil / hyperframes / remotion）")


__all__ = [
    "OverlayProvider",
    "PilProvider",
    "HyperFramesProvider",
    "RemotionProvider",
    "get_provider",
    "OVERLAYS_DIR",
    "DEFAULT_OUTPUT_DIR",
    "REPO_ROOT",
]


if __name__ == "__main__":
    # CLI 自检：python scripts/overlay.py pil "示例字幕" out.png
    if len(sys.argv) < 4:
        sys.stderr.write(
            "用法: python scripts/overlay.py <engine> <text|template> <out>\n"
            "  pil <text> <out.png>\n"
            "  hyperframes <template> <out.mp4>\n"
        )
        sys.exit(2)
    engine, payload, out_str = sys.argv[1], sys.argv[2], sys.argv[3]
    provider = get_provider(engine)
    out_path = Path(out_str)
    if engine == "pil":
        result = provider.render(
            composition={"width": 1920, "height": 1080},
            data={"text": payload},
            out=out_path,
        )
    elif engine == "hyperframes":
        result = provider.render(
            composition={"width": 1920, "height": 1080, "duration": 4.0, "fps": 30},
            data={"template": payload},
            out=out_path,
        )
    else:
        result = provider.render({}, {}, out_path)
    print(f"OK: {result}")