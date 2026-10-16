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

R4. Code stays verbatim: never translate text inside code-type tags such as
<codeph> and <varname>, and keep escaped characters (&lt; &gt; &amp;) exactly
as they appear.

R5. UI labels: translate text inside <uicontrol> and <parmname> using the
glossary. When the English names a UI element without a tag, put the Chinese
name in Chinese quotation marks “”.
Example: "From Setup, enter ..." → "从“设置”中，输入……"

R6. UI actions: "click" → 单击 (not 点击); "select" → 选择.

R7. Spacing: put one half-width space between Chinese characters and Latin
letters or digits.
Example: "启用 Chatter"，"最多 50 个字符"

R8. Punctuation: use standard zh-CN full-width punctuation: ，。：；？！（）、“”.
End with 。 only if the English source ends with a period. Headings and
labels without final punctuation get none.

R9. Numbers and placeholders: keep every number and every placeholder such
as {0} unchanged.

R10. Faithfulness: do not add, omit or explain content that is not in the source.

## Evidence

Counts are over the 2000 dev-set references (`data/raw/enzh_zh_dev.json`).

| Rule | Evidence |
|---|---|
| R1 | 您 appears in 423 segments, 你 in 0 |
| R2 | Each listed name is kept in English in 100% of the segments where it appears (e.g. Salesforce 214/214, Lightning Experience 55/55) |
| R3 | Tag order differs from the source in 14 of 518 tagged references, all because of Chinese word order |
| R4 | Measured per tag by the QA gate calibration: `reports/gate_calibration.md` |
| R5 | “” used in 189 segments; "From Setup" → 从“设置”中 |
| R6 | 单击 111 vs 点击 4 segments |
| R7 | To be confirmed by the QA gate calibration (`cjk_latin_spacing`) |
| R8 | All 2000 references are unchanged by Unicode NFKC normalization, and NFKC turns ，（）：；？！ into half-width forms while keeping 。、“”. That matches the references exactly (，0 vs , 1396; 。1865), so the half-width marks come from dataset preprocessing, not from a style choice. Evaluation applies NFKC to both sides. When the English has no terminal punctuation, the reference ends with 。 in only 9 of 774 cases |
| R9 | Enforced by the QA gate (`numbers`, `placeholders`) |

## Open questions for the reviewer

1. 帐户 or 账户 for "account" in the sense of a login account? References use
   both (帐户 17, 账户 6).
2. "your org": 贵组织 (23) or 您的组织 (14)? References use both.
3. Should imperative instructions start with 请? References use it in 79
   segments, but not consistently.
