"""Record which golden segments get a TM fuzzy match in configurations D-F.

Writes data/golden_tm_coverage.json, which scripts/evaluate.py reads to split
chrF by TM coverage: is E's gain only from old sentences that happen to be in
the TM, or does it hold for new content too?

It uses exactly the lookup the pipeline uses (l10n_pipeline.tm, raw score >=
MIN_SCORE), so the split matches what the model saw. (data/golden.jsonl's
max_train_sim is rounded, which moves 2 borderline segments.) The small
committed file lets evaluate.py run without the raw TM data, as in CI.

Run from the repo root:
    .venv/bin/python -I scripts/tm_coverage.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))   # `python -I` does not add the repo root
from l10n_pipeline import tm  # noqa: E402

GOLDEN = REPO_ROOT / "data" / "golden.jsonl"
OUT = REPO_ROOT / "data" / "golden_tm_coverage.json"


def main():
    rows = [json.loads(line) for line in GOLDEN.open(encoding="utf-8")]
    memory = tm.default_tm()
    with_match = sorted(row["id"] for row in rows if memory.lookup(row["source"]))
    OUT.write_text(json.dumps({"min_score": tm.MIN_SCORE, "segments": len(rows),
                               "with_match": with_match}, indent=1) + "\n", encoding="utf-8")
    print(f"{len(with_match)} of {len(rows)} golden segments have a fuzzy match "
          f">= {tm.MIN_SCORE}% -> {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
