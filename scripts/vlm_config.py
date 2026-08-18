#!/usr/bin/env python3
"""统一 LLM 配置入口（Phase F / Phase F2）。

设计目标：把散落在 scripts/*.py 里的 VLM / Chat API_KEY / API_URL / MODEL
集中到项目根 .env 一处。换 key 或换模型只需改 .env，不动任何脚本。

分组约定（Phase F2 引入）：
  - VLM_*  : 视觉审查用（filmstrip / 帧图 / 视频片段 → 多模态 chat completion）
  - CHAT_* : 纯文本对话用（预留：未来可能用更强的推理模型或独立 key）

读取优先级（从高到低，以分组前缀为变量名前缀）：
  1. 进程环境变量 VLM_API_KEY / VLM_API_URL / VLM_MODEL / CHAT_API_KEY / ...
  2. 项目根 .env（D:\\ai-video-pipeline/.env，相对本文件 ../../.env）
  3. 兜底默认值（仅供本地 self-test，禁止依赖）

公开常量（Phase F 兼容层 + Phase F2 分组层）：
  VLM_API_KEY / VLM_API_URL / VLM_MODEL    ← 新分组（视觉）
  CHAT_API_KEY / CHAT_API_URL / CHAT_MODEL  ← 新分组（文本，预留）
  API_KEY / API_URL / MODEL                ← 兼容别名（= VLM 组，等价）

兼容策略：所有现存脚本 `from vlm_config import API_KEY, API_URL, MODEL`
无需任何改动（10 个 scripts/*.py 已经全部走兼容别名）。后续新增脚本可按
用途选择 VLM_* 或 CHAT_*。

无第三方依赖：手写 .env 解析（按行 strip + split('=', 1)，忽略注释 / 空行 / 引号）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径：项目根 = 本文件父目录的父目录
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_ENV_PATH = _REPO_ROOT / ".env"

# ---------------------------------------------------------------------------
# 兜底默认值（仅在 .env 缺失且环境变量也缺失时使用；正常情况下应被 .env 覆盖）
# ---------------------------------------------------------------------------
_DEFAULT_API_KEY = ""
_DEFAULT_API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
_DEFAULT_MODEL = "minimax-m3"


def _strip_quotes(val: str) -> str:
    """去掉 value 两侧成对包裹的单引号或双引号（容忍但不强求）。"""
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _parse_env_file(path: Path) -> dict[str, str]:
    """手写 .env 解析（stdlib only）。

    - 支持 KEY=VALUE（VALUE 可选 ' 或 " 包裹）
    - 忽略空行与 # 开头注释
    - KEY 重名时后者覆盖前者（与 dotenv 行为一致）
    - 自动去除 UTF-8 BOM（Windows 记事本/save 经常带 BOM）
    - 解析失败不抛错，仅不返回该行（确保脚本启动不被一个格式瑕疵拖垮）
    """
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return out
    except OSError as exc:  # noqa: BLE001
        print(f"[vlm_config] WARN: read {path} failed: {exc}", file=sys.stderr)
        return out
    # 去除 UTF-8 BOM
    text = text.lstrip("\ufeff")

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            print(
                f"[vlm_config] WARN: skip malformed line {lineno}: {raw!r}",
                file=sys.stderr,
            )
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = _strip_quotes(val)
    return out


def _resolve(name: str, default: str, file_values: dict[str, str]) -> str:
    """三层兜底：env var > .env file > default。"""
    val = os.environ.get(name)
    if val is not None and val != "":
        return val
    val = file_values.get(name)
    if val is not None and val != "":
        return val
    return default


_file_values = _parse_env_file(_ENV_PATH)
_env_file_exists = _ENV_PATH.is_file()

# ---------------------------------------------------------------------------
# VLM 组：视觉审查（Phase F2 显式前缀）
# ---------------------------------------------------------------------------
VLM_API_KEY = _resolve("VLM_API_KEY", _DEFAULT_API_KEY, _file_values)
VLM_API_URL = _resolve("VLM_API_URL", _DEFAULT_API_URL, _file_values)
VLM_MODEL = _resolve("VLM_MODEL", _DEFAULT_MODEL, _file_values)

# ---------------------------------------------------------------------------
# CHAT 组：纯文本对话（Phase F2 新增，预留）
# ---------------------------------------------------------------------------
CHAT_API_KEY = _resolve("CHAT_API_KEY", _DEFAULT_API_KEY, _file_values)
CHAT_API_URL = _resolve("CHAT_API_URL", _DEFAULT_API_URL, _file_values)
CHAT_MODEL = _resolve("CHAT_MODEL", _DEFAULT_MODEL, _file_values)

# ---------------------------------------------------------------------------
# 兼容别名（Phase F 留下的 API_KEY / API_URL / MODEL → VLM 组）
# 保留理由：10 个 scripts/*.py 已经 `from vlm_config import API_KEY, API_URL, MODEL`，
# 改名会触发 10 处 import 改动；保留别名则零改动。
# ---------------------------------------------------------------------------
API_KEY = VLM_API_KEY
API_URL = VLM_API_URL
MODEL = VLM_MODEL


def _mask_key(k: str) -> str:
    """API_KEY 显示脱敏：保留前 6 位 + 后 2 位，中间用 * 替换。"""
    if not k:
        return "<empty>"
    if len(k) <= 8:
        return k[:2] + "***" + k[-2:]
    return k[:6] + "*" * (len(k) - 8) + k[-2:]


# ---------------------------------------------------------------------------
# Self-test：`python scripts/vlm_config.py` 直接执行，验证 .env 可读
# ---------------------------------------------------------------------------
def _self_test() -> int:
    print(f"[vlm_config] repo_root    = {_REPO_ROOT}")
    print(f"[vlm_config] env_path     = {_ENV_PATH}  (exists={_env_file_exists})")
    print()
    print("[vlm_config] --- VLM 组（视觉审查）---")
    print(f"[vlm_config] VLM_API_KEY  = {_mask_key(VLM_API_KEY)}  (len={len(VLM_API_KEY)})")
    print(f"[vlm_config] VLM_API_URL  = {VLM_API_URL}")
    print(f"[vlm_config] VLM_MODEL    = {VLM_MODEL}")
    print()
    print("[vlm_config] --- CHAT 组（文本对话，预留）---")
    print(f"[vlm_config] CHAT_API_KEY = {_mask_key(CHAT_API_KEY)}  (len={len(CHAT_API_KEY)})")
    print(f"[vlm_config] CHAT_API_URL = {CHAT_API_URL}")
    print(f"[vlm_config] CHAT_MODEL   = {CHAT_MODEL}")
    print()
    print("[vlm_config] --- 兼容别名（= VLM 组；10 个现存脚本 import 用）---")
    print(f"[vlm_config] API_KEY      = {_mask_key(API_KEY)}  (len={len(API_KEY)})")
    print(f"[vlm_config] API_URL      = {API_URL}")
    print(f"[vlm_config] MODEL        = {MODEL}")
    if not _env_file_exists:
        print(
            "[vlm_config] WARN: .env not found, falling back to env vars / defaults.",
            file=sys.stderr,
        )
    if not VLM_API_KEY or not CHAT_API_KEY:
        print(
            "[vlm_config] ERROR: VLM_API_KEY / CHAT_API_KEY 至少有一组为空，"
            "对应分组调用将失败。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())