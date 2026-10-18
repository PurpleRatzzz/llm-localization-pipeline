"""Translation memory (TM): approved translations of similar segments.

A CAT tool or TMS gives the translator two kinds of TM help, and so does this
module. The TM is the train split (~98k professionally translated segments).

- Fuzzy matches (lookup): earlier segments similar to the new one as a whole.
  Only 80 of the 200 golden segments have one at 75%+.
- Concordance (concordance): earlier segments that contain a specific phrase
  of the new one ("fiscal year", "approver's delegate"), however different the
  rest is. This shows Salesforce's established wording for every segment.

The golden set has no train segment at 95% similarity or more (excluded in
scripts/prepare_data.py), so every match is a fuzzy match, never the answer.
"""

import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

TM_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "train.jsonl"

# 75% is the usual lower bound for a fuzzy match in CAT tools: below it the
# old translation rarely saves work and can mislead.
MIN_SCORE = 75
MAX_MATCHES = 2

TAG_RE = re.compile(r"<[^>]+>")

# Concordance settings.
MAX_EXAMPLES = 4        # per segment: enough to show the key terms, short enough to read
MAX_PHRASE_DF = 3000    # a phrase found in more TM entries than this is everyday language
WORD_RE = re.compile(r"[a-z][a-z'-]*[a-z]|[a-z]")
# Words that cannot start or end a searched phrase (also used by
# scripts/extract_terms.py, so both agree on what a phrase is).
STOPWORDS = set("""
a an the and or but if then else of to in on at by for from with without into onto over under
about as is are was were be been being it its this that these those there here which who whom
whose what when where why how not no can cannot could should would will shall may might must do
does did done you your yours we our they their them he she his her i me my also only just than so
such each every all any some more most other another same own both either neither one two three
first new see use using used set have has had want before after next
""".split())


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
        # Word -> positions in self.keys of the entries containing it, so a
        # phrase search only checks entries that have all of its words.
        self.index = defaultdict(set)
        for n, key in enumerate(self.keys):
            for word in set(WORD_RE.findall(key)):
                self.index[word].add(n)

    def lookup(self, source: str, limit: int = MAX_MATCHES,
               min_score: int = MIN_SCORE) -> list[dict]:
        """The best fuzzy matches: [{"score", "source", "target"}], best first."""
        matches = process.extract(normalize(source), self.keys, scorer=fuzz.ratio,
                                  limit=limit, score_cutoff=min_score)
        return [{"score": round(score), "source": self.entries[key][0],
                 "target": self.entries[key][1]}
                for key, score, _ in matches]

    def _containing(self, phrase: str) -> list[int]:
        """Positions of the entries that contain `phrase` as whole words."""
        candidates = set.intersection(*(self.index.get(w, set()) for w in phrase.split()))
        if " " not in phrase:
            return sorted(candidates)       # the index is already whole-word
        pattern = re.compile(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])")
        return sorted(n for n in candidates if pattern.search(self.keys[n]))

    def concordance(self, source: str, max_examples: int = MAX_EXAMPLES) -> list[dict]:
        """Approved translations of the most specific phrases in `source`:
        [{"phrase", "source", "target"}].

        Phrases are 1-3 words that do not start or end with a stopword. The
        most specific go first: longer phrases, then rarer ones. Each chosen
        phrase brings the shortest TM entry containing it (the most focused
        example); a phrase whose words are already covered is skipped.
        """
        query = normalize(source)
        words = WORD_RE.findall(query)
        phrases = {}
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS or any(len(w) < 3 for w in gram):
                    continue
                phrases.setdefault(" ".join(gram), set(range(i, i + n)))
        ranked = []
        for phrase, positions in phrases.items():
            hits = self._containing(phrase)
            if 1 <= len(hits) <= MAX_PHRASE_DF:
                ranked.append((-len(positions), len(hits), phrase, positions, hits))
        ranked.sort(key=lambda r: r[:3])

        examples, covered, used = [], set(), set()
        for _, _, phrase, positions, hits in ranked:
            if positions <= covered:
                continue
            # Shortest entry first; never the segment itself or a near-copy.
            for n in sorted(hits, key=lambda n: (len(self.keys[n]), self.keys[n])):
                if n not in used and fuzz.ratio(query, self.keys[n]) < 95:
                    key = self.keys[n]
                    examples.append({"phrase": phrase, "source": self.entries[key][0],
                                     "target": self.entries[key][1]})
                    covered |= positions
                    used.add(n)
                    break
            if len(examples) == max_examples:
                break
        return examples


@lru_cache(maxsize=1)
def default_tm() -> TranslationMemory:
    """The train split as TM, loaded once per process (~2 seconds)."""
    if not TM_PATH.exists():
        raise FileNotFoundError(f"{TM_PATH} not found. Build the translation memory first: "
                                ".venv/bin/python -I scripts/prepare_data.py")
    with open(TM_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return TranslationMemory([(row["source"], row["reference"]) for row in rows])
