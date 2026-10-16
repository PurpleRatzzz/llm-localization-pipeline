"""Translation memory (TM): approved translations of similar segments.

A CAT tool or TMS shows the translator "fuzzy matches": earlier segments that
are similar to the new one, with their approved translations. Here the TM is
the train split (~98k professionally translated segments), and the best fuzzy
matches are given to the model as examples of Salesforce's own wording.

The golden set has no train segment at 95% similarity or more (excluded in
scripts/prepare_data.py), so every match is a fuzzy match, never the answer.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

TM_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "train.jsonl"

# 75% is the usual lower bound for a fuzzy match in CAT tools: below it the
# old translation rarely saves work and can mislead.
MIN_SCORE = 75
MAX_MATCHES = 2

TAG_RE = re.compile(r"<[^>]+>")


def normalize(text: str) -> str:
    """What similarity is computed on: no tags, case or extra spaces (the same
    normalization as the near-duplicate check in scripts/prepare_data.py)."""
    return " ".join(TAG_RE.sub("", text).lower().split())


class TranslationMemory:
    def __init__(self, pairs: list[tuple[str, str]]):
        # One entry per distinct normalized source; the first translation wins.
        self.entries = {}
        for source, target in pairs:
            self.entries.setdefault(normalize(source), (source, target))
        self.keys = list(self.entries)

    def lookup(self, source: str, limit: int = MAX_MATCHES,
               min_score: int = MIN_SCORE) -> list[dict]:
        """The best fuzzy matches: [{"score", "source", "target"}], best first."""
        matches = process.extract(normalize(source), self.keys, scorer=fuzz.ratio,
                                  limit=limit, score_cutoff=min_score)
        return [{"score": round(score), "source": self.entries[key][0],
                 "target": self.entries[key][1]}
                for key, score, _ in matches]


@lru_cache(maxsize=1)
def default_tm() -> TranslationMemory:
    """The train split as TM, loaded once per process (~2 seconds)."""
    with open(TM_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return TranslationMemory([(row["source"], row["reference"]) for row in rows])
