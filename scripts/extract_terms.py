"""Extract terminology candidates from the train set by tag alignment (task T3).

In this dataset, UI labels are wrapped in <uicontrol> and <parmname> on both
sides, e.g. "<uicontrol>Save</uicontrol>" / "<uicontrol>保存</uicontrol>".
Pairing the n-th <uicontrol> of the source with the n-th <uicontrol> of the
reference aligns English UI labels with their Chinese translations, with no
LLM and no word aligner. Counting the pairs over ~98k train segments shows,
for each English label, how consistently it was translated.

Only train data is used to find translations. The golden/tune sources are only
used to decide which terms are relevant (in production you would also build a
glossary for the content about to be translated); their references are never read.

Run from the repo root:
    .venv/bin/python -I scripts/extract_terms.py
"""

import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAIN = REPO_ROOT / "data" / "raw" / "train.jsonl"
EVAL_SETS = [REPO_ROOT / "data" / "golden.jsonl", REPO_ROOT / "data" / "tune.jsonl"]
OUT = REPO_ROOT / "context" / "glossary_candidates.zh-CN.csv"

ALIGN_TAGS = ["uicontrol", "parmname"]  # tags whose content is a translated UI label
MIN_COUNT = 3           # ignore labels seen fewer than 3 times: too little evidence
REQUIRED_SHARE = 0.9    # inside tags, one translation is used >= 90% of the time
# A label can be translated consistently inside <uicontrol> and still not be a
# term in running text ("To" is the email field 收件人, but usually just "to").
# hit_rate = share of train segments containing the English term (anywhere)
# whose reference contains the Chinese term. This is also the false-positive
# check for the QA gate: a "required" term must be honored by human translators.
REQUIRED_HIT_RATE = 0.9
MIN_HIT_RATE = 0.5      # below this the pair is not a usable term at all; drop it


def tag_texts(segment, tag):
    """Full text of every <tag> element, in document order (nested tags included)."""
    root = ET.fromstring(f"<root>{segment}</root>")
    return [" ".join("".join(el.itertext()).split()) for el in root.iter(tag)]


def singular(label):
    """Crude English singular, used only to merge "Leads" into "Lead"."""
    if label.endswith("ies"):
        return label[:-3] + "y"
    if label.endswith("s") and not label.endswith("ss"):
        return label[:-1]
    return label


def merge_plurals(counts):
    # "Lead" (2) and "Leads" (6) are one term with 8 observations. Merge only
    # when the singular also occurs as a label, so "Status" never becomes "Statu".
    merged = defaultdict(Counter)
    for label, zh_counts in counts.items():
        key = singular(label) if singular(label) in counts else label
        merged[key].update(zh_counts)
    return merged


def align_labels(train_rows):
    """Count English label -> Chinese label pairs across the train set."""
    counts = defaultdict(Counter)
    for row in train_rows:
        for tag in ALIGN_TAGS:
            en_labels = tag_texts(row["source"], tag)
            zh_labels = tag_texts(row["reference"], tag)
            # Pair by position only when both sides have the same number of this
            # tag. Tags are occasionally reordered (14 of 518 tagged dev pairs),
            # but almost never two of the same tag; rare misalignments are
            # outvoted by the majority count below.
            if len(en_labels) != len(zh_labels):
                continue
            for en, zh in zip(en_labels, zh_labels):
                if en and zh and en != zh:  # identical = untranslated name, not a term pair
                    counts[en][zh] += 1
    return merge_plurals(counts)


def term_pattern(term):
    # Whole word, singular or plural, case-sensitive: "Case" (个案) is an object
    # name, "in this case" (情况) is not. Same rule as the QA gate.
    forms = [term, term + "s", term + "es"]
    if re.search(r"[^aeiou]y$", term):
        forms.append(term[:-1] + "ies")  # Opportunity -> Opportunities
    alternatives = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


def occurrences(term, texts):
    """How many texts contain the term."""
    pattern = term_pattern(term)
    return sum(bool(pattern.search(t)) for t in texts)


def hit_rate(term, target, train_rows):
    """Share of train segments containing `term` whose reference contains `target`."""
    pattern = term_pattern(term)
    hits = [target in row["reference"] for row in train_rows if pattern.search(row["source"])]
    return sum(hits) / len(hits) if hits else 0.0


def main():
    train_rows = [json.loads(line) for line in TRAIN.open(encoding="utf-8")]
    counts = align_labels(train_rows)

    eval_sources = [json.loads(line)["source"] for path in EVAL_SETS for line in path.open(encoding="utf-8")]

    rows = []
    dropped = 0
    for en, zh_counts in counts.items():
        total = sum(zh_counts.values())
        if total < MIN_COUNT:
            continue
        in_eval = occurrences(en, eval_sources)
        if in_eval == 0:
            continue  # not relevant to the content we will translate
        (top, top_n), *others = zh_counts.most_common(4)
        share = top_n / total
        rate = hit_rate(en, top, train_rows)
        if rate < MIN_HIT_RATE:
            dropped += 1
            continue
        consistent = share >= REQUIRED_SHARE and rate >= REQUIRED_HIT_RATE
        rows.append({
            "source": en,
            "target": top,
            "share": f"{share:.2f}",
            "hit_rate": f"{rate:.2f}",
            "train_count": total,
            "eval_segments": in_eval,
            "suggested_kind": "required" if consistent else "ambiguous",
            "alternatives": "; ".join(f"{zh} ({n})" for zh, n in others),
        })

    # Most useful first: terms that appear in many eval segments.
    rows.sort(key=lambda r: (-r["eval_segments"], -r["train_count"]))
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Aligned labels: {len(counts)} distinct English labels")
    print(f"Dropped (hit_rate < {MIN_HIT_RATE}): {dropped}")
    print(f"Candidates (train_count >= {MIN_COUNT}, found in golden/tune): {len(rows)}")
    print(f"  suggested required: {sum(r['suggested_kind'] == 'required' for r in rows)}")
    print(f"  suggested ambiguous: {sum(r['suggested_kind'] == 'ambiguous' for r in rows)}")
    print(f"Written to {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
