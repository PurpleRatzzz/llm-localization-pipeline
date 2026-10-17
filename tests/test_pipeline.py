"""Tests for pipeline logic that needs no API call."""

from l10n_pipeline.pipeline import term_choices
from l10n_pipeline.units import GlossaryEntry, Unit

GLOSSARY = [
    GlossaryEntry("contact", "联系人|客户", kind="ambiguous", note="客户 only for the business contact"),
    GlossaryEntry("report", "报表"),
    GlossaryEntry("View", "视图|查看", kind="ambiguous", note="视图 for a list view", scope="ui"),
]


def test_ambiguous_choice_from_the_set_goes_to_the_editor():
    unit = Unit("s1", "Add a contact and click <uicontrol>View</uicontrol>.")
    hints = term_choices(unit, "添加联系人，然后单击<uicontrol>查看</uicontrol>。", GLOSSARY)
    assert hints == ['"contact": the draft uses 联系人; options: 联系人 / 客户. 客户 only for the business contact',
                     '"View": the draft uses 查看; options: 视图 / 查看. 视图 for a list view']


def test_required_terms_and_misses_are_not_hints():
    # A required term needs no judgement; a miss is already a gate error.
    unit = Unit("s2", "Share the report with a contact.")
    assert term_choices(unit, "与某人共享报表。", GLOSSARY) == []


def test_guidance_only_term_is_a_hint_even_when_unused():
    guidance = [GlossaryEntry("Community", "社区", kind="ambiguous", note="")]
    hints = term_choices(Unit("s3", "Join the Community forum."), "加入用户论坛。", guidance)
    assert hints == ['"Community": usually 社区, which the draft does not use; '
                     'keep the draft unless 社区 fits better.']
