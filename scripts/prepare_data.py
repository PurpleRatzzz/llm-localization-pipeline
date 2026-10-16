"""Prepare the EN->ZH evaluation data (task T1).

Steps:
1. Download the raw dataset files that are missing from data/raw/.
2. Write every aligned train pair to data/raw/train.jsonl (used later for
   terminology extraction).
3. Score each dev segment against train to find near-duplicates.
4. Sample two disjoint, stratified sets from dev: golden (200) and tune (100).

Run from the repo root:
    .venv/bin/python -I scripts/prepare_data.py
"""

import json
import random
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz, process

# --- Paths and source -------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
BASE_URL = "https://raw.githubusercontent.com/salesforce/localization-xml-mt/master/data/enzh/"
RAW_FILES = ["enzh_en_train.json", "enzh_zh_train.json", "enzh_en_dev.json", "enzh_zh_dev.json"]

# --- Sampling settings --------------------------------------------------------

SEED = 42
# A dev segment this similar to a train segment would be a translation-memory
# match in real life, not an MT job, so it does not belong in the test set.
NEAR_DUP_THRESHOLD = 95

# Tags are only ~26% of dev, but they are where pipelines break, so we
# oversample them to half of each set.
GOLDEN_TAGGED = 100
GOLDEN_UNTAGGED = 100
GOLDEN_NESTED_QUOTA = 20  # nested tags are the hardest case; make sure we see enough
TUNE_TAGGED = 50
TUNE_UNTAGGED = 50

SHORT_MAX_WORDS = 5    # 1-5 words: UI labels, titles
MEDIUM_MAX_WORDS = 25  # 6-25 words: a typical sentence; 26+ is "long"

# In this dataset a literal "<" in text is always escaped as "&lt;", so every
# raw "<...>" is a tag.
TAG_RE = re.compile(r"<[^>]+>")


# --- Loading and writing ----------------------------------------------------

def download_missing():
    """Download only the files we don't have, so a clean checkout can rebuild
    everything while existing files are never overwritten."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name in RAW_FILES:
        path = RAW_DIR / name
        if path.exists():
            continue
        print(f"Downloading {name} ...")
        # Download to a temporary name first, so an interrupted download
        # never leaves a half-written file that looks complete.
        tmp_path = path.with_suffix(".part")
        urllib.request.urlretrieve(BASE_URL + name, tmp_path)
        tmp_path.rename(path)


def load_pairs(split):
    """Join the en and zh files of one split on their shared segment IDs."""
    en = json.loads((RAW_DIR / f"enzh_en_{split}.json").read_text(encoding="utf-8"))["text"]
    zh = json.loads((RAW_DIR / f"enzh_zh_{split}.json").read_text(encoding="utf-8"))["text"]
    unmatched = en.keys() ^ zh.keys()  # IDs present in only one of the two files
    if unmatched:
        print(f"WARNING: {len(unmatched)} {split} IDs have no partner and are skipped")
    # IDs are zero-padded, so sorting the strings also sorts them numerically.
    return [
        {"id": seg_id, "source": en[seg_id], "reference": zh[seg_id]}
        for seg_id in sorted(en.keys() & zh.keys())
    ]


def write_jsonl(path, rows):
    # ensure_ascii=False keeps Chinese readable in the file and in git diffs.
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# --- Segment features ---------------------------------------------------------

def strip_tags(text):
    # Replace with "" (not a space) so "<ph>account</ph>s" stays one word.
    return TAG_RE.sub("", text)


def normalize(text):
    """Text used only for similarity: without tags, case or spacing, so that
    markup differences don't hide a real duplicate."""
    return " ".join(strip_tags(text).lower().split())


def tag_structure(text):
    """Return (has_tags, nested) from real XML parsing, or None if the text is
    not well-formed. Parsing is stricter than a regex: it also catches
    unclosed tags and bad entities."""
    try:
        root = ET.fromstring(f"<root>{text}</root>")
    except ET.ParseError:
        return None
    elements = list(root.iter())[1:]  # every tag element except our wrapper
    has_tags = len(elements) > 0
    nested = any(len(el) > 0 for el in elements)  # len(el) = number of child elements
    return has_tags, nested


def length_bucket(n_words):
    if n_words <= SHORT_MAX_WORDS:
        return "short"
    if n_words <= MEDIUM_MAX_WORDS:
        return "medium"
    return "long"


# --- Near-duplicate check -----------------------------------------------------

def max_train_similarity(dev, train):
    """For each dev segment, the best fuzz.ratio score (0-100) against any train source."""
    # Train repeats some sources; scoring each distinct text once is enough.
    choices = sorted({normalize(pair["source"]) for pair in train})
    scores = []
    for pair in dev:
        # extractOne raises its internal cutoff to the best score found so far,
        # so it can quickly skip candidates that can no longer win (e.g. ones
        # far too short or too long).
        _, score, _ = process.extractOne(normalize(pair["source"]), choices, scorer=fuzz.ratio)
        scores.append(score)
    return scores


# --- Filtering and sampling ---------------------------------------------------

def exclusion_reason(pair, max_train_sim):
    """Why a dev pair can't be sampled, or None if it is eligible."""
    if not pair["source"].strip() or not pair["reference"].strip():
        return "empty"
    # Later QA gates parse both sides, so both must be well-formed XML.
    if tag_structure(pair["source"]) is None or tag_structure(pair["reference"]) is None:
        return "unparseable"
    if max_train_sim >= NEAR_DUP_THRESHOLD:
        return "near_duplicate"
    return None


def make_record(pair, max_train_sim):
    has_tags, nested = tag_structure(pair["source"])
    n_words = len(strip_tags(pair["source"]).split())
    return {
        "id": pair["id"],
        "source": pair["source"],        # kept exactly as in the dataset
        "reference": pair["reference"],
        "has_tags": has_tags,
        "nested": nested,
        "n_words": n_words,
        "length_bucket": length_bucket(n_words),
        "max_train_sim": max_train_sim,
    }


def sample_sets(pool, rng):
    """Draw golden first, then tune from what is left, so the two never overlap."""
    tagged = [r for r in pool if r["has_tags"]]
    untagged = [r for r in pool if not r["has_tags"]]
    nested = [r for r in tagged if r["nested"]]
    flat = [r for r in tagged if not r["nested"]]

    n_nested = min(GOLDEN_NESTED_QUOTA, len(nested))
    golden = (
        rng.sample(nested, n_nested)
        + rng.sample(flat, GOLDEN_TAGGED - n_nested)
        + rng.sample(untagged, GOLDEN_UNTAGGED)
    )

    golden_ids = {r["id"] for r in golden}
    rest_tagged = [r for r in tagged if r["id"] not in golden_ids]
    rest_untagged = [r for r in untagged if r["id"] not in golden_ids]
    # Tune keeps the natural nested/flat mix of what is left; no quota needed
    # because it is only used to tune prompts, not to report scores.
    tune = rng.sample(rest_tagged, TUNE_TAGGED) + rng.sample(rest_untagged, TUNE_UNTAGGED)

    # Sorting by id gives stable files and clean diffs between runs.
    return sorted(golden, key=lambda r: r["id"]), sorted(tune, key=lambda r: r["id"])


# --- Reporting ----------------------------------------------------------------

def describe(name, rows):
    tagged = sum(r["has_tags"] for r in rows)
    nested = sum(r["nested"] for r in rows)
    buckets = Counter(r["length_bucket"] for r in rows)
    print(
        f"  {name:<8} {len(rows):>4} segments | tagged {tagged} (nested {nested}), "
        f"untagged {len(rows) - tagged} | short {buckets['short']}, "
        f"medium {buckets['medium']}, long {buckets['long']}"
    )


def main():
    download_missing()

    train = load_pairs("train")
    write_jsonl(RAW_DIR / "train.jsonl", train)
    dev = load_pairs("dev")

    start = time.perf_counter()
    raw_scores = max_train_similarity(dev, train)
    near_dup_seconds = time.perf_counter() - start

    pool = []
    excluded = Counter()
    for pair, raw_score in zip(dev, raw_scores):
        # Filter on the same rounded number we store, so anyone can re-check
        # the rule from the output files alone.
        sim = round(raw_score)
        reason = exclusion_reason(pair, sim)
        if reason:
            excluded[reason] += 1
        else:
            pool.append(make_record(pair, sim))

    golden, tune = sample_sets(pool, random.Random(SEED))
    assert not {r["id"] for r in golden} & {r["id"] for r in tune}, "golden and tune overlap"
    write_jsonl(DATA_DIR / "golden.jsonl", golden)
    write_jsonl(DATA_DIR / "tune.jsonl", tune)

    # Natural tag rate of the whole dev split, for context only.
    dev_tagged = sum(bool(TAG_RE.search(p["source"])) for p in dev)
    print("\n=== Summary ===")
    print(f"Train pairs written to data/raw/train.jsonl: {len(train)}")
    print(f"Dev pairs: {len(dev)} (with tags: {dev_tagged}, {dev_tagged / len(dev):.1%})")
    print(
        f"Near-duplicate check: {near_dup_seconds:.1f}s | "
        f">= {NEAR_DUP_THRESHOLD}: {sum(round(s) >= NEAR_DUP_THRESHOLD for s in raw_scores)}, "
        f"exact (100): {sum(s == 100 for s in raw_scores)}"
    )
    print(
        f"Excluded from dev: empty {excluded['empty']}, unparseable {excluded['unparseable']}, "
        f"near-duplicate {excluded['near_duplicate']}"
    )
    describe("pool", pool)
    describe("golden", golden)
    describe("tune", tune)


if __name__ == "__main__":
    main()
