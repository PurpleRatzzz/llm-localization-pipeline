"""Tests for the deterministic QA gate.

Each check gets at least one passing and one failing case. Strings marked
"dev NNNN" are copied from the dev set (salesforce/localization-xml-mt,
segment enzh_dev_000000NNNN); the rest are minimal hand-written cases.
"""

from l10n_pipeline.qa_gate import check_segment, find_terms, passed
from l10n_pipeline.units import GlossaryEntry


def checks(issues, severity):
    """The check names that fired with the given severity."""
    return [issue.check for issue in issues if issue.severity == severity]


# ------------------------------------------------------- a clean real pair

def test_real_reference_passes_with_no_issues():
    # dev 0004: two <codeph> kept verbatim, spaces around Latin text
    source = ("If set to <codeph>true</codeph> and the user is logged in and has previously "
              "approved the <codeph>client_id</codeph>, Salesforce skips the approval step.")
    target = ("如果设为 <codeph>true</codeph>,且用户登录且之前批准了 <codeph>client_id</codeph>,"
              "则 Salesforce 跳过批准步骤。")
    assert check_segment(source, target) == []


# ----------------------------------------------------------- xml_wellformed

def test_malformed_target_is_an_error():
    issues = check_segment("Enable <b>Chatter</b>.", "启用 <b>Chatter</i>。")
    assert checks(issues, "error") == ["xml_wellformed"]
    assert "mismatched tag" in issues[0].message
    assert not passed(issues)


def test_malformed_source_is_only_a_warning_and_skips_tag_checks():
    issues = check_segment("Enable <b>Chatter.", "启用 <b>Chatter。")
    assert checks(issues, "warning") == ["xml_wellformed"]
    assert checks(issues, "error") == []


def test_html_entity_nbsp_does_not_break_parsing():
    issues = check_segment("Click&nbsp;<uicontrol>Save</uicontrol>.",
                           "单击&nbsp;<uicontrol>保存</uicontrol>。")
    assert issues == []


# ------------------------------------------------------------------ tag_set

def test_missing_tag_is_an_error():
    issues = check_segment("Click <uicontrol>Save</uicontrol>.", "单击保存。")
    assert checks(issues, "error") == ["tag_set"]
    assert issues[0].message == "Tag <uicontrol> missing in target: source has 1, target has 0"


def test_extra_tag_is_an_error():
    issues = check_segment("Click Save.", "单击<b>保存</b>。")
    assert checks(issues, "error") == ["tag_set"]
    assert "extra in target" in issues[0].message


def test_tag_set_ignores_translated_ui_label():
    # <uicontrol> text is a UI label: it is translated, not protected.
    assert passed(check_segment("Click <uicontrol>Save</uicontrol>.",
                                "单击<uicontrol>保存</uicontrol>。"))


# -------------------------------------------------------------- tag_nesting

def test_nested_tags_kept_pass():
    # dev 0183: <uicontrol> nested inside <menucascade>
    source = ("To run all tests from the Developer Console, select <menucascade><uicontrol>Test"
              "</uicontrol><uicontrol>Run All</uicontrol></menucascade>.")
    target = ("要从开发人员控制台运行所有测试,请选择<menucascade><uicontrol>测试</uicontrol>"
              "<uicontrol>全部运行</uicontrol></menucascade>。")
    assert check_segment(source, target) == []


def test_tag_moved_out_of_its_parent_is_an_error():
    # dev 1539: the reference moved a <uicontrol> out of the <menucascade>
    source = ("click <menucascade><uicontrol><uicontrol>Sales Cloud</uicontrol></uicontrol>"
              "<uicontrol>#URL2#</uicontrol></menucascade>.")
    target = ("单击 <uicontrol><uicontrol>Sales Cloud</uicontrol></uicontrol><menucascade>"
              "<uicontrol>#URL2#</uicontrol></menucascade>。")
    issues = check_segment(source, target)
    # Two (tag, parent) pairs changed: one fewer inside <menucascade>, one more at top level.
    assert checks(issues, "error") == ["tag_nesting", "tag_nesting"]
    messages = [issue.message for issue in issues]
    assert "Tag <uicontrol> inside <menucascade>: source has 2, target has 1" in messages
    assert "Tag <uicontrol> at top level: source has 0, target has 1" in messages


# ---------------------------------------------------------------- tag_order

def test_reordered_tags_are_only_a_warning():
    # dev 0925: Chinese puts "in the Quick Find box" before "enter X"
    source = ("From Setup, enter <userinput>Workflow Rules</userinput> in the <parmname>Quick Find"
              "</parmname> box, then select <uicontrol>Workflow Rules</uicontrol>.")
    target = ("从“设置”中,在<parmname>快速查找</parmname>框中输入<userinput>工作流规则</userinput>,"
              "然后选择<uicontrol>工作流规则</uicontrol>。")
    issues = check_segment(source, target)
    assert checks(issues, "warning") == ["tag_order"]
    assert checks(issues, "error") == []
    assert passed(issues)


# -------------------------------------------------------- protected_content

def test_translated_codeph_is_an_error():
    # dev 0808 (shortened): the HTML tag name "body" was translated as 正文
    source = "redefine the formatting of HTML tags such as <codeph>body</codeph> or <codeph>h1</codeph>."
    target = "重新定义 HTML 标记的格式,如<codeph>正文</codeph>或 <codeph>h1</codeph>。"
    issues = check_segment(source, target)
    assert checks(issues, "error") == ["protected_content"]
    assert issues[0].message == 'Tag <codeph> content changed: "body" missing in target'


def test_nested_protected_tags_kept_pass():
    # dev 1379: <varname> inside <codeph>, plus a reordered <varname> (warning only)
    source = ("<codeph>HOUR(<varname>time</varname>)</codeph> and replace <varname>time</varname> "
              "with a time value or value such as <codeph>TIMENOW()</codeph>.")
    target = ("<codeph>HOUR(<varname>time</varname>)</codeph>,并使用时间值或 <codeph>TIMENOW()</codeph> "
              "等值替换 <varname>time</varname>。")
    issues = check_segment(source, target)
    assert checks(issues, "error") == []
    assert checks(issues, "warning") == ["tag_order"]


def test_translated_varname_is_an_error():
    issues = check_segment("Replace <varname>field</varname> with a name.",
                           "将 <varname>字段</varname> 替换为名称。")
    assert checks(issues, "error") == ["protected_content"]


# --------------------------------------------------------- escaped_literals

def test_escaped_script_tag_kept_passes():
    # dev 0449 (shortened): &lt;script&gt; is text, not a tag
    source = "When saving, all <codeph>&lt;script&gt;</codeph> tags are removed."
    target = "保存时,将删除所有 <codeph>&lt;script&gt;</codeph> 标记。"
    assert check_segment(source, target) == []


def test_unescaped_script_tag_in_target_is_an_error():
    # The model wrote a real "<script>" tag instead of the escaped text.
    source = "All <codeph>&lt;script&gt;</codeph> tags are removed."
    target = "将删除所有 <codeph><script></codeph> 标记。"
    issues = check_segment(source, target)
    # Not parseable, and the &lt; / &gt; counts no longer match either.
    assert checks(issues, "error") == ["xml_wellformed", "escaped_literals", "escaped_literals"]


def test_dropped_escaped_lt_is_an_error():
    issues = check_segment("Use CloseDate &lt; TODAY() here.", "在此处使用 CloseDate TODAY()。")
    assert "escaped_literals" in checks(issues, "error")


def test_ampersand_written_as_he_is_only_a_warning():
    # dev 1050: "&" rendered as 和, which is a correct translation
    issues = check_segment("In the Dataflows &amp; Recipes tab, click the Dataset Recipes subtab.",
                           "在“数据流和模式”选项卡中,单击“数据集模式”子选项卡。")
    assert checks(issues, "warning") == ["escaped_literals"]
    assert passed(issues)


# ------------------------------------------------------------------ numbers

def test_changed_number_is_a_warning():
    issues = check_segment("You can store up to 10 tags.", "您最多可以存储 100 个标记。")
    assert checks(issues, "warning") == ["numbers", "numbers"]
    assert passed(issues)


def test_same_numbers_pass():
    # dev 0969 (shortened)
    assert check_segment("seconds since 1970 (midnight UTC)", "自 1970 年(午夜 UTC)以来的秒数") == []


def test_numbers_inside_protected_tags_and_placeholders_are_ignored():
    # The "2" in the codeph and the "1" in #URL1# are checked by other rules.
    issues = check_segment("Call <codeph>v2</codeph> at #URL1#.", "在 #URL1# 调用 <codeph>v2</codeph>。")
    assert issues == []


# ------------------------------------------------------------- placeholders

def test_missing_brace_placeholder_is_an_error():
    issues = check_segment("{0} records deleted", "已删除记录", content_type="ui")
    assert "placeholders" in checks(issues, "error")
    assert "Placeholder {0} missing in target" in issues[0].message


def test_brace_placeholders_kept_in_any_order_pass():
    assert check_segment("{0} of {1} done", "{1} 中的 {0} 已完成", content_type="ui") == []


def test_missing_url_mask_is_an_error():
    issues = check_segment("See #URL1# for details.", "有关详细信息,请参阅文档。")
    assert checks(issues, "error") == ["placeholders"]


# -------------------------------------------------------------- terminology

GLOSSARY = [
    GlossaryEntry("Account", "客户"),
    # Made-up target that shares no characters with 客户, so the test can tell
    # whether "Account" was (wrongly) enforced inside "Person Account".
    GlossaryEntry("Person Account", "个人帐户"),
    GlossaryEntry("Case", "个案"),
    GlossaryEntry("Opportunity", "业务机会"),
    GlossaryEntry("Key", "密钥"),
    GlossaryEntry("Territory", "区域", kind="ambiguous", note="also 地区 in maps"),
]


def test_required_term_missing_is_an_error():
    issues = check_segment("Open the Account.", "打开帐户。", glossary=GLOSSARY)
    assert checks(issues, "error") == ["terminology"]
    assert issues[0].message == 'Term "Account" must be translated as "客户", not found in target'


def test_required_term_present_passes():
    assert check_segment("Open the Account.", "打开客户。", glossary=GLOSSARY) == []


def test_plural_term_matches():
    issues = check_segment("Close the Cases.", "关闭案例。", glossary=GLOSSARY)
    assert checks(issues, "error") == ["terminology"]


def test_y_to_ies_plural_matches():
    # "Opportunity" must also be found as "Opportunities"
    issues = check_segment("Close your Opportunities.", "关闭您的机会。", glossary=GLOSSARY)
    assert checks(issues, "error") == ["terminology"]
    assert check_segment("Close your Opportunities.", "关闭您的业务机会。", glossary=GLOSSARY) == []


def test_vowel_y_plural_uses_plain_s():
    # "Key" ends in vowel + y, so its plural is "Keys" (via +s), never "Kies".
    assert [e.source for e in find_terms("Rotate the Keys.", GLOSSARY)] == ["Key"]
    assert find_terms("Rotate the Kies.", GLOSSARY) == []


def test_ambiguous_term_is_not_enforced():
    assert check_segment("Assign the Territory.", "分配地区。", glossary=GLOSSARY) == []


def test_longest_match_person_account_consumes_account():
    # Only "Person Account" applies; "Account" must not demand 客户 here.
    assert check_segment("Convert the Person Account.", "转换个人帐户。", glossary=GLOSSARY) == []


def test_matching_is_case_sensitive():
    # "in this case" is an ordinary word (情况), not the Case object (个案).
    assert check_segment("In this case, save the record.", "在这种情况下,保存记录。",
                         glossary=GLOSSARY) == []


def test_code_like_references_are_not_terms():
    # Found in a pipeline run (dev 0025): the gate demanded 个案 inside
    # "[Case].Id" and the edit stage broke the field reference to "[个案].Id".
    for source in ["Select <uicontrol>[Case].Id</uicontrol>.", "Use {!Case.Id} here.",
                   "Map Case.Subject to the field.", "Use $Case in formulas."]:
        assert find_terms(source, GLOSSARY) == [], source
    # A sentence-final period is not a code reference.
    assert [e.source for e in find_terms("Open the Case.", GLOSSARY)] == ["Case"]


def test_term_inside_protected_tag_is_ignored():
    source = "Query the <codeph>Account</codeph> table."
    target = "查询 <codeph>Account</codeph> 表。"
    assert check_segment(source, target, glossary=GLOSSARY) == []


def test_find_terms_returns_any_kind_longest_first():
    found = find_terms("The Person Account and the Account are in one Territory.", GLOSSARY)
    assert [e.source for e in found] == ["Person Account", "Territory", "Account"]


# ------------------------------------------------------------------- length

def test_ui_string_over_max_length_is_an_error():
    issues = check_segment("Save", "保存所有更改", max_length=4, content_type="ui")
    assert checks(issues, "error") == ["length"]
    assert issues[0].message == "Target is 6 characters, max_length is 4"


def test_ui_string_within_max_length_passes():
    assert check_segment("Save", "保存", max_length=4, content_type="ui") == []


def test_doc_target_much_shorter_than_source_is_a_warning():
    # dev 0342: "page layout" was dropped from the reference
    issues = check_segment("Edit the page layout", "编辑")
    assert checks(issues, "warning") == ["length"]
    assert passed(issues)


def test_doc_normal_length_passes():
    # dev 0001
    assert check_segment("Enable Chatter before you enable link previews.",
                         "在启用链接预览之前启用 Chatter。") == []


# -------------------------------------------------------- cjk_latin_spacing

def test_missing_space_between_chinese_and_latin_is_a_warning():
    # dev 1074 (shortened)
    issues = check_segment("Use the Lightning App Builder.", "使用 Lightning应用程序生成器。")
    assert checks(issues, "warning") == ["cjk_latin_spacing"]
    assert '"g应"' in issues[0].message


def test_no_space_next_to_a_tag_is_not_flagged():
    # The adjacency crosses a tag boundary, which the convention does not cover.
    assert check_segment("Enter <userinput>Chatter</userinput>.",
                         "输入<userinput>Chatter</userinput>。") == []


# ----------------------------------------------------- content_type "ui"

def test_ui_strings_are_plain_text_not_xml():
    # A literal "&" in a Custom Label is fine; it is not an XML fragment.
    assert check_segment("Terms & Conditions", "条款和条件", content_type="ui") == []


# ------------------------------------------------------------------- passed

def test_passed_allows_warnings_but_not_errors():
    from l10n_pipeline.units import Issue
    assert passed([])
    assert passed([Issue("numbers", "warning", "x")])
    assert not passed([Issue("numbers", "warning", "x"), Issue("tag_set", "error", "y")])


# ------------------------------------------------------------- untranslated

def test_copied_english_prose_is_an_error():
    source = "Enable Chatter before you enable link previews."
    assert checks(check_segment(source, source), "error") == ["untranslated"]


def test_identifier_left_in_english_passes():
    # dev 1563: an identifier is correctly kept as is.
    assert check_segment("file_not_found", "file_not_found") == []


def test_product_name_left_in_english_passes():
    # No lowercase prose words, so keeping it in English is allowed.
    source = "<xref>Microsoft Active Directory Federation Services 2.0</xref>."
    assert check_segment(source, source) == []
