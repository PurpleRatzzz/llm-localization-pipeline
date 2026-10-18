"""Tests for the XLIFF-in/XLIFF-out interface used by other tools.

None of these tests calls the API: the pipeline is replaced by a stub where a
translation is needed, and the missing-key test fails before any request.
"""

import xml.etree.ElementTree as ET

import pytest

from l10n_pipeline import __main__ as cli
from l10n_pipeline import llm, pipeline
from l10n_pipeline.units import Result
from l10n_pipeline.xliff import read_file_attributes, read_xliff

NS = {"x": "urn:oasis:names:tc:xliff:document:1.2"}

LABELS = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
  <file original="CustomLabels.labels-meta.xml" source-language="en-US" target-language="zh-CN" datatype="xml">
    <body>
      <trans-unit id="Hero_Title">
        <source>Find your dream home</source>
        <note from="context">component: c-hero
ui: heading
maxlen: 12</note>
        <note from="developer">Main heading of the property search page.</note>
        <note from="qa">[error] length: from an earlier run, not context</note>
      </trans-unit>
      <trans-unit id="Save_Button" maxwidth="6" size-unit="char">
        <source>Save</source>
        <note from="context">ui: button
maxlen: 20</note>
      </trans-unit>
    </body>
  </file>
</xliff>
"""


@pytest.fixture
def labels(tmp_path):
    path = tmp_path / "labels.xlf"
    path.write_text(LABELS, encoding="utf-8")
    return path


# (a) context notes ------------------------------------------------------------

def test_context_notes_are_passed_to_the_model(labels):
    units, _ = read_xliff(labels, content_type="ui")
    hero = units[0]
    assert "component: c-hero" in hero.note and "ui: heading" in hero.note
    assert "Main heading of the property search page." in hero.note
    assert "earlier run" not in hero.note          # qa notes are not context


def test_maxlen_marker_is_a_length_limit_unless_maxwidth_is_set(labels):
    hero, save = read_xliff(labels, content_type="ui")[0]
    assert hero.max_length == 12                   # from "maxlen: 12"
    assert save.max_length == 6                    # maxwidth wins over the marker


# (b) target language -------------------------------------------------------------

def test_target_language_accepts_both_chinese_spellings():
    for value in ("zh-CN", "zh_CN", "ZH_cn"):
        assert cli.target_language(value) == "zh-CN"


def test_unsupported_target_language_exits_nonzero(labels, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["translate", "--in", str(labels), "--out", str(tmp_path / "out.xlf"),
                  "--target-lang", "ja"])
    assert exit_info.value.code != 0
    assert "only zh-CN (or zh_CN)" in capsys.readouterr().err


# (c) file attributes ---------------------------------------------------------------

def test_output_keeps_the_input_file_attributes(labels, tmp_path, monkeypatch):
    def fake_run(units, config):   # no API call: echo the source as the target
        results = [Result(u.id, u.source, u.source, "auto_approved") for u in units]
        return results, pipeline.RunStats()
    monkeypatch.setattr(pipeline, "run", fake_run)

    out = tmp_path / "labels.zh_CN.xlf"
    cli.main(["translate", "--in", str(labels), "--out", str(out),
              "--content-type", "ui", "--target-lang", "zh_CN"])
    file_elem = ET.parse(out).getroot().find("x:file", NS)
    assert file_elem.get("original") == "CustomLabels.labels-meta.xml"
    assert file_elem.get("source-language") == "en-US"
    assert file_elem.get("target-language") == "zh-CN"
    assert read_file_attributes(out)["original"] == "CustomLabels.labels-meta.xml"


# (d) missing API key -------------------------------------------------------------------

def test_missing_api_key_exits_nonzero_with_a_clear_message(labels, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "empty-cache")   # force a real request
    with pytest.raises(SystemExit) as exit_info:
        # Config B needs no translation memory, so this works on a fresh checkout.
        cli.main(["translate", "--in", str(labels), "--out", str(tmp_path / "out.xlf"),
                  "--content-type", "ui", "--config", "B"])
    assert exit_info.value.code == 1
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err
