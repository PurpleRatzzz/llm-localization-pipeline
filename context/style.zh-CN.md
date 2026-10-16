# zh-CN Style Rules

Status: DRAFT, derived from the 2000 dev-set human references. Pending review by
the zh-CN reviewer (Yanlin Chen). See "Open questions" at the end.

The `## Rules` section is inserted verbatim into the translate, edit and verify
prompts. The `## Evidence` section is for humans and is not sent to the model.

## Rules

R1. Formality: address the reader as 您, never 你.

R2. Product and feature names stay in English, with no Chinese gloss:
Salesforce, Chatter, Lightning Experience, Salesforce Classic, Apex,
Visualforce, Einstein, AppExchange, Sales Cloud, Service Cloud.
Example: "Enable Chatter" → "启用 Chatter"

R3. Inline tags: keep every tag from the source, with the same count and
nesting. You may move a tag when Chinese word order requires it. Never
translate tag names or attributes.

R4. Code stays verbatim: never translate text inside <codeph>, <varname> or
<filepath>, and keep escaped characters (&lt; &gt; &amp;) exactly as they appear.

R5. Translate the text inside every other tag: <uicontrol>, <parmname> and
<menucascade> are UI labels (use the glossary); <userinput> is what the user
types in the Chinese UI, such as the name of a Setup page; <xref> and <ph> are
link titles and running text. Keep in English only product names (R2), API
and parameter names written as one word (e.g. provAuthorizeEndpointHost) and
literal sample values.
Example: "enter <userinput>Sharing Settings</userinput> in the <parmname>Quick Find</parmname> box"
→ "在<parmname>快速查找</parmname>框中输入<userinput>共享设置</userinput>"
When the English names a UI element without a tag, put the Chinese name in
Chinese quotation marks “”.
Example: "From Setup, enter ..." → "从“设置”中，输入……"

R6. UI actions: "click" → 单击 (not 点击); "select" → 选择.

R7. Spacing: put one half-width space between Chinese characters and Latin
letters or digits.
Example: "启用 Chatter"，"最多 50 个字符"

R8. Punctuation: use standard zh-CN full-width punctuation: ，。：；？！（）、“”.
End with 。 only if the English source ends with a period. Headings and
labels without final punctuation get none.

R9. Numbers and placeholders: keep every placeholder such as {0} unchanged.
Write numbers as digits; large numbers follow Chinese grouping
(10 million → 1000 万).

R10. Faithfulness: do not add, omit or explain content that is not in the source.

## Evidence

Counts are over the 2000 dev-set references (`data/raw/enzh_zh_dev.json`).

| Rule | Evidence |
|---|---|
| R1 | 您 appears in 423 segments, 你 in 0 |
| R2 | Each listed name is kept in English in 100% of the segments where it appears (e.g. Salesforce 214/214, Lightning Experience 55/55) |
| R3 | Tag order differs from the source in 14 of 518 tagged references, all because of Chinese word order |
| R4 | QA gate calibration (`reports/gate_calibration.md`): text kept verbatim in varname 18/18, codeph 58/63, filepath 9/10 |
| R5 | Same calibration: kept verbatim in only 6% of uicontrol, 8% of parmname, 34% of userinput, 48% of ph, i.e. translated in most cases. In the first tune-set run the model left uicontrol, userinput and xref text in English when R5 only mentioned uicontrol and parmname. “” used in 189 segments; "From Setup" → 从“设置”中 |
| R6 | 单击 111 vs 点击 4 segments |
| R7 | Space present in 1777 of 1779 CJK-Latin adjacencies (99.9%), measured by the QA gate calibration (`cjk_latin_spacing`) |
| R8 | All 2000 references are unchanged by Unicode NFKC normalization, and NFKC turns ，（）：；？！ into half-width forms while keeping 。、“”. That matches the references exactly (，0 vs , 1396; 。1865), so the half-width marks come from dataset preprocessing, not from a style choice. Evaluation applies NFKC to both sides. When the English has no terminal punctuation, the reference ends with 。 in only 9 of 774 cases |
| R9 | Placeholders: 42/42 `#URL1#` and 4/4 merge fields kept (QA gate calibration). Numbers: 10 of the 11 references whose digits differ from the source are correct localizations (three → 3, 10 million → 1000 万). The first wording ("keep every number unchanged") made the AI verifier flag a correct 1000 万 as a major error |

## Open questions for the reviewer

1. 帐户 or 账户 for "account" in the sense of a login account? References use
   both (帐户 17, 账户 6).
2. "your org": 贵组织 (23) or 您的组织 (14)? References use both.
3. Should imperative instructions start with 请? References use it in 79
   segments, but not consistently.
