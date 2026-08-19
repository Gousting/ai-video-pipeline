"""Verify cut_point_review Phase F2 output: vlm_unavailable should be False."""
import json
import sys

data = json.load(open(r"D:\ai-video-pipeline\output\cut_point_review.json", encoding="utf-8"))

print("cut_points count:", len(data["cut_points"]))
print("model in meta:", data.get("model"))
print()

for i, cp in enumerate(data["cut_points"], 1):
    vlm = cp.get("vlm", {})
    cid = cp.get("id")
    pos = cp.get("position")
    score = cp.get("score")
    passed = cp.get("pass")
    print(f"cut_{i}: id={cid} position={pos:.3f}s score={score} pass={passed}")
    print(f"  vlm_unavailable = {vlm.get('vlm_unavailable')}")
    print(f"  visual_jump    = {vlm.get('visual_jump')}")
    print(f"  subtitle       = {vlm.get('subtitle')}")
    print(f"  text_clear     = {vlm.get('text_clear')}")
    print(f"  vlm_score      = {vlm.get('vlm_score')}")
    rs = vlm.get("raw_response") or ""
    if rs:
        print(f"  raw_response[:120] = {rs[:120]!r}")
    print()

unavail = sum(1 for cp in data["cut_points"] if cp.get("vlm", {}).get("vlm_unavailable"))
total = len(data["cut_points"])
print(f"VLM unavailable: {unavail}/{total}  -> {'PASS' if unavail == 0 else 'FAIL'}")
print(f"overall.pass: {data.get('overall', {}).get('pass')}")
print(f"overall.notes: {data.get('overall', {}).get('notes')}")

sys.exit(0 if unavail == 0 else 1)
