"""Build the human review sheet for the golden runs (blind where it matters).

Part 1, routed: every segment that config B, C, D or E routed to human review.
  The reviewer judges whether the reason is a real problem. This measures how
  precise the routing is (does it waste reviewer time?).

Part 2, blind pair: 30 random segments that C and E both auto-approved.
  The two translations are shown as X and Y in random order, without saying
  which configuration produced which, and without the reference translation.
  The reviewer picks the better one and rates each as ok / minor / major.
  This measures what auto-approval lets through (the miss rate) and whether
  C's edit + verify stages (3x the cost of B) beat E's translation-memory
  context (best chrF, half the cost of C).

The X/Y key is written to a separate file so the review stays blind.

Run from the repo root:
    .venv/bin/python -I scripts/make_review_sheet.py
"""

import csv
import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS = REPO_ROOT / "runs"
OUT_DIR = REPO_ROOT / "reviews"
SHEET = OUT_DIR / "review_sheet.zh-CN.csv"
KEY = OUT_DIR / "review_key.json"
SEED = 7
N_PAIRS = 30
ROUTED_CONFIGS = "BCDE"
PAIR = ("C", "E")


def load(config):
    with open(RUNS / config / "segments.jsonl", encoding="utf-8") as f:
        return {row["id"]: row for row in (json.loads(line) for line in f)}


def main():
    runs = {config: load(config) for config in ROUTED_CONFIGS}
    rng = random.Random(SEED)
    rows, key = [], {}

    # Part 1: each routed (config, segment), with the reasons the pipeline gave.
    for config in ROUTED_CONFIGS:
        for seg in runs[config].values():
            if seg["status"] != "needs_review":
                continue
            reasons = "; ".join(f"{i['check']}: {i['message']}"
                                for i in seg["issues"] if i["severity"] == "error")
            rows.append({"part": "1 routed", "item": f"{config}-{seg['id'][-4:]}",
                         "source": seg["source"], "X": seg["target"], "Y": "",
                         "pipeline_reason": reasons, "better": "",
                         "X_rating": "", "Y_rating": "", "comment": ""})

    # Part 2: blind pairs among segments both configurations auto-approved.
    first, second = PAIR
    both = sorted(i for i in runs[first] if runs[first][i]["status"] == runs[second][i]["status"]
                  == "auto_approved")
    for n, seg_id in enumerate(sorted(rng.sample(both, N_PAIRS)), start=1):
        order = list(PAIR)
        rng.shuffle(order)   # which config is X is random per row
        item = f"P{n:02d}"
        key[item] = {"id": seg_id, "X": order[0], "Y": order[1]}
        rows.append({"part": "2 blind", "item": item, "source": runs[first][seg_id]["source"],
                     "X": runs[order[0]][seg_id]["target"], "Y": runs[order[1]][seg_id]["target"],
                     "pipeline_reason": "", "better": "", "X_rating": "", "Y_rating": "",
                     "comment": ""})

    OUT_DIR.mkdir(exist_ok=True)
    # utf-8-sig: Excel needs the byte-order mark to read the Chinese correctly.
    with open(SHEET, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    KEY.write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    print(f"{sum(r['part'] == '1 routed' for r in rows)} routed + {N_PAIRS} blind pairs "
          f"-> {SHEET.relative_to(REPO_ROOT)} (key: {KEY.relative_to(REPO_ROOT)})")


if __name__ == "__main__":
    main()
