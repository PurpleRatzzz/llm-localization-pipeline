"""Tests for the translation memory lookup and how matches reach the prompt."""

from l10n_pipeline.context import build_context
from l10n_pipeline.tm import TranslationMemory
from l10n_pipeline.units import Unit

TM = TranslationMemory([
    ("Click <uicontrol>Save</uicontrol> to save the report.", "单击<uicontrol>保存</uicontrol>保存报表。"),
    ("Click Save to save the dashboard.", "单击“保存”保存仪表板。"),
    ("Enable Chatter for your org.", "为您的组织启用 Chatter。"),
])


def test_fuzzy_matches_best_first_and_ignore_tags():
    matches = TM.lookup("Click <uicontrol>Save</uicontrol> to save the reports.")
    assert [m["target"] for m in matches][0] == "单击<uicontrol>保存</uicontrol>保存报表。"
    assert matches[0]["score"] >= matches[-1]["score"] >= 75


def test_no_match_below_threshold():
    assert TM.lookup("Delete the old knowledge articles from the archive.") == []


def test_tm_appears_in_context_only_when_given():
    unit = Unit("s1", "Click Save to save the report.")
    with_tm = build_context([unit], [], "R1.", {"s1": TM.lookup(unit.source)})
    without = build_context([unit], [], "R1.")
    assert "## Translation memory" in with_tm and "单击“保存”保存仪表板。" in with_tm
    assert "Translation memory" not in without
