"""Score the human review sheet (reviews/review_sheet.zh-CN.csv).

Unblinds part 2 with reviews/review_key.json and writes reports/human_review.md.

Conventions agreed with the reviewer:
- ratings are ok / minor / major (case-insensitive);
- in part 2, a blank rating means ok (the reviewer only marked problems);
- "better" is X / Y / same.

Run from the repo root:
    .venv/bin/python -I scripts/score_review.py
"""

import csv
import json
import math
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET = REPO_ROOT / "reviews" / "review_sheet.zh-CN.csv"
KEY = REPO_ROOT / "reviews" / "review_key.json"
REPORT = REPO_ROOT / "reports" / "human_review.md"
RATINGS = ("ok", "minor", "major")


def rating(value: str) -> str:
    value = value.strip().lower() or "ok"   # blank = ok (agreed convention)
    if value not in RATINGS:
        raise ValueError(f"Unknown rating {value!r}")
    return value


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test (ties dropped): how likely a split at least
    this uneven is if both configurations were equally good."""
    n = wins + losses
    k = max(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main():
    with open(SHEET, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    key = json.loads(KEY.read_text(encoding="utf-8"))

    routed = [r for r in rows if r["part"].startswith("1")]
    routed_ratings = Counter(rating(r["X_rating"]) for r in routed)

    pairs = [r for r in rows if r["part"].startswith("2")]
    configs = sorted({key[r["item"]]["X"] for r in pairs} | {key[r["item"]]["Y"] for r in pairs})
    preference = Counter()
    position = Counter()   # was the left (X) or right (Y) column preferred?
    ratings = {c: Counter() for c in configs}
    for r in pairs:
        k = key[r["item"]]
        better = r["better"].strip().lower()
        position[better] += 1
        preference[{"x": k["X"], "y": k["Y"]}.get(better, "same")] += 1
        ratings[k["X"]][rating(r["X_rating"])] += 1
        ratings[k["Y"]][rating(r["Y_rating"])] += 1

    a, b = configs
    out = ["# Human review", "",
           "Reviewer: Yanlin Chen (zh-CN). Sheet: `reviews/review_sheet.zh-CN.csv`, "
           "built by `scripts/make_review_sheet.py`; scored by `scripts/score_review.py`.", "",
           "## Part 1: segments the pipeline routed to review", "",
           f"{len(routed)} routed segments (configs B-E). Was the reason a real problem?", "",
           "| Rating | Segments |", "|---|---:|"]
    out += [f"| {r} | {routed_ratings[r]} |" for r in RATINGS]
    real = routed_ratings["minor"] + routed_ratings["major"]
    out += ["", f"Routing precision: {real} of {len(routed)} routed segments had a real problem.", "",
            "## Part 2: blind comparison of auto-approved segments", "",
            f"{len(pairs)} random segments that both {a} and {b} auto-approved, shown as X/Y in "
            "random order without the configuration names or the reference.", "",
            "| Preferred | Segments |", "|---|---:|"]
    out += [f"| {c} | {preference[c]} |" for c in (a, b, "same")]
    x_first = sum(key[r["item"]]["X"] == a for r in pairs)
    out += ["", f"Sign test ({a} vs {b}, ties dropped): two-sided p = "
            f"{sign_test(preference[a], preference[b]):.2f}.", "",
            f"Position check: the left column (X) was preferred {position['x']} times and the "
            f"right column (Y) {position['y']} times (sign test p = "
            f"{sign_test(position['x'], position['y']):.2f}). X was {a} in {x_first} pairs and "
            f"{b} in {len(pairs) - x_first}, assigned at random, so a preference for the left "
            "column adds noise but favours neither configuration.", "",
            "| Config | ok | minor | major | Auto-approved with a problem |",
            "|---|---:|---:|---:|---:|"]
    for c in configs:
        n = sum(ratings[c].values())
        bad = ratings[c]["minor"] + ratings[c]["major"]
        out.append(f"| {c} | {ratings[c]['ok']} | {ratings[c]['minor']} | {ratings[c]['major']} | "
                   f"{bad}/{n} ({bad / n:.0%}) |")
    out += ["", "Blank ratings in part 2 count as ok (the reviewer marked only problems). "
            "With 30 segments, these are indicative counts, not precise rates."]
    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
