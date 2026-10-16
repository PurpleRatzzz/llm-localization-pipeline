"""XLIFF 1.2 export and import.

Export: segments that need a human (they failed the QA gate or got a low AI
quality score) are written to an XLIFF 1.2 file, the exchange format that CAT
tools and translation vendors read. The reasons go into <note> elements.

Import: read an XLIFF 1.2 file back into Units, plus the translations it
holds (e.g. reviewed segments coming back, or Salesforce Custom Labels sent
by another project).

Inline tags: a "doc" segment is an XML fragment with DITA tags, e.g.
"Click <uicontrol>Save</uicontrol>.". XLIFF 1.2 does not allow foreign tags
inside <source>/<target>, so each tag is replaced by an XLIFF inline element
that keeps the tag name in its ctype attribute:
    <uicontrol>Save</uicontrol>  ->  <g id="1" ctype="x-uicontrol">Save</g>
A CAT tool shows <g> as a protected tag pair around translatable text: the
translator can move the tags but cannot break them.
"""

import xml.etree.ElementTree as ET
from collections import Counter
from xml.sax.saxutils import escape

from l10n_pipeline.qa_gate import parse_fragment
from l10n_pipeline.units import Result, Unit

XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"

# Pipeline status -> standard XLIFF 1.2 <target state="...">.
#   needs-review-translation: a translation exists, but its text must be
#     checked by a person. That is exactly what a QA failure means.
#   translated: translated, but not reviewed by a person. We do not use
#     "final" or "signed-off": those claim that a reviewer approved it.
STATES = {"needs_review": "needs-review-translation", "auto_approved": "translated"}

# XLIFF <note priority> goes from 1 (highest) to 10.
PRIORITIES = {"error": "1", "warning": "5"}


# ------------------------------------------------------------------ export

def write_xliff(units: list[Unit], results: list[Result], path, *,
                source_lang: str = "en-US", target_lang: str = "zh-CN",
                original: str = "salesforce-help.xml") -> None:
    """Write one <trans-unit> per result, in the order of `results`."""
    units_by_id = {unit.id: unit for unit in units}

    # xmlns is set like a normal attribute: every element below then belongs to
    # the XLIFF namespace without a prefix. (ElementTree's default_namespace
    # option cannot be used: it rejects un-prefixed attributes such as id.)
    xliff = ET.Element("xliff", {"version": "1.2", "xmlns": XLIFF_NS})
    file_elem = ET.SubElement(xliff, "file", {
        "original": original,              # name of the source document
        "source-language": source_lang,
        "target-language": target_lang,
        "datatype": "xml",                 # the original content is XML
    })
    body = ET.SubElement(file_elem, "body")

    pending = []   # (source, target, unit, result): content is added after indenting
    for result in results:
        unit = units_by_id[result.id]

        attrs = {"id": unit.id}
        if unit.max_length is not None:
            # The standard way to carry a length limit (e.g. a Custom Label).
            # size-unit must be given: its default in XLIFF 1.2 is "pixel".
            attrs["maxwidth"] = str(unit.max_length)
            attrs["size-unit"] = "char"
        trans_unit = ET.SubElement(body, "trans-unit", attrs)

        source = ET.SubElement(trans_unit, "source")
        target = ET.SubElement(trans_unit, "target", {"state": STATES[result.status]})

        # The XLIFF 1.2 schema puts notes after <target>.
        if unit.note:
            # Context from the content author, about the segment as a whole.
            note = ET.SubElement(trans_unit, "note",
                                 {"from": "developer", "annotates": "general"})
            note.text = unit.note
        for issue in result.issues:
            # One note per problem, so the reviewer sees why it is in the file.
            note = ET.SubElement(trans_unit, "note", {
                "from": "qa",
                "priority": PRIORITIES[issue.severity],
                "annotates": "target",     # the problem is in the translation
            })
            note.text = f"[{issue.severity}] {issue.check}: {issue.message}"

        pending.append((source, target, unit, result))

    # ET.indent replaces whitespace-only text between child elements. Inside a
    # segment that whitespace is content (the space in "<g>A</g> <g>B</g>"),
    # so indent while <source> and <target> are still empty, then fill them.
    ET.indent(xliff)
    for source, target, unit, result in pending:
        if unit.content_type == "ui":
            # A UI string is plain text, not XML: "&" or "{0}" are ordinary
            # characters. The serializer escapes "&" as &amp; in the file.
            source.text = unit.source
            target.text = result.target
            continue
        src_root, _ = parse_fragment(unit.source)
        tgt_root, _ = parse_fragment(result.target)
        ids = _inline_ids(src_root, tgt_root)
        _write_segment(source, unit.source, src_root, ids)
        _write_segment(target, result.target, tgt_root, ids)

    ET.ElementTree(xliff).write(path, encoding="utf-8", xml_declaration=True)


def _tag_keys(root: ET.Element) -> dict[ET.Element, tuple[str, int]]:
    """Key every tag by (tag name, occurrence), in document order.

    "<b>A</b> <i>B</i> <b>C</b>" -> ("b", 0), ("i", 0), ("b", 1).
    The same key in source and target means "the same tag", even when Chinese
    word order moves it (14 of 518 tagged dev references reorder tags).
    """
    seen = Counter()
    keys = {}
    for elem in root.iter():
        if elem is not root:           # skip the <root> added by parse_fragment
            keys[elem] = (elem.tag, seen[elem.tag])
            seen[elem.tag] += 1
    return keys


def _inline_ids(src_root: ET.Element | None,
                tgt_root: ET.Element | None) -> dict[ET.Element, str]:
    """Give every tag in source and target an XLIFF id: {element: "1", ...}.

    Source tags are numbered 1, 2, 3... in document order. A target tag gets
    the id of the matching source tag, which is how a CAT tool pairs them and
    checks that no tag was lost. A target tag the source does not have gets
    the next unused number.
    """
    id_by_key = {}
    ids = {}
    for root in (src_root, tgt_root):  # source first, so the target reuses its ids
        if root is None:               # not well-formed: written as plain text
            continue
        for elem, key in _tag_keys(root).items():
            if key not in id_by_key:
                id_by_key[key] = str(len(id_by_key) + 1)
            ids[elem] = id_by_key[key]
    return ids


def _write_segment(elem: ET.Element, segment: str, root: ET.Element | None,
                   ids: dict[ET.Element, str]) -> None:
    """Put one "doc" segment into an empty <source> or <target>."""
    if root is None:
        # Not well-formed XML (e.g. the model broke a tag), so its tags cannot
        # be mapped. Export the raw string as text: the reviewer sees exactly
        # what the model wrote, and the qa note says what is wrong.
        elem.text = segment
    else:
        _copy_inline(root, elem, ids)


def _copy_inline(src: ET.Element, dst: ET.Element, ids: dict[ET.Element, str]) -> None:
    """Copy the text and tags under `src` (a parsed segment) into `dst` (XLIFF).

    XLIFF 1.2 has two kinds of inline markup. <bpt>/<ept>/<ph> store the
    original code as escaped text; <g>/<x> replace the code entirely. Our tags
    have no attributes, so the tag name alone is enough to rebuild them: we use
    <g>/<x> and keep the name in ctype. User-defined ctype values must start
    with "x-". We always use "x-<tag>", even where XLIFF has a predefined value
    (such as "bold"), so the import maps it back 1:1 without a lookup table.
    """
    dst.text = src.text                # text before the first tag (entities decoded)
    for child in src:
        attrs = {"id": ids[child], "ctype": "x-" + child.tag}
        if child.text or len(child):
            # A tag around content: <g> keeps the content inside it, so the
            # translator translates the text between the tags. Nesting is kept
            # by the recursion.
            inline = ET.SubElement(dst, "g", attrs)
            _copy_inline(child, inline, ids)
        else:
            # A tag with nothing inside: <x/> is a standalone placeholder.
            inline = ET.SubElement(dst, "x", attrs)
        inline.tail = child.tail       # text after the tag belongs to the parent


# ------------------------------------------------------------------ import

def read_xliff(path, *, content_type: str = "doc") -> tuple[list[Unit], dict[str, str]]:
    """Read an XLIFF 1.2 file.

    Returns the Units and {unit_id: target_text} for the trans-units whose
    <target> is not empty. content_type says how to read <source>/<target>:
    "doc" rebuilds the DITA tags, "ui" returns plain text.
    """
    units = []
    targets = {}
    root = ET.parse(path).getroot()
    # "{*}" matches the tag in any namespace or none, so a file that forgot the
    # XLIFF namespace still loads.
    for trans_unit in root.iterfind(".//{*}trans-unit"):
        unit_id = trans_unit.get("id")
        source = trans_unit.find("{*}source")
        if source is None:
            raise ValueError(f"trans-unit {unit_id!r} has no <source>")

        # Only a limit in characters can be checked with len(); maxwidth in
        # pixels (the XLIFF 1.2 default size-unit) is ignored.
        max_length = None
        if trans_unit.get("maxwidth") and trans_unit.get("size-unit") == "char":
            max_length = int(trans_unit.get("maxwidth"))

        # Context notes: from="developer", or no "from" at all (many tools omit
        # it). Notes from "qa" are our own reasons from a previous export.
        context = [note.text or "" for note in trans_unit.findall("{*}note")
                   if note.get("from", "developer") == "developer"]

        units.append(Unit(
            id=unit_id,
            source=_read_segment(source, content_type),
            content_type=content_type,
            note="\n".join(context) or None,
            max_length=max_length,
        ))

        target = trans_unit.find("{*}target")
        if target is not None:
            text = _read_segment(target, content_type)
            if text.strip():           # an empty <target/> is not a translation
                targets[unit_id] = text
    return units, targets


def _read_segment(elem: ET.Element, content_type: str) -> str:
    """The <source>/<target> content as a pipeline string."""
    if content_type == "ui":
        return "".join(elem.itertext())   # plain text, "&" stays "&"
    return _segment_from_inline(elem)


def _segment_from_inline(elem: ET.Element) -> str:
    """Rebuild the dataset string from XLIFF inline markup (reverse of _copy_inline).

    Text is escaped the dataset way: only & < > become &amp; &lt; &gt;
    (saxutils.escape does exactly these three).
    """
    parts = [escape(elem.text or "")]
    for child in elem:
        kind = _local_name(child.tag)
        ctype = child.get("ctype", "")
        if kind not in ("g", "x") or not ctype.startswith("x-"):
            # We only write <g>/<x> with ctype "x-<tag>". Anything else (e.g.
            # <bpt>, or ctype="bold") cannot be turned back into the original
            # tag, so fail loudly instead of silently dropping a tag.
            raise ValueError(f"Unsupported inline element <{kind} ctype={ctype!r}>")
        tag = ctype.removeprefix("x-")
        if kind == "g":
            parts.append(f"<{tag}>{_segment_from_inline(child)}</{tag}>")
        else:
            parts.append(f"<{tag}/>")
        parts.append(escape(child.tail or ""))
    return "".join(parts)


def _local_name(tag: str) -> str:
    # ElementTree names a namespaced tag "{urn:...}g"; keep only "g".
    return tag.rsplit("}", 1)[-1]
