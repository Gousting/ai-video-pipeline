"""Verify cut_point_review Phase F2 output: vlm_unavailable should be False."""
import json
from pathlib import Path

data = json.load(open(r"output\cut_point_review_phase_f2.json", encoding="utf-8"))
print("Top-level keys:", list(data.keys()))
print()
print("cut_points count:", len(data["cut_points"]))
for i, cp in enumerate(data["cut_points"], 1):
    vlm = cp.get("vlm", {})
    print(f"  cut_point[{i}] id={cp.get('id')} "
          f"vlm_unavailable={vlm.get('vlm_unavailable')} "
          f"score={vlm.get('score')} "
          f"visual_jump={vlm.get('visual_jump')} "
          f"subtitle={vlm.get('subtitle_occlusion')} "
          f"text_clear={vlm.get('text_clear')}")
print()
print("meta.model =", data.get("meta", {}).get("model"))
print("generated_at =", data.get("generated_at"))
print()
unavail = sum(1 for cp in data["cut_points"] if cp.get("vlm", {}).get("vlm_unavailable"))
total = len(data["cut_points"])
status = "PASS (all VLM available)" if unavail == 0 else f"FAIL ({unavail}/{total} unavailable)"
print(f"VLM unavailable: {unavail}/{total}  -> {status}")