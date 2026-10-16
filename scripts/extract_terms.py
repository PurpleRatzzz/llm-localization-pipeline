"""Extract terminology candidates from the train set (task T3).

Two glossaries, because inside and outside UI tags a word follows different
rules: the "New" button is always 新建, but "new" in a sentence is just 新.

ui    The whole text of a <uicontrol>/<parmname>: a UI string the docs must
      cite exactly. Found by TAG ALIGNMENT: the n-th <uicontrol> of a train
      source pairs with the n-th <uicontrol> of its reference, e.g.
      "<uicontrol>Save</uicontrol>" / "<uicontrol>保存</uicontrol>".
      Required if one translation dominates inside tags (tag share >= 0.9).

text  A term in running text (outside UI and code tags), e.g. "related list".
      Many such terms never appear inside a tag, so tag alignment cannot find
      them. Found by CO-OCCURRENCE: the Chinese string that best matches the
      set of train segments containing the English term (Dice coefficient).
      Required if human references use it consistently (hit rate >= 0.9).

No LLM is used. Only train data is used to find translations; the golden/tune
sources only decide which terms are relevant, and their references are never read.

Writes context/glossary_candidates.zh-CN.csv (every candidate) and
context/glossary.zh-CN.csv (the shortlist for the human reviewer).

Run from the repo root:
    .venv/bin/python -I scripts/extract_terms.py
"""

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# `python -I` does not put the repo root on sys.path; add it to import the gate.
sys.path.insert(0, str(REPO_ROOT))
from l10n_pipeline.qa_gate import UI_TAGS, parse_fragment, term_pattern, text_nodes  # noqa: E402

TRAIN = REPO_ROOT / "data" / "raw" / "train.jsonl"
EVAL_SETS = [REPO_ROOT / "data" / "golden.jsonl", REPO_ROOT / "data" / "tune.jsonl"]
CANDIDATES_OUT = REPO_ROOT / "context" / "glossary_candidates.zh-CN.csv"
REVIEW_OUT = REPO_ROOT / "context" / "glossary.zh-CN.csv"

# --- ui glossary
UI_MIN_COUNT = 3          # aligned pairs needed before trusting a label's translation
UI_REQUIRED_SHARE = 0.9   # one translation used >= 90% of the time inside tags
# Strong evidence needs no human review: >= 95% consistent over >= 10 pairs.
UI_AUTO_SHARE, UI_AUTO_COUNT = 0.95, 10

# --- text glossary
TEXT_MIN_DF = 20          # train segments containing the English term
ZH_MIN_LEN, ZH_MAX_LEN = 2, 8   # length of Chinese candidate strings ("开发人员控制台" = 7)
TEXT_REQUIRED_HIT = 0.9
# Strong evidence needs no human review: references use it 95%+ of the time,
# the alignment is clean (Dice >= 0.5) and it was seen in 30+ segments.
TEXT_AUTO_HIT, TEXT_AUTO_DICE, TEXT_AUTO_COUNT = 0.95, 0.5, 30
MIN_HIT = 0.5             # below this the pair is not a usable term; drop it
NESTED_SHARE = 0.8        # "Find box" is redundant if 80%+ of it sits inside "Quick Find box"
KEPT_ENGLISH = 0.8        # a word left in English in 80%+ of references is a product name (R2)
HEADING_CAPS = 0.6        # 60%+ capitalized words = a Title Case heading
REVIEW_MIN_EVAL = 2       # shortlist text terms that occur in 2+ golden/tune segments

# Words that cannot start or end a term ("the related list" -> "related list").
STOPWORDS = set("""
a an the and or but if then else of to in on at by for from with without into onto over under
about as is are was were be been being it its this that these those there here which who whom
whose what when where why how not no can cannot could should would will shall may might must do
does did done you your yours we our they their them he she his her i me my also only just than so
such each every all any some more most other another same own both either neither one two three
first new see use using used set have has had want before after next
""".split())
# Salesforce standard object names are core terms even when written in
# lowercase ("create a case") or seen only in headings, so they skip the
# termhood filter below. The hit rate still decides required vs ambiguous:
# lowercase "case" can also mean 情况.
STANDARD_OBJECTS = ["Account", "Contact", "Lead", "Opportunity", "Case", "Campaign", "Report",
                    "Dashboard", "Product", "Quote", "Contract", "Order", "Asset", "Task",
                    "Event", "Entitlement", "Solution", "Forecast"]
OBJECT_FORMS = [form for name in STANDARD_OBJECTS for form in (name, name.lower())]
OBJECT_PATTERNS = {form: re.compile(term_pattern(form)) for form in OBJECT_FORMS}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*[A-Za-z]|[A-Za-z]")
CJK_RUN_RE = re.compile(r"[㐀-䶿一-鿿]+")


# ------------------------------------------------------------------ shared

def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def running_text(segment):
    """Text outside UI and code tags: where "text" terms live."""
    root, _ = parse_fragment(segment)
    if root is None:
        return re.sub(r"<[^>]*>", " ", segment)
    return "".join(text_nodes(root, skip_protected=True, skip_ui=True))


def ui_label_texts(segment, tag):
    """Whole text of every <tag> element, in document order."""
    root = ET.fromstring(f"<root>{segment}</root>")
    return [" ".join("".join(el.itertext()).split()) for el in root.iter(tag)]


def write_csv(path, rows, fieldnames):
    # utf-8-sig writes a byte-order mark first: without it Excel reads the
    # file in the system encoding and the Chinese turns into mojibake.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------- ui glossary

def singular(label):
    """Crude English singular, used only to merge "Leads" into "Lead"."""
    if label.endswith("ies"):
        return label[:-3] + "y"
    if label.endswith("s") and not label.endswith("ss"):
        return label[:-1]
    return label


def align_ui_labels(train_rows):
    """Count English label -> Chinese label pairs across the train set."""
    counts = defaultdict(Counter)
    for row in train_rows:
        # sorted(): iterating a set of strings follows Python's per-process hash
        # randomization, which would change tie-breaking from run to run.
        for tag in sorted(UI_TAGS):
            en_labels = ui_label_texts(row["source"], tag)
            zh_labels = ui_label_texts(row["reference"], tag)
            # Pair by position only when both sides have the same number of this
            # tag. Two tags of the same kind are rarely swapped; such
            # misalignments are outvoted by the majority count.
            if len(en_labels) != len(zh_labels):
                continue
            for en, zh in zip(en_labels, zh_labels):
                if en and zh and en != zh:  # identical = untranslated name, not a term
                    counts[en][zh] += 1
    # "Lead" (2) and "Leads" (6) are one label with 8 observations. Merge only
    # when the singular also occurs as a label, so "Status" never becomes "Statu".
    merged = defaultdict(Counter)
    for label, zh_counts in counts.items():
        key = singular(label) if singular(label) in counts else label
        merged[key].update(zh_counts)
    return merged


def ui_candidates(train_rows, eval_rows):
    counts = align_ui_labels(train_rows)
    # Every UI label in the golden/tune sources, to keep only relevant entries.
    eval_labels = [{label for tag in sorted(UI_TAGS) for label in ui_label_texts(row["source"], tag)}
                   for row in eval_rows]
    rows = []
    for en, zh_counts in counts.items():
        total = sum(zh_counts.values())
        if total < UI_MIN_COUNT:
            continue
        pattern = re.compile(term_pattern(en))   # whole label, plurals allowed (same as the gate)
        in_eval = sum(any(pattern.fullmatch(label) for label in labels) for labels in eval_labels)
        if in_eval == 0:
            continue
        (top, top_n), *others = zh_counts.most_common(4)
        share = top_n / total
        auto = share >= UI_AUTO_SHARE and total >= UI_AUTO_COUNT
        rows.append({
            "scope": "ui", "source": en, "target": top,
            "kind": "required" if share >= UI_REQUIRED_SHARE else "ambiguous",
            "review": "auto" if auto else "todo",
            "tag_share": f"{share:.2f}", "hit_rate": "", "dice": "",
            "train_count": total, "eval_segments": in_eval,
            "alternatives": "; ".join(f"{zh} ({n})" for zh, n in others),
        })
    return rows


# ------------------------------------------------------------- text glossary

def english_terms(text):
    """Candidate terms in a running text: 2-3 word phrases, or one word that is
    capitalized mid-sentence (Salesforce capitalizes object and feature names:
    "Opportunity", "Chatter"), plus standard object names in any case. Other
    single lowercase words ("select", "data") are everyday vocabulary: their
    wording is for the AI verifier, not a gate rule.
    """
    terms = set()
    # In a Title Case heading ("Manage Permission Sets") every word is
    # capitalized, so a capital letter says nothing about being a name.
    long_words = [w for w in WORD_RE.findall(text) if len(w) > 3]
    heading = bool(long_words) and \
        sum(w[0].isupper() for w in long_words) >= HEADING_CAPS * len(long_words)
    for chunk in re.split(r"[^A-Za-z'\- ]+", text):   # punctuation and tags break a phrase
        words = WORD_RE.findall(chunk)
        for n in (1, 2, 3):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                if gram[0].lower() in STOPWORDS or gram[-1].lower() in STOPWORDS:
                    continue
                if any(len(w) < 3 and not w.isupper() for w in gram):
                    continue   # fragments such as "t" from "don’t" (curly apostrophe)
                if n == 1 and (heading or not (gram[0][0].isupper() and i > 0)):
                    continue
                terms.add(" ".join(gram))
    terms.update(form for form, pattern in OBJECT_PATTERNS.items() if pattern.search(text))
    return terms


def chinese_strings(text):
    """Every CJK substring of 2-8 characters."""
    out = set()
    for run in CJK_RUN_RE.findall(text):
        for n in range(ZH_MIN_LEN, ZH_MAX_LEN + 1):
            out.update(run[i:i + n] for i in range(len(run) - n + 1))
    return out


def drop_nested(postings):
    """Remove a term that almost always occurs inside a longer candidate
    ("Find box" inside "Quick Find box"): it adds nothing on its own."""
    keep = {}
    for term, segs in postings.items():
        redundant = any(
            longer != term and f" {term} " in f" {longer} "
            and len(set(postings[longer]) & set(segs)) >= NESTED_SHARE * len(segs)
            for longer in postings)
        if not redundant:
            keep[term] = segs
    return keep


def text_candidates(train_rows, eval_rows):
    eval_terms = Counter(t for row in eval_rows for t in english_terms(running_text(row["source"])))
    src_text = [running_text(row["source"]) for row in train_rows]
    ref_text = [running_text(row["reference"]) for row in train_rows]

    # Posting lists: which train segments contain each relevant English term.
    postings = defaultdict(list)
    for i, text in enumerate(src_text):
        for term in sorted(english_terms(text) & eval_terms.keys()):   # sorted: reproducible order
            postings[term].append(i)
    postings = drop_nested({t: segs for t, segs in postings.items() if len(segs) >= TEXT_MIN_DF})

    # Shortlist Chinese strings per term: those present in >= 30% of the
    # references of segments that contain the term (first 400 suffice).
    shortlist = {}
    for term, segs in postings.items():
        counts = Counter()
        for i in segs[:400]:
            counts.update(chinese_strings(ref_text[i]))
        sample = min(len(segs), 400)
        # Sort by count, then by the string itself, so ties break the same way
        # on every run (most_common would follow the random set order).
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
        shortlist[term] = [zh for zh, n in ranked if n >= 0.3 * sample]

    # How many references contain each shortlisted string (one pass over train).
    wanted = {zh for zhs in shortlist.values() for zh in zhs}
    zh_df = Counter()
    for text in ref_text:
        zh_df.update(chinese_strings(text) & wanted)

    rows = []
    for term, segs in postings.items():
        # Product names stay in English (style rule R2), and terms containing
        # them ("Visualforce page" -> "Visualforce 页面") cannot be matched by
        # Chinese-only strings: leave them out.
        if any(sum(word in ref_text[i] for i in segs) >= KEPT_ENGLISH * len(segs)
               for word in term.split()):
            continue
        scored = []
        for zh in shortlist[term]:
            both = sum(zh in ref_text[i] for i in segs)
            # Dice: 2|E∩Z| / (|E| + |Z|). High only if the English term and
            # the Chinese string occur in nearly the same segments, so a
            # frequent word like 设置 cannot win by being everywhere.
            dice = 2 * both / (len(segs) + zh_df[zh])
            scored.append((dice, zh, both / len(segs)))
        if not scored:
            continue
        best = max(s[0] for s in scored)
        # Near-ties go to the longer string, so "电子邮件" beats its fragment "电子邮".
        dice, zh, hit = max((s for s in scored if s[0] >= best - 0.03), key=lambda s: len(s[1]))
        if hit < MIN_HIT:
            continue
        # Alternatives worth showing a reviewer: not fragments of the chosen string.
        others = sorted((s for s in scored if s[1] not in zh), reverse=True)[:3]
        auto = hit >= TEXT_AUTO_HIT and dice >= TEXT_AUTO_DICE and len(segs) >= TEXT_AUTO_COUNT
        rows.append({
            "scope": "text", "source": term, "target": zh,
            "kind": "required" if hit >= TEXT_REQUIRED_HIT else "ambiguous",
            "review": "auto" if auto else "todo",
            "tag_share": "", "hit_rate": f"{hit:.2f}", "dice": f"{dice:.2f}",
            "train_count": len(segs), "eval_segments": eval_terms[term],
            "alternatives": "; ".join(f"{z} (hit {h:.2f})" for _, z, h in others),
        })
    # A word whose chosen "translation" contains the whole translation of a
    # longer phrase around it is a fragment of that phrase: "Quick" -> 快速查找
    # is really "Quick Find box". "case" -> 个案 is not a fragment of
    # "case team" -> 个案团队: it is a term of its own.
    def fragment(r):
        return any(r is not longer and f" {r['source']} " in f" {longer['source']} "
                   and longer["target"] in r["target"]
                   for longer in rows)
    rows = [r for r in rows if not fragment(r)]
    # The gate matches plurals, so "Entitlements" adds nothing next to "Entitlement".
    sources = {r["source"] for r in rows}
    return [r for r in rows if r["source"] == singular(r["source"]) or singular(r["source"]) not in sources]


# ---------------------------------------------------------------------- main

def evidence(row):
    if row["scope"] == "ui":
        text = f"tag share {row['tag_share']}, {row['train_count']} aligned pairs"
    else:
        text = f"hit rate {row['hit_rate']}, dice {row['dice']}, {row['train_count']} train segments"
    text += f", {row['eval_segments']} golden/tune segments"
    return text + (f"; alternatives: {row['alternatives']}" if row["alternatives"] else "")


def main():
    train_rows = load_jsonl(TRAIN)
    eval_rows = [row for path in EVAL_SETS for row in load_jsonl(path)]

    ui = ui_candidates(train_rows, eval_rows)
    text = text_candidates(train_rows, eval_rows)
    candidates = sorted(ui, key=lambda r: -r["eval_segments"]) + \
        sorted(text, key=lambda r: -r["eval_segments"])
    write_csv(CANDIDATES_OUT, candidates, list(candidates[0].keys()))

    # Reviewer shortlist: every relevant UI label, and text terms in 2+ eval segments.
    review = [r for r in candidates if r["scope"] == "ui" or r["eval_segments"] >= REVIEW_MIN_EVAL]
    write_csv(REVIEW_OUT,
              [{"scope": r["scope"], "source": r["source"], "target": r["target"],
                "kind": r["kind"], "note": "", "review": r["review"], "evidence": evidence(r)}
               for r in review],
              ["scope", "source", "target", "kind", "note", "review", "evidence"])

    for scope, rows in (("ui", ui), ("text", text)):
        short = [r for r in review if r["scope"] == scope]
        print(f"{scope:<4}: {len(rows)} candidates; shortlist {len(short)} "
              f"({sum(r['kind'] == 'required' for r in short)} required, "
              f"{sum(r['review'] == 'auto' for r in short)} auto-approved)")
    print(f"Written to {CANDIDATES_OUT.relative_to(REPO_ROOT)} and {REVIEW_OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
