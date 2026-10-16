"""Deterministic QA gate: checks that have exactly one correct answer.

No LLM is involved, so every result is free and 100% reproducible. Each check
is one small function that returns a list of Issues; check_segment() runs them
in order. An "error" fails the gate (the segment goes to human review); a
"warning" is only reported.

Severities were chosen by running the gate on the 2000 human reference
translations of the dev set (scripts/calibrate_gate.py ->
reports/gate_calibration.md). Human references should pass, so anything that
fires on them is a false positive unless the reference itself is broken. The
numbers quoted in the comments below come from that calibration.
"""

import html
import re
import xml.etree.ElementTree as ET
from collections import Counter

from l10n_pipeline.units import GlossaryEntry, Issue

# Tags whose text must be copied verbatim (code, variable names, file paths).
# Measured on the dev set: how often the reference keeps the source text of a
# tag unchanged inside the same tag:
#   varname 18/18 (100%), codeph 58/63 (92%), filepath 9/10 (90%).
# All 5 codeph misses are reference errors (e.g. the HTML tag
# <codeph>body</codeph> translated as 正文); the 1 filepath miss is a UI label
# ("Community Template") wrongly tagged as filepath in the source.
# Translated, so NOT protected: ph 48%, b 57%, userinput 34%,
# parmname 8%, uicontrol 6%, systemoutput 4/6 (these are UI labels and prose).
PROTECTED_TAGS = {"codeph", "varname", "filepath"}

# Placeholders that must survive translation exactly:
#   {0}, {1}  Salesforce Custom Label parameters (UI strings)
#   #URL1#    the dataset masks URLs this way: 42/42 kept in dev references
#   {!Name}   Salesforce merge fields: 4/4 kept in dev references
PLACEHOLDER_RE = re.compile(r"\{\d+\}|#URL\d+#|\{![^}]*\}")

# ASCII digits only on purpose: zh-CN uses half-width digits, so a full-width
# "３" in the target should show up as a mismatch.
NUMBER_RE = re.compile(r"[0-9]+")

# Escaped characters as they appear in the raw segment, with their severity.
# &lt; and &gt; are code or menu arrows: the reference keeps the same count in
# 7/7 and 11/11 segments -> error.
# &amp; is often the word "and" inside a UI name ("Dataflows &amp; Recipes"),
# which translators may write as 和: 2 of 7 segments do that -> warning.
ESCAPED_LITERALS = {"&lt;": "error", "&gt;": "error", "&amp;": "warning"}

# Allowed target/source character-length ratio for "doc" segments (tags
# stripped). Derived from the 2000 dev references: 0.5th percentile = 0.187,
# 99.5th percentile = 1.000, rounded outward to 2 decimals. The upper bound is
# exactly 1.0 because untranslated identifiers (ratio 1.0) are common, while
# Chinese is almost never longer than English in characters (3/2000 refs).
LENGTH_RATIO_MIN = 0.18
LENGTH_RATIO_MAX = 1.0

# zh-CN convention: a space between Chinese characters and Latin letters or
# digits ("启用 Chatter"). The references follow it in 1777 of 1779
# adjacencies (99.9%), so a missing space is almost certainly a slip. It is a
# warning, not an error, because it is cosmetic and never changes meaning.
CJK = "\u3400-\u4dbf\u4e00-\u9fff"   # CJK Unified Ideographs + Extension A
MISSING_SPACE_RE = re.compile(f"[{CJK}][A-Za-z0-9]|[A-Za-z0-9][{CJK}]")


# ---------------------------------------------------------------- helpers

def parse_fragment(segment: str) -> tuple[ET.Element | None, str]:
    """Parse a segment as an XML fragment.

    Returns (root, "") on success or (None, reason) on failure.
    """
    # ElementTree only knows the 5 XML entities. HTML ones such as &nbsp; would
    # make parsing fail; none occur in the dev data, but decode them anyway so
    # a single &nbsp; does not switch off all the tag checks.
    fixed = re.sub(r"&(?!(?:lt|gt|amp|quot|apos);)[A-Za-z]+;",
                   lambda m: html.unescape(m.group()), segment)
    try:
        # A segment is a fragment ("text <b>x</b> text"), so give it a root.
        return ET.fromstring(f"<root>{fixed}</root>"), ""
    except ET.ParseError as err:
        return None, str(err).split(":")[0]   # e.g. "mismatched tag"


def text_nodes(root: ET.Element, skip_protected: bool = False) -> list[str]:
    """The text between tags, in reading order (entities already decoded).

    With skip_protected=True, the text inside PROTECTED_TAGS is left out; it
    is already checked verbatim by check_protected_content.
    """
    nodes = []

    def walk(elem):
        if skip_protected and elem.tag in PROTECTED_TAGS:
            nodes.append(" ")   # keep the words on both sides of the tag apart
            return
        if elem.text:
            nodes.append(elem.text)
        for child in elem:
            walk(child)
            if child.tail:      # text after </child> belongs to elem, not child
                nodes.append(child.tail)

    walk(root)
    return nodes


def _fallback_text_nodes(segment: str) -> list[str]:
    """Approximate text nodes for a segment that is not well-formed XML."""
    return [html.unescape(piece) for piece in re.split(r"<[^>]*>", segment)]


def _elements(root: ET.Element) -> list[ET.Element]:
    """All tags in document order, without our artificial <root>."""
    return [elem for elem in root.iter() if elem is not root]


def _tag_label(elem: ET.Element) -> str:
    # No dev segment has attributes, but include them so an attribute the
    # model invents (or drops) still counts as a different tag.
    attrs = "".join(f' {k}="{v}"' for k, v in sorted(elem.attrib.items()))
    return f"<{elem.tag}{attrs}>"


def _diff_counts(source: Counter, target: Counter) -> list[tuple[str, int, int]]:
    """Items whose count differs: (item, count in source, count in target)."""
    return [(item, source[item], target[item])
            for item in sorted(source.keys() | target.keys(), key=str)
            if source[item] != target[item]]


# ----------------------------------------------------------------- checks

def check_xml_wellformed(src_error: str, tgt_error: str) -> list[Issue]:
    # The arguments are the parse errors from parse_fragment ("" = parsed OK).
    # All 2000 dev sources and references parse.
    if src_error:
        # Not the translator's fault, but the tag checks cannot run.
        return [Issue("xml_wellformed", "warning",
                      f"Source is not well-formed XML ({src_error}); tag checks skipped")]
    if tgt_error:
        return [Issue("xml_wellformed", "error",
                      f"Target is not well-formed XML ({tgt_error})")]
    return []


def check_tag_set(src_root: ET.Element, tgt_root: ET.Element) -> list[Issue]:
    # 0 of 518 tagged dev references change the set of tags.
    src = Counter(_tag_label(e) for e in _elements(src_root))
    tgt = Counter(_tag_label(e) for e in _elements(tgt_root))
    issues = []
    for tag, n_src, n_tgt in _diff_counts(src, tgt):
        what = "missing in target" if n_tgt < n_src else "extra in target"
        issues.append(Issue("tag_set", "error",
                            f"Tag {tag} {what}: source has {n_src}, target has {n_tgt}"))
    return issues


def check_tag_nesting(src_root: ET.Element, tgt_root: ET.Element) -> list[Issue]:
    # Compare (tag, parent) pairs: same tags, but is each one inside the same
    # kind of tag? 2 of 518 dev references differ, and both are broken
    # references (a <menucascade> rebuilt with misaligned labels; a <uicontrol>
    # moved out of its <menucascade>).
    def pairs(root):
        return Counter((child.tag, parent.tag)
                       for parent in root.iter() for child in parent)

    issues = []
    for (tag, parent), n_src, n_tgt in _diff_counts(pairs(src_root), pairs(tgt_root)):
        where = "at top level" if parent == "root" else f"inside <{parent}>"
        issues.append(Issue("tag_nesting", "error",
                            f"Tag <{tag}> {where}: source has {n_src}, target has {n_tgt}"))
    return issues


def check_tag_order(src_root: ET.Element, tgt_root: ET.Element) -> list[Issue]:
    # Chinese word order can legitimately move tags: 14 of 518 tagged dev
    # references reorder them (e.g. "enter <userinput>X</userinput> in the
    # <parmname>Quick Find</parmname> box" -> 在<parmname>快速查找</parmname>
    # 方框中输入<userinput>X</userinput>). So this is a warning only.
    src = [e.tag for e in _elements(src_root)]
    tgt = [e.tag for e in _elements(tgt_root)]
    if src == tgt:
        return []
    return [Issue("tag_order", "warning",
                  f"Tag order differs from source: source {', '.join(src)}; "
                  f"target {', '.join(tgt)}")]


def check_protected_content(src_root: ET.Element, tgt_root: ET.Element) -> list[Issue]:
    def protected_texts(root, tag):
        # Leading/trailing spaces inside a tag do not change the code.
        return Counter("".join(e.itertext()).strip() for e in root.iter(tag))

    issues = []
    for tag in sorted(PROTECTED_TAGS):
        missing = protected_texts(src_root, tag) - protected_texts(tgt_root, tag)
        for text in missing.elements():
            issues.append(Issue("protected_content", "error",
                                f'Tag <{tag}> content changed: "{text}" missing in target'))
    return issues


def check_escaped_literals(source: str, target: str) -> list[Issue]:
    issues = []
    for entity, severity in ESCAPED_LITERALS.items():
        n_src, n_tgt = source.count(entity), target.count(entity)
        if n_src != n_tgt:
            issues.append(Issue("escaped_literals", severity,
                                f"Escaped {entity} count differs: source has {n_src}, "
                                f"target has {n_tgt}"))
    return issues


def check_numbers(source_text: str, target_text: str) -> list[Issue]:
    # Warning, not error: 11 of the 136 dev references that contain digits
    # differ, and 10 of those are correct translations ("three" -> 3,
    # "12:00:00 AM" -> 00:00:00, "10 million" -> 1000 万). As an error, it
    # would send ~7% of number-bearing segments to review for nothing.
    def numbers(text):
        # Placeholders are checked separately; "#URL1#" is not the number 1.
        return Counter(NUMBER_RE.findall(PLACEHOLDER_RE.sub(" ", text)))

    issues = []
    for number, n_src, n_tgt in _diff_counts(numbers(source_text), numbers(target_text)):
        issues.append(Issue("numbers", "warning",
                            f'Number "{number}" appears {n_src}x in source, {n_tgt}x in target'))
    return issues


def check_placeholders(source: str, target: str) -> list[Issue]:
    src = Counter(PLACEHOLDER_RE.findall(source))
    tgt = Counter(PLACEHOLDER_RE.findall(target))
    issues = []
    for placeholder, n_src, n_tgt in _diff_counts(src, tgt):
        what = "missing in target" if n_tgt < n_src else "extra in target"
        issues.append(Issue("placeholders", "error",
                            f"Placeholder {placeholder} {what}: source has {n_src}, "
                            f"target has {n_tgt}"))
    return issues


def term_pattern(term: str) -> str:
    """Regex for a term and its English plurals, as a whole word.

    Public so scripts/extract_terms.py validates glossary candidates with
    exactly the matching rule the gate enforces.
    """
    forms = [re.escape(term) + r"(?:e?s)?"]           # Account(s), Business(es), Key(s)
    if len(term) > 1 and term.endswith("y") and term[-2].isalpha() \
            and term[-2].lower() not in "aeiou":
        forms.append(re.escape(term[:-1]) + "ies")    # Opportunity -> Opportunities
    # (?<!\w) and (?!\w): no letter/digit right before or after, i.e. a whole word.
    return r"(?<!\w)(?:" + "|".join(forms) + r")(?!\w)"


def find_terms(source: str, glossary: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """Glossary entries (any kind) whose source term occurs in `source`, longest match first,
    ignoring text inside protected tags.

    Public so the prompt builder shows the model exactly the terms the gate
    will check. Matching is CASE-SENSITIVE: Salesforce object and UI names are
    capitalized, while the lowercase word often means something else and has a
    different translation ("Case" -> 个案 but "in this case" -> 情况; "Lead" ->
    潜在客户 but "lead to" -> 导致). Whole words only; plurals +s, +es, and
    consonant+y -> ies are accepted.
    """
    root, _ = parse_fragment(source)
    if root is not None:
        text = "".join(text_nodes(root, skip_protected=True))
    else:
        # Not XML (e.g. a UI string with a literal "&"): use the text as it is.
        text = "".join(_fallback_text_nodes(source))

    found = []
    claimed = []   # character spans already matched by a longer term
    # Longest term first, so "Person Account" claims its words before "Account"
    # gets a chance to match inside it.
    for entry in sorted(glossary, key=lambda e: len(e.source), reverse=True):
        matched = False
        for match in re.finditer(term_pattern(entry.source), text):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue   # inside a longer term that already matched
            claimed.append((start, end))
            matched = True
        if matched:
            found.append(entry)
    return found


def check_terminology(source: str, target_text: str,
                      glossary: list[GlossaryEntry]) -> list[Issue]:
    issues = []
    for entry in find_terms(source, glossary):
        # Ambiguous terms still claim their words inside find_terms (so a longer
        # ambiguous term hides a shorter required one), but only required terms
        # are enforced: ambiguous ones need judgement, not a rule.
        if entry.kind == "required" and entry.target not in target_text:
            issues.append(Issue("terminology", "error",
                                f'Term "{entry.source}" must be translated as '
                                f'"{entry.target}", not found in target'))
    return issues


def check_length(source_text: str, target_text: str, max_length: int | None,
                 content_type: str) -> list[Issue]:
    issues = []
    # A hard limit (e.g. a UI label that must fit a button) is never negotiable.
    if max_length is not None and len(target_text) > max_length:
        issues.append(Issue("length", "error",
                            f"Target is {len(target_text)} characters, "
                            f"max_length is {max_length}"))
    # For docs there is no hard limit; a ratio far from what human translators
    # produce hints at an omission or added text.
    if content_type == "doc" and source_text:
        ratio = len(target_text) / len(source_text)
        if not LENGTH_RATIO_MIN <= ratio <= LENGTH_RATIO_MAX:
            issues.append(Issue("length", "warning",
                                f"Target/source length ratio {ratio:.3f} is outside "
                                f"the usual {LENGTH_RATIO_MIN}-{LENGTH_RATIO_MAX}"))
    return issues


def check_cjk_latin_spacing(target_nodes: list[str]) -> list[Issue]:
    # Checked inside each text node, so "输入<b>Chatter</b>" (a tag between the
    # two scripts) is not counted, and punctuation never forms an adjacency.
    found = [m for node in target_nodes for m in MISSING_SPACE_RE.findall(node)]
    if not found:
        return []
    examples = ", ".join(f'"{m}"' for m in found[:3])
    return [Issue("cjk_latin_spacing", "warning",
                  f"Missing space between Chinese and Latin text at {examples}")]


# ------------------------------------------------------------- public API

def check_segment(source: str, target: str, *, glossary: list[GlossaryEntry] | None = None,
                  max_length: int | None = None, content_type: str = "doc") -> list[Issue]:
    """Run every deterministic check on one (source, target) pair."""
    issues = []

    if content_type == "ui":
        # UI strings (Custom Labels) are plain text, not XML: "&" is a literal
        # character and there are no tags, so the XML checks do not apply.
        src_nodes, tgt_nodes = [source], [target]
        src_free, tgt_free = source, target
    else:
        src_root, src_error = parse_fragment(source)
        tgt_root, tgt_error = parse_fragment(target)
        issues += check_xml_wellformed(src_error, tgt_error)
        if src_root is not None and tgt_root is not None:
            tag_issues = check_tag_set(src_root, tgt_root)
            # Only look at nesting/order once the tags match; otherwise one
            # missing tag would be reported three times.
            if not tag_issues:
                tag_issues = check_tag_nesting(src_root, tgt_root)
            if not tag_issues:
                tag_issues = check_tag_order(src_root, tgt_root)
            issues += tag_issues
            issues += check_protected_content(src_root, tgt_root)
            src_nodes, tgt_nodes = text_nodes(src_root), text_nodes(tgt_root)
            src_free = "".join(text_nodes(src_root, skip_protected=True))
            tgt_free = "".join(text_nodes(tgt_root, skip_protected=True))
        else:
            # Cannot trust the tree: strip tags by regex on BOTH sides so the
            # text checks below still run and stay comparable.
            src_nodes, tgt_nodes = _fallback_text_nodes(source), _fallback_text_nodes(target)
            src_free, tgt_free = "".join(src_nodes), "".join(tgt_nodes)
        issues += check_escaped_literals(source, target)

    issues += check_numbers(src_free, tgt_free)
    issues += check_placeholders(source, target)
    issues += check_terminology(source, tgt_free, glossary or [])
    issues += check_length("".join(src_nodes), "".join(tgt_nodes), max_length, content_type)
    issues += check_cjk_latin_spacing(tgt_nodes)
    return issues


def passed(issues: list[Issue]) -> bool:
    """True if nothing blocks the segment (warnings are allowed)."""
    return not any(issue.severity == "error" for issue in issues)
