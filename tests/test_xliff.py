"""Tests for XLIFF 1.2 export and import.

The round-trip tests use every segment of data/golden.jsonl and
data/tune.jsonl; the rest are minimal hand-written cases.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from l10n_pipeline.units import Issue, Result, Unit
from l10n_pipeline.xliff import XLIFF_NS, read_xliff, write_xliff

DATA = Path(__file__).resolve().parent.parent / "data"
NS = {"x": XLIFF_NS}   # prefix for ElementTree find() calls


def load_rows(filename):
    lines = (DATA / filename).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def write_one(tmp_path, source, target, status="needs_review", issues=(), **unit_fields):
    """Write a single unit; return (path, parsed <trans-unit>)."""
    path = tmp_path / "one.xlf"
    unit = Unit(id="u1", source=source, **unit_fields)
    write_xliff([unit], [Result("u1", source, target, status, list(issues))], path)
    return path, ET.parse(path).getroot().find(".//x:trans-unit", NS)


def inline(elem):
    """[(element kind, id, ctype)] for every <g>/<x> inside elem, in document order."""
    return [(child.tag.split("}")[1], child.get("id"), child.get("ctype"))
            for child in elem.iter() if child is not elem]


def round_trip(tmp_path, source, target, **unit_fields):
    """Write one unit, read it back; return (unit, target or None)."""
    path, _ = write_one(tmp_path, source, target, **unit_fields)
    units, targets = read_xliff(path, content_type=unit_fields.get("content_type", "doc"))
    return units[0], targets.get("u1")


# ------------------------------------------------- real data round trip

@pytest.mark.parametrize("filename", ["golden.jsonl", "tune.jsonl"])
def test_every_dataset_segment_round_trips_byte_identical(tmp_path, filename):
    rows = load_rows(filename)
    units = [Unit(id=row["id"], source=row["source"]) for row in rows]
    results = [Result(row["id"], row["source"], row["reference"], "needs_review")
               for row in rows]
    path = tmp_path / "data.xlf"
    write_xliff(units, results, path)

    units_back, targets = read_xliff(path)

    assert len(rows) == {"golden.jsonl": 200, "tune.jsonl": 100}[filename]
    assert [unit.id for unit in units_back] == [row["id"] for row in rows]
    mismatches = []
    for row, unit in zip(rows, units_back):
        if unit.source != row["source"]:
            mismatches.append((row["id"], "source"))
        if targets.get(unit.id) != row["reference"]:
            mismatches.append((row["id"], "reference"))
    assert mismatches == []


def test_golden_target_tags_reuse_the_source_ids(tmp_path):
    # Every golden reference has the same tags as its source (data/README.md),
    # so every target <g> must carry the id and ctype of a source <g>.
    rows = [row for row in load_rows("golden.jsonl") if row["has_tags"]]
    path = tmp_path / "golden.xlf"
    write_xliff([Unit(row["id"], row["source"]) for row in rows],
                [Result(row["id"], row["source"], row["reference"], "needs_review")
                 for row in rows], path)

    trans_units = ET.parse(path).getroot().findall(".//x:trans-unit", NS)
    assert len(trans_units) == 100
    for trans_unit in trans_units:
        source = sorted(inline(trans_unit.find("x:source", NS)))
        target = sorted(inline(trans_unit.find("x:target", NS)))
        assert source and target == source, trans_unit.get("id")


# --------------------------------------------------- file structure

def test_file_is_wellformed_xliff_12(tmp_path):
    path = tmp_path / "out.xlf"
    units = [Unit("a", "One"), Unit("b", "Two")]
    # Results decide the order of the trans-units, not units.
    results = [Result("b", "Two", "二", "needs_review"),
               Result("a", "One", "一", "auto_approved")]
    write_xliff(units, results, path, original="help.xml")

    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("<?xml version='1.0' encoding='utf-8'?>")
    root = ET.fromstring(raw.encode("utf-8"))      # raises if not well-formed
    assert root.tag == f"{{{XLIFF_NS}}}xliff"
    assert root.get("version") == "1.2"
    # Every element, down to <note>, is in the XLIFF 1.2 namespace.
    assert all(elem.tag.startswith(f"{{{XLIFF_NS}}}") for elem in root.iter())

    files = root.findall("x:file", NS)
    assert len(files) == 1
    assert files[0].attrib == {"original": "help.xml", "source-language": "en-US",
                               "target-language": "zh-CN", "datatype": "xml"}
    ids = [tu.get("id") for tu in files[0].findall("x:body/x:trans-unit", NS)]
    assert ids == ["b", "a"]
    assert "\n      <trans-unit" in raw            # pretty-printed


def test_state_values(tmp_path):
    _, review = write_one(tmp_path, "One", "一", status="needs_review")
    assert review.find("x:target", NS).get("state") == "needs-review-translation"
    _, approved = write_one(tmp_path, "One", "一", status="auto_approved")
    assert approved.find("x:target", NS).get("state") == "translated"


def test_qa_notes_with_priorities(tmp_path):
    issues = [Issue("tag_set", "error",
                    "Tag <codeph> missing in target: source has 1, target has 0"),
              Issue("numbers", "warning", 'Number "3" appears 1x in source, 0x in target')]
    path, trans_unit = write_one(tmp_path, "Use <codeph>x</codeph> 3 times.", "使用 x。",
                                 issues=issues)

    notes = trans_unit.findall("x:note", NS)
    assert [note.attrib for note in notes] == [
        {"from": "qa", "priority": "1", "annotates": "target"},
        {"from": "qa", "priority": "5", "annotates": "target"},
    ]
    assert notes[0].text == ("[error] tag_set: Tag <codeph> missing in target: "
                             "source has 1, target has 0")
    assert notes[1].text == '[warning] numbers: Number "3" appears 1x in source, 0x in target'
    # Notes come after <target>, as the XLIFF 1.2 schema requires.
    assert [child.tag.split("}")[1] for child in trans_unit] == ["source", "target",
                                                                 "note", "note"]
    # Our own qa notes are not read back as context.
    units, _ = read_xliff(path)
    assert units[0].note is None


def test_developer_note(tmp_path):
    path, trans_unit = write_one(tmp_path, "Save", "保存", note="Button on the Setup page")
    note = trans_unit.find("x:note", NS)
    assert note.attrib == {"from": "developer", "annotates": "general"}
    assert note.text == "Button on the Setup page"
    units, _ = read_xliff(path)
    assert units[0].note == "Button on the Setup page"


def test_maxwidth_and_size_unit(tmp_path):
    _, limited = write_one(tmp_path, "Save", "保存", max_length=40)
    assert limited.get("maxwidth") == "40"
    assert limited.get("size-unit") == "char"
    _, free = write_one(tmp_path, "Save", "保存")
    assert "maxwidth" not in free.attrib and "size-unit" not in free.attrib

    unit, _ = round_trip(tmp_path, "Save", "保存", max_length=40)
    assert unit.max_length == 40


# ------------------------------------------------------- inline tags

def test_nested_tags_become_nested_g(tmp_path):
    source = "See <xref><ph>A <ph>B</ph></ph></xref>."
    target = "请参阅<xref><ph>A <ph>B</ph></ph></xref>。"
    _, trans_unit = write_one(tmp_path, source, target)

    src = trans_unit.find("x:source", NS)
    xref = src.find("x:g", NS)
    outer = xref.find("x:g", NS)
    inner = outer.find("x:g", NS)
    assert (xref.get("id"), xref.get("ctype")) == ("1", "x-xref")
    assert (outer.get("id"), outer.get("ctype"), outer.text) == ("2", "x-ph", "A ")
    assert (inner.get("id"), inner.get("ctype"), inner.text) == ("3", "x-ph", "B")
    assert inline(trans_unit.find("x:target", NS)) == inline(src)

    assert round_trip(tmp_path, source, target)[0].source == source
    assert round_trip(tmp_path, source, target)[1] == target


def test_escaped_literals_round_trip(tmp_path):
    source = "Type &lt;script&gt; &amp; <codeph>a &lt; b</codeph>."
    path, trans_unit = write_one(tmp_path, source, source)
    # Inside the file the text holds the real characters; the serializer escapes them.
    assert trans_unit.find("x:source", NS).text == "Type <script> & "
    assert "Type &lt;script&gt; &amp; <g" in path.read_text(encoding="utf-8")

    unit, target = round_trip(tmp_path, source, source)
    assert unit.source == source and target == source


def test_whitespace_between_tags_survives_indenting(tmp_path):
    source = "<b>A</b> <i>B</i>"
    unit, target = round_trip(tmp_path, source, source)
    assert unit.source == source and target == source


def test_empty_element_becomes_x(tmp_path):
    source = "Line one<ph/>line two"
    _, trans_unit = write_one(tmp_path, source, "第一行<ph/>第二行")
    assert inline(trans_unit.find("x:source", NS)) == [("x", "1", "x-ph")]
    assert round_trip(tmp_path, source, "第一行<ph/>第二行")[0].source == source
    # <ph></ph> is the same XML, so it comes back in the short form.
    assert round_trip(tmp_path, "a<ph></ph>b", "a<ph></ph>b")[0].source == "a<ph/>b"


def test_reordered_target_tags_keep_the_source_ids(tmp_path):
    # Chinese word order can move tags; the ids follow the tags, not positions.
    _, trans_unit = write_one(tmp_path, "Enter <userinput>X</userinput> in <parmname>Q</parmname>.",
                              "在<parmname>Q</parmname>中输入<userinput>X</userinput>。")
    assert inline(trans_unit.find("x:source", NS)) == [("g", "1", "x-userinput"),
                                                       ("g", "2", "x-parmname")]
    assert inline(trans_unit.find("x:target", NS)) == [("g", "2", "x-parmname"),
                                                       ("g", "1", "x-userinput")]


def test_target_with_an_extra_tag_gets_a_new_id(tmp_path):
    source = "Click <b>Save</b>."
    target = "单击 <b>保存</b> 和 <b>关闭</b> <i>现在</i>。"
    _, trans_unit = write_one(tmp_path, source, target)
    assert inline(trans_unit.find("x:source", NS)) == [("g", "1", "x-b")]
    # The first <b> matches the source; the second <b> and the <i> are new.
    assert inline(trans_unit.find("x:target", NS)) == [("g", "1", "x-b"), ("g", "2", "x-b"),
                                                       ("g", "3", "x-i")]
    assert round_trip(tmp_path, source, target)[1] == target


def test_malformed_target_is_written_as_plain_text(tmp_path):
    target = "启用 <b>Chatter</i>。"
    _, trans_unit = write_one(tmp_path, "Enable <b>Chatter</b>.", target)
    written = trans_unit.find("x:target", NS)
    assert len(written) == 0 and written.text == target


# --------------------------------------------------------- UI strings

def test_ui_unit_is_plain_text(tmp_path):
    source = "Save & Close {0} <b>"        # never parsed as XML
    target = "保存并关闭 {0} <b>"
    path, trans_unit = write_one(tmp_path, source, target, content_type="ui")
    written = trans_unit.find("x:source", NS)
    assert len(written) == 0 and written.text == source
    assert "<source>Save &amp; Close {0} &lt;b&gt;</source>" in path.read_text(encoding="utf-8")

    unit, target_back = round_trip(tmp_path, source, target, content_type="ui",
                                   max_length=30, note="Button label")
    assert (unit.source, target_back) == (source, target)
    assert (unit.content_type, unit.max_length, unit.note) == ("ui", 30, "Button label")


# ------------------------------------------------ files from other tools

CUSTOM_LABELS = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
  <file original="CustomLabels" source-language="en-US" target-language="zh-CN"
        datatype="plaintext">
    <body>
      <trans-unit id="Save_Close" maxwidth="20" size-unit="char">
        <source>Save &amp; Close</source>
        <target state="translated">保存并关闭</target>
        <note>Main button of the case page</note>
        <note from="qa" priority="1">[error] length: too long</note>
      </trans-unit>
      <trans-unit id="Greeting" maxwidth="200">
        <source>Hello {0}</source>
        <target/>
        <note from="developer">{0} is the first name</note>
        <note from="reviewer">Checked</note>
      </trans-unit>
    </body>
  </file>
</xliff>
"""


def test_read_custom_labels_file(tmp_path):
    path = tmp_path / "labels.xlf"
    path.write_text(CUSTOM_LABELS, encoding="utf-8")
    units, targets = read_xliff(path, content_type="ui")

    assert [(u.id, u.source, u.content_type) for u in units] == [
        ("Save_Close", "Save & Close", "ui"), ("Greeting", "Hello {0}", "ui")]
    # A note without "from" is context; "qa" and "reviewer" notes are not.
    assert [u.note for u in units] == ["Main button of the case page", "{0} is the first name"]
    # No size-unit means pixels (the XLIFF 1.2 default), which len() cannot check.
    assert [u.max_length for u in units] == [20, None]
    # The empty <target/> is not a translation.
    assert targets == {"Save_Close": "保存并关闭"}


def test_file_without_namespace_still_loads(tmp_path):
    path = tmp_path / "plain.xlf"
    path.write_text(CUSTOM_LABELS.replace(f' xmlns="{XLIFF_NS}"', ""), encoding="utf-8")
    units, targets = read_xliff(path, content_type="ui")
    assert len(units) == 2 and targets == {"Save_Close": "保存并关闭"}


def test_unsupported_inline_element_is_an_error(tmp_path):
    path = tmp_path / "bpt.xlf"
    path.write_text(f'<xliff version="1.2" xmlns="{XLIFF_NS}"><file><body>'
                    '<trans-unit id="1"><source>A <bpt id="1">&lt;b&gt;</bpt>B</source>'
                    '</trans-unit></body></file></xliff>', encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported inline element <bpt"):
        read_xliff(path)
