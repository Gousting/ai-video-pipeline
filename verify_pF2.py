"""Phase F2 静态校验脚本（一次性，运行后保留在仓库根用于审计）。"""
import re
from pathlib import Path

SCRIPTS = Path("scripts")
files = sorted(SCRIPTS.glob("*.py"))

print("=== [1] grep 硬编码 sk- key（应 0 matches）===")
hits = 0
for f in files:
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"sk-[A-Za-z0-9_-]{40,}", line):
            print(f"  HIT {f.name}:{i} {line.strip()[:80]}")
            hits += 1
print(f"  total = {hits}  -> {'PASS' if hits == 0 else 'FAIL'}")

print()
print("=== [2] grep 硬编码 API_URL/API_KEY/MODEL 顶层赋值（应 0 matches）===")
hits = 0
for f in files:
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if re.match(r"^(API_URL|API_KEY|MODEL)\s*=\s*[\"']", line.strip()):
            print(f"  HIT {f.name}:{i} {line.strip()[:80]}")
            hits += 1
print(f"  total = {hits}  -> {'PASS' if hits == 0 else 'FAIL'}")

print()
print("=== [3] 所有 from vlm_config import 行（应仍是 API_KEY/API_URL/MODEL 别名）===")
count = 0
for f in files:
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if "from vlm_config import" in line:
            print(f"  {f.name}:{i}  {line.strip()}")
            count += 1
print(f"  total = {count}")

print()
print("=== [4] importlib 加载 10 个 VLM 脚本（from vlm_config import 的）===")
import importlib.util
import sys

# 找出所有 from vlm_config import 的脚本
vlm_scripts = []
for f in files:
    txt = f.read_text(encoding="utf-8")
    if "from vlm_config import" in txt and "vlm_config.py" not in f.name:
        vlm_scripts.append(f)

print(f"  -> 目标脚本数: {len(vlm_scripts)}  (Phase F 锁定为 10 个)")
assert len(vlm_scripts) == 10, f"应恰好 10 个 VLM 脚本，实际 {len(vlm_scripts)}"

sys.path.insert(0, str(SCRIPTS.resolve()))
ok, fail = 0, 0
for f in vlm_scripts:
    spec = importlib.util.spec_from_file_location(f.stem, f)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        # 兼容别名必须仍指向 VLM 组
        assert mod.API_URL == "https://opencode.ai/zen/go/v1/chat/completions", \
            f"API_URL mismatch: {mod.API_URL}"
        assert mod.MODEL == "minimax-m3", f"MODEL mismatch: {mod.MODEL}"
        assert len(mod.API_KEY) == 67, f"API_KEY len={len(mod.API_KEY)}"
        print(f"  OK  {f.name:36s}  MODEL={mod.MODEL:14s}  API_KEY_LEN={len(mod.API_KEY)}  API_URL={mod.API_URL[-40:]}")
        ok += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL {f.name}: {exc}")
        fail += 1
print(f"  -> {ok} OK / {fail} FAIL")

print()
print("=== [5] 检查 vlm_config 模块自身的 VLM_*/CHAT_* 导出 ===")
import vlm_config
for name in ("VLM_API_KEY", "VLM_API_URL", "VLM_MODEL",
             "CHAT_API_KEY", "CHAT_API_URL", "CHAT_MODEL",
             "API_KEY", "API_URL", "MODEL"):
    assert hasattr(vlm_config, name), f"missing {name}"
    print(f"  {name:13s} = {getattr(vlm_config, name)[:80] if 'KEY' not in name else '*' * 8}")
# 别名等价性：API_* == VLM_*
assert vlm_config.API_KEY == vlm_config.VLM_API_KEY
assert vlm_config.API_URL == vlm_config.VLM_API_URL
assert vlm_config.MODEL == vlm_config.VLM_MODEL
print("  -> 别名等价性 OK (API_KEY == VLM_API_KEY, ...)")