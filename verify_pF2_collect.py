"""Collect pF2 verification summary for the report."""
import json
from pathlib import Path

data = json.load(open(r"output\cut_point_review_phase_f2.json", encoding="utf-8"))
print("cut_points summary:")
for cp in data["cut_points"]:
    vlm = cp["vlm"]
    cid = cp["id"]
    score = vlm["score"]
    unavail = vlm["vlm_unavailable"]
    vj = vlm["visual_jump"]
    so = vlm["subtitle_occlusion"]
    tc = vlm["text_clear"]
    print(f"  {cid}: score={score} vlm_unavailable={unavail} visual_jump={vj} subtitle={so} text_clear={tc}")
print("overall.pass =", data["overall"]["pass"])
print("overall.score =", data["overall"].get("score"))
print("model =", data["model"])
print("generated_at =", data["generated_at"])
print()
# 还要 .env 字节级校验（key 不能被改坏）
env = Path(".env").read_text(encoding="utf-8")
expected = "VLM_API_KEY=sk-VxLhB9Fqnm6XBgd4l1kjloOGq2bJ9g9sKJ2Y0SJTdLwdt6Rtd0olISu02pkmNCZr"
assert expected in env, ".env VLM_API_KEY missing or modified!"
print(".env VLM_API_KEY byte-exact match: PASS")
print(".env total lines:", len(env.splitlines()))