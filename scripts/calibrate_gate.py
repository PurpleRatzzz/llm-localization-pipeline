"""Calibrate the QA gate on human reference translations.

Runs the gate on all 2000 dev (source, human reference) pairs without a
glossary. A human reference should pass, so anything that fires is a false
positive, unless the reference itself is broken (verdicts below were recorded
by reading each example). Also re-measures the numbers quoted in qa_gate.py.

Usage (from the repo root):
    .venv/bin/python -I scripts/calibrate_gate.py
Writes reports/gate_calibration.md.
"""

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# "python -I" (isolated mode) does not put the repo on sys.path, so add it
# here to be able to import the local l10n_pipeline package.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from l10n_pipeline import qa_gate  # noqa: E402

EN_FILE = REPO / "data/raw/enzh_en_dev.json"
ZH_FILE = REPO / "data/raw/enzh_zh_dev.json"
REPORT = REPO / "reports/gate_calibration.md"

# Verdicts for every reference the gate flags, recorded by reading each one.
# Key: (check, last 4 digits of the segment ID).
# "real" = the reference itself is wrong (gate is right);
# "fp"   = the reference is fine (gate false positive).
VERDICTS = {
    ("tag_nesting", "1054"): ("real", "reference restructures the menucascade and misaligns "
                              "the labels (Content Targeting -> 针对, Topics -> 主题的内容); "
                              "the source markup is odd too"),
    ("tag_nesting", "1539"): ("real", "reference moves a <uicontrol> out of the <menucascade>"),
    ("protected_content", "0392"): ("real", "`community` is a parameter name; translating it "
                                    "breaks the instruction"),
    ("protected_content", "0514"): ("real", "`augment` is an attribute value and must stay English"),
    ("protected_content", "0808"): ("real", "`body` is an HTML tag name; 正文 is wrong"),
    ("protected_content", "0978"): ("fp", "the source tags the prose name 'registration handler' "
                                    "as code (the real interface Auth.RegistrationHandler is kept); "
                                    "translating it is defensible"),
    ("protected_content", "1352"): ("fp", "'Community Template' is a UI label wrongly tagged "
                                    "<filepath> in the source; translating it is correct"),
    ("protected_content", "1914"): ("real", "identifier `feed-items` changed to `feed-item`"),
    ("escaped_literals", "1050"): ("fp", "'Dataflows & Recipes' -> 数据流和模式: '&' written as 和"),
    ("escaped_literals", "1148"): ("fp", "'Summary & Image' -> 汇总和图像: '&' written as 和"),
    ("numbers", "0473"): ("fp", "'up to three' -> 最多 3 个 (digits are normal in zh)"),
    ("numbers", "0489"): ("fp", "'up to three' -> 最多 3 个"),
    ("numbers", "0592"): ("fp", "'Zero' -> 0"),
    ("numbers", "0615"): ("fp", "12:00:00 AM -> 凌晨 00:00:00 (24-hour clock)"),
    ("numbers", "0803"): ("fp", "'six' -> 6"),
    ("numbers", "0969"): ("fp", "'January 1' -> 1 月 1 日"),
    ("numbers", "1304"): ("fp", "12:00:00 AM -> 凌晨 00:00:00 (24-hour clock)"),
    ("numbers", "1373"): ("fp", "'50 percent' -> 一半 (half)"),
    ("numbers", "1703"): ("fp", "'10' -> 十"),
    ("numbers", "1872"): ("real", "'7 Days After Case' - the 7 days are missing in the reference"),
    ("numbers", "1935"): ("fp", "'10 million' -> 1000 万 (Chinese counts in units of 10,000)"),
    ("length", "0342"): ("real", "'Edit the page layout' -> 编辑: 'page layout' missing"),
    ("length", "0400"): ("fp", "fragment 'to a question' -> 问题"),
    ("length", "0465"): ("fp", "complete, just concise"),
    ("length", "0675"): ("fp", "dense noun compound, complete"),
    ("length", "0751"): ("fp", "'creating library permissions' -> 创建库权限, complete"),
    ("length", "0992"): ("fp", "'Carriage return' -> 回车"),
    ("length", "1158"): ("fp", "the EN source is already Chinese (上午6:00); dataset anomaly"),
    ("length", "1486"): ("fp", "mostly a product name; 在 ... 中 plus spaces makes it longer"),
    ("length", "1821"): ("fp", "'SalesforceA app' -> SalesforceA 应用程序"),
    ("length", "1839"): ("real", "'enabling communities' -> 社区: 'enabling' missing"),
    ("length", "1884"): ("fp", "'Drag-and-drop scheduling' -> 拖放计划"),
    ("cjk_latin_spacing", "0484"): ("real", "活动的Salesforce: missing space"),
    ("cjk_latin_spacing", "1074"): ("real", "Lightning应用程序: missing space"),
}
# All tag_order warnings are legitimate reorders (that is why it is a warning).
for _id in ("0248", "0477", "0839", "0925", "1208", "1213", "1248", "1379", "1477", "1698",
            "1895", "1997"):
    VERDICTS[("tag_order", _id)] = ("fp", "legitimate reorder for Chinese word order (e.g. "
                                    "在<parmname>快速查找</parmname>框中输入<userinput>X</userinput>)")

# Decisions taken from the data, and changes made after looking at the results.
CHANGES = [
    "Before the first run, the protected tag set was chosen from the verbatim table above: "
    "codeph, varname, filepath. ph (48%), b (57%), userinput (34%) and systemoutput (4/6) "
    "are translated too often to be protected.",
    "`tag_order` is a warning: re-measured 14/518 tagged references reorder tags. The "
    "structural checks are chained (tag_set, then tag_nesting, then tag_order), so the 2 "
    "references with a nesting error are not counted again as reorders (14 reorders -> 12 "
    "tag_order warnings).",
    "`escaped_literals`: severity per entity. `&lt;` 7/7 and `&gt;` 11/11 are kept -> error; "
    "`&amp;` is written as 和 in 2/7 references -> warning.",
    "`numbers` is a warning: 10 of the 11 references it flags are correct localization. As an "
    "error it would fail 8% of the references that contain digits.",
    "The data scan found `#URL1#`-style URL masks (42 occurrences in 37 segments, 42/42 kept) "
    "and `{!...}` merge fields (4/4 kept). Both were added to the placeholder pattern, and "
    "placeholders are removed before counting digits (otherwise #URL1# looks like the number 1).",
    "After the first full run (8 references failed: 6 real problems, 2 false positives), "
    "codeph and filepath stayed protected. Both false positives (0978, 1352) come from the "
    "source tagging prose as code. A false positive costs one human review; a translated "
    "identifier ships broken code to readers. A narrower rule (e.g. skip tag texts that contain "
    "spaces) would also skip real code such as `CloseDate &lt; TODAY()`.",
    "The length message now prints 3 decimals: the first run showed `ratio 0.18 is outside "
    "0.18-1.0` for a ratio of 0.179.",
    "The CJK-Latin measurement counts overlapping adjacencies (lookahead), so `中A中` counts 2.",
]


def short_id(seg_id: str) -> str:
    return seg_id[-4:]


def excerpt(text: str, width: int = 110) -> str:
    text = text.replace("`", "'")
    return text if len(text) <= width else text[:width] + "..."


# ------------------------------------------------------------ measurements

def scan_data(en: dict, zh: dict) -> list[str]:
    """Look for anything that could break the XML parser."""
    lines = []
    for lang, segs in (("en", en), ("zh", zh)):
        entities = Counter(m for s in segs.values() for m in re.findall(r"&[#\w]+;", s))
        with_attrs = sum(1 for s in segs.values() if re.search(r"<\w+\s+[^>]*>", s))
        other = sum(1 for s in segs.values() if re.search(r"<[!?]|/>", s))
        failures = sum(1 for s in segs.values() if qa_gate.parse_fragment(s)[0] is None)
        lines.append(f"- {lang}: entities {dict(entities)}; tags with attributes: {with_attrs}; "
                     f"comments/CDATA/self-closing tags: {other}; parse failures: {failures}")
    return lines


def measure_verbatim_tags(pairs) -> dict:
    """Per tag type: how often the source text is kept unchanged in the same tag."""
    stats = defaultdict(lambda: [0, 0])   # tag -> [source elements, kept verbatim]
    for src_root, ref_root in pairs:
        ref_texts = Counter((e.tag, "".join(e.itertext()).strip()) for e in ref_root.iter())
        for elem in src_root.iter():
            if elem is src_root:
                continue
            key = (elem.tag, "".join(elem.itertext()).strip())
            stats[elem.tag][0] += 1
            if ref_texts[key] > 0:    # consume, so 2 source copies need 2 target copies
                ref_texts[key] -= 1
                stats[elem.tag][1] += 1
    return stats


def measure_tag_order(pairs) -> tuple[int, int]:
    tagged = [(s, r) for s, r in pairs if len(s)]
    reordered = sum(1 for s, r in tagged
                    if [e.tag for e in s.iter()] != [e.tag for e in r.iter()])
    return len(tagged), reordered


def measure_escaped(en: dict, zh: dict) -> dict:
    result = {}
    for entity in qa_gate.ESCAPED_LITERALS:
        ids = [k for k in en if entity in en[k]]
        same = sum(1 for k in ids if en[k].count(entity) == zh[k].count(entity))
        result[entity] = (len(ids), same)
    return result


def measure_numbers(pairs) -> int:
    """Segments whose (unprotected, placeholder-free) text contains a digit."""
    count = 0
    for src_root, ref_root in pairs:
        text = "".join(qa_gate.text_nodes(src_root, skip_protected=True)
                       + qa_gate.text_nodes(ref_root, skip_protected=True))
        if qa_gate.NUMBER_RE.search(qa_gate.PLACEHOLDER_RE.sub(" ", text)):
            count += 1
    return count


def measure_spacing(pairs) -> tuple[int, int]:
    # Wrapping a pattern in a lookahead "(?=...)" counts overlapping matches,
    # so "中A中" counts as 2 adjacencies, not 1.
    without_re = re.compile(f"(?={qa_gate.MISSING_SPACE_RE.pattern})")
    with_re = re.compile(f"(?=[{qa_gate.CJK}] [A-Za-z0-9]|[A-Za-z0-9] [{qa_gate.CJK}])")
    with_space = without = 0
    for _, ref_root in pairs:
        for node in qa_gate.text_nodes(ref_root):
            with_space += len(with_re.findall(node))
            without += len(without_re.findall(node))
    return with_space, without


def measure_length(pairs) -> dict:
    ratios = []
    for src_root, ref_root in pairs:
        src = "".join(qa_gate.text_nodes(src_root))
        ref = "".join(qa_gate.text_nodes(ref_root))
        if src:
            ratios.append(len(ref) / len(src))
    cuts = statistics.quantiles(ratios, n=200, method="inclusive")   # 0.5% steps
    outside = sum(1 for r in ratios
                  if not qa_gate.LENGTH_RATIO_MIN <= r <= qa_gate.LENGTH_RATIO_MAX)
    return {"n": len(ratios), "min": min(ratios), "p0.5": cuts[0], "median": cuts[99],
            "p99.5": cuts[-1], "max": max(ratios), "outside": outside,
            "exactly_1": sum(1 for r in ratios if r == 1.0)}


# ------------------------------------------------------------------- main

def main():
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))["text"]
    zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))["text"]
    ids = sorted(en)
    assert set(en) == set(zh), "en and zh dev files must share segment IDs"
    pairs = [(qa_gate.parse_fragment(en[k])[0], qa_gate.parse_fragment(zh[k])[0]) for k in ids]

    # Run the gate exactly as the pipeline would, with no glossary.
    counts = defaultdict(lambda: {"error": set(), "warning": set()})
    examples = defaultdict(list)   # (check, severity) -> [(id, first message)]
    failed = 0
    for k in ids:
        issues = qa_gate.check_segment(en[k], zh[k])
        if not qa_gate.passed(issues):
            failed += 1
        for issue in issues:
            bucket = counts[issue.check][issue.severity]
            if k not in bucket:
                examples[(issue.check, issue.severity)].append((k, issue.message))
            bucket.add(k)

    def verdict_counts(check):
        kinds = [VERDICTS.get((check, short_id(k)), ("?", ""))[0]
                 for severity in ("error", "warning") for k in counts[check][severity]]
        return kinds.count("real"), kinds.count("fp"), kinds.count("?")

    # A failing reference is a false positive if none of its errors is a real problem.
    real_error_ids = {k for check in counts for k in counts[check]["error"]
                      if VERDICTS.get((check, short_id(k)), ("?",))[0] == "real"}
    error_fp = failed - len(real_error_ids)

    checks = ["xml_wellformed", "tag_set", "tag_nesting", "tag_order", "protected_content",
              "escaped_literals", "untranslated", "numbers", "placeholders", "terminology", "length",
              "cjk_latin_spacing"]
    out = ["# QA gate calibration on human references", "",
           f"Gate run on all {len(ids)} dev pairs (EN source, human ZH reference), "
           "no glossary. Generated by `scripts/calibrate_gate.py`.", "",
           f"**{failed} of {len(ids)} references fail the gate** (have at least one error); "
           f"**{error_fp} of those are gate false positives** "
           f"({error_fp / len(ids):.1%} of all references), the rest are broken references.", "",
           "## Data scan (constructs that could break the XML parser)", ""]
    out += scan_data(en, zh)
    out += ["", "## Issues per check (number of segments)", "",
            "Verdicts were recorded by reading every flagged reference: *real* = the reference "
            "is wrong and the gate is right; *false positive* = the reference is fine.", "",
            "| check | errors | warnings | real problems | false positives |",
            "|---|---:|---:|---:|---:|"]
    for check in checks:
        real, fp, unknown = verdict_counts(check)
        fp_cell = f"{fp}" + (f" (+{unknown} without verdict)" if unknown else "")
        out.append(f"| {check} | {len(counts[check]['error'])} | "
                   f"{len(counts[check]['warning'])} | {real} | {fp_cell} |")

    out += ["", "## Examples and verdicts", "",
            "Every error-level example, and up to 3 per warning-level check.", ""]
    for check in checks:
        for severity in ("error", "warning"):
            flagged = examples[(check, severity)]
            if not flagged:
                continue
            shown = flagged if severity == "error" else flagged[:3]
            out.append(f"### {check} ({severity}, {len(flagged)} segments)")
            out.append("")
            for k, message in shown:
                kind, note = VERDICTS.get((check, short_id(k)), ("?", "no verdict recorded"))
                label = {"real": "reference problem", "fp": "gate false positive"}.get(kind, "?")
                out.append(f"- **{short_id(k)}**: `{excerpt(message, 140)}`")
                out.append(f"  - EN: `{excerpt(en[k])}`")
                out.append(f"  - ZH: `{excerpt(zh[k])}`")
                out.append(f"  - Verdict: **{label}**: {note}")
            if len(flagged) > len(shown):
                rest = ", ".join(short_id(k) for k, _ in flagged[len(shown):])
                out.append(f"- Also flagged: {rest}")
            out.append("")

    out += ["## Measurements behind the severities", "",
            "### Protected tags: is the tag text kept verbatim in the reference?", "",
            "| tag | source elements | kept verbatim | % | protected |", "|---|---:|---:|---:|---|"]
    tag_stats = measure_verbatim_tags(pairs)
    for tag, (n, kept) in sorted(tag_stats.items(), key=lambda x: -x[1][0]):
        mark = "yes" if tag in qa_gate.PROTECTED_TAGS else ""
        out.append(f"| {tag} | {n} | {kept} | {kept / n:.0%} | {mark} |")

    tagged, reordered = measure_tag_order(pairs)
    out += ["", "### Tag order", "",
            f"{reordered} of {tagged} tagged references put the tags in a different order "
            f"({reordered / tagged:.1%}) -> warning."]

    out += ["", "### Escaped literals", "",
            "| entity | source segments | reference keeps the count | severity |",
            "|---|---:|---:|---|"]
    for entity, (n, same) in measure_escaped(en, zh).items():
        out.append(f"| `{entity}` | {n} | {same} | {qa_gate.ESCAPED_LITERALS[entity]} |")

    with_digits = measure_numbers(pairs)
    n_num = len(counts["numbers"]["warning"])
    out += ["", "### Numbers", "",
            f"{with_digits} segments contain digits (outside protected tags and placeholders); "
            f"{n_num} references differ from their source ({n_num / with_digits:.1%} of "
            f"digit-bearing segments, {n_num / len(ids):.1%} of all). Most are correct "
            "localization (see examples), so the check is a warning."]

    with_space, without = measure_spacing(pairs)
    total = with_space + without
    out += ["", "### CJK-Latin spacing (inside text nodes, tags and punctuation excluded)", "",
            f"- with a space: {with_space}", f"- without a space: {without}",
            f"- consistency: {with_space / total:.2%} -> warning (cosmetic, never changes meaning)"]

    length = measure_length(pairs)
    out += ["", "### Length ratio (target/source characters, tags stripped)", "",
            f"- {length['n']} pairs: min {length['min']:.3f}, 0.5th pct {length['p0.5']:.3f}, "
            f"median {length['median']:.3f}, 99.5th pct {length['p99.5']:.3f}, "
            f"max {length['max']:.3f}",
            f"- {length['exactly_1']} references have ratio exactly 1.0 (untranslated "
            "identifiers, code), which is why the 99.5th percentile is 1.0",
            f"- bounds used (percentiles rounded outward): {qa_gate.LENGTH_RATIO_MIN}-"
            f"{qa_gate.LENGTH_RATIO_MAX}; {length['outside']} references fall outside -> warning"]

    out += ["", "## Decisions and changes driven by calibration", ""]
    out += [f"{i}. {change}" for i, change in enumerate(CHANGES, 1)]
    out += ["", "## Not calibrated yet", "",
            "- `terminology`: the glossary does not exist yet, so this run uses no glossary "
            "and the check never fires. It is covered by unit tests with a small fixture "
            "glossary; calibrate it on the references once the real glossary is built. "
            "Matching is case-sensitive, and the dev set often names objects in lowercase "
            "prose (e.g. 'person account' 6 segments vs 'Person Account' 2, 'case' 49 vs "
            "'Case' 27), so expect missed matches rather than false alarms.",
            "- `placeholders` `{0}` and `max_length` (content_type \"ui\"): the dev set has no "
            "UI strings; covered by unit tests only.", ""]

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"{failed}/{len(ids)} references fail the gate; report written to {REPORT}")
    for check in checks:
        print(f"  {check:18} errors={len(counts[check]['error']):3} "
              f"warnings={len(counts[check]['warning']):3}")


if __name__ == "__main__":
    main()
