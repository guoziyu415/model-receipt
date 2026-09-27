#!/usr/bin/env python3
"""Rebuild data/vulcanbench-sessions.json from Morgan Linton's redacted traces.

    git clone https://github.com/morganlinton/vulcanbench-opus55-traces
    python3 scripts/build_vulcanbench_data.py vulcanbench-opus55-traces

One row per session: effort, task, functional score, cost, cost on Opus 5.5,
cost on Opus 4.8, minutes, replies, replies by Opus 4.8, classifier stop notices.
Reply counts and scores come from manifest.csv; costs per model come from the
closing result line of each trace.
"""
import csv, glob, json, os, sys

src = sys.argv[1] if len(sys.argv) > 1 else "vulcanbench-opus55-traces"
man = {(r["effort"], r["task"]): r for r in csv.DictReader(open(os.path.join(src, "manifest.csv")))}
order = {"low": 0, "medium": 1, "high": 2, "extra-high": 3, "max": 4}
rows = []
for path in sorted(glob.glob(os.path.join(src, "traces", "*", "*.jsonl"))):
    lines = [json.loads(l) for l in open(path)]
    head, result = lines[0], lines[-1]
    r = man[(head["effort"], head["task"])]
    usage = result.get("usage_by_model", {})
    rows.append({
        "effort": head["effort"],
        "task": head["task"],
        "functional": float(r["functional"]),
        "passed": float(r["functional"]) == 1.0,
        "cost_usd": round(float(r["cost_usd"]), 2),
        "cost_opus_5_5": round(usage.get("claude-opus-5-5", {}).get("cost_usd", 0.0), 2),
        "cost_opus_4_8": round(usage.get("claude-opus-4-8", {}).get("cost_usd", 0.0), 2),
        "minutes": round(float(r["minutes"]), 1),
        "replies": int(r["replies_total"]),
        "replies_opus_4_8": int(r["replies_opus_4_8"]),
        "classifier_stop_notices": int(r["classifier_stop_notices"]),
        "fallback_category": next((x.get("api_refusal_category") for x in lines if x.get("event") == "model_refusal_fallback"), None),
    })
rows.sort(key=lambda x: (order[x["effort"]], x["task"]))
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "vulcanbench-sessions.json")
with open(out, "w") as f:
    json.dump({"source": "https://github.com/morganlinton/vulcanbench-opus55-traces", "sessions": rows}, f, indent=1)
print("wrote %d sessions to %s" % (len(rows), os.path.normpath(out)))
for e, i in sorted(order.items(), key=lambda x: x[1]):
    s = [x for x in rows if x["effort"] == e]
    spend = sum(x["cost_opus_4_8"] for x in s) / sum(x["cost_usd"] for x in s)
    print("%-10s passed %2d/23  replies by 4.8 %5.1f%%  spend on 4.8 %3.0f%%" % (e, sum(x["passed"] for x in s),
          100.0 * sum(x["replies_opus_4_8"] for x in s) / sum(x["replies"] for x in s), 100 * spend))
