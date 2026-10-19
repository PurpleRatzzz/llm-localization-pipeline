# Prompt tuning log (tune set, config C)

Prompts and rules were tuned on the 100-segment **tune** set only, never on
the golden set, so the golden results are not fitted to the test data. Each
version is one full run of config C (translate → edit → verify) with
`claude-sonnet-5-5` at effort `low`. The model accepts no temperature setting,
so the same prompt gives slightly different output on every run: chrF
differences below about 1 point are noise.

| | v1 | v2 | v3 | v4 (final) |
|---|---|---|---|---|
| chrF, translate draft | 61.1 | 62.4 | 61.7 | 63.3 |
| chrF, after edit | 61.2 | 62.7 | 62.0 | 63.3 |
| Segments changed by edit (closer / further from reference) | 6 (2 / 4) | 2 (1 / 1) | 4 (2 / 2) | 3 (1 / 2) |
| Required terms hit, draft → final | 48 → 54 of 56 | 53 → 55 of 56 | 53 → 55 of 56 | 14 → 15 of 16* |
| MQM major errors | 5 | 6 | 0 | 2 |
| Routed to human review | 7 | 6 | 1 | 1 |
| Cost (100 segments) | $0.38 | $0.39 | $0.40 | $0.41 |

\* v4 uses the reviewed two-scope glossary (fewer, stricter entries), so its
term counts are not comparable with v1-v3.

## What changed and why

**v1 → v2**
- *Problem:* the model left the text inside `<uicontrol>`, `<userinput>` and
  `<xref>` in English. The prompt said "keep every tag" and rule R5 only
  mentioned `<uicontrol>`/`<parmname>`.
  *Fix:* R5 now says to translate the text inside every tag except
  `<codeph>`, `<varname>` and `<filepath>`, and the translate prompt says the
  same. Required terms hit in the draft went from 48 to 53.
- *Problem:* the editor "fixed" a numbers **warning** and turned a correct
  `1000 万` back into `10 million`. Calibration on human references shows most
  warnings are false positives (numbers 10/11, tag order 12/12).
  *Fix:* the editor now receives only error-level gate findings.
- *Problem:* the editor added glosses in parentheses and swapped correct words
  for synonyms (点按 → 轻点).
  *Fix:* the edit prompt forbids both. The verifier had flagged the glosses as
  major errors.

**v2 → v3**
- *Problem:* the verifier flagged correct translations because two style rules
  were too absolute: R9 ("keep every number unchanged") against `1000 万`, and
  R5 against the API parameter `provAuthorizeEndpointHost`.
  *Fix:* both rules were reworded.
- *Problem:* the verifier wrote "acceptable" or "no real error" in an
  explanation and still had to choose a severity.
  *Fix:* the explanation field now comes first, and the severity enum gained a
  `not_an_error` value, whose entries are dropped in code.

**v3 → v4** (after the glossary and style review)
- *Changes:* the reviewed glossary (UI-label and running-text scopes,
  closed sets of translations), style rules R11-R13, and term-choice hints
  for the editor.
- *Worked as designed:* a draft invented a `<parmname>` tag that the source
  does not have; the gate reported it and the editor removed it.
- *Problem:* a conflict between the glossary and the style rules. "Connected
  Accounts" are login accounts, so the draft's 关联账户 followed R11, but the
  required glossary entry Account → 客户 made the gate flag it and the editor
  changed it to 关联客户. The verifier caught this as a major terminology error
  and routed the segment to human review.
  *Fix (reviewer decision):* Account in running text became an ambiguous
  closed set 客户|账户 with a note saying when to use which.

**Diagnosis after the golden run: why B and C barely beat A on chrF**

The golden results showed B and C within 1 chrF of A. Inspecting the
lowest-scoring golden segments found no bugs (no empty, misaligned or
truncated outputs). The model's translations were correct but not in
Salesforce's established wording: 财年 for 会计年度 (fiscal year), 审批人 for
批准者 (approver), 架构 for 方案 (schema). Two measurements on the golden runs:

- D's gain came only from segments with a fuzzy match: +16.2 chrF on the 80
  segments with one, +0.9 on the 120 without.
- Wordings the references do not share cost little
  (`reports/style_cost.md`):
  - Deleting 请 where the reference has none (style rule R13) adds 0.4 chrF
    to B.
  - Deleting the extra 您的 adds another 0.2. The model adds 您的 on its own:
    19 in config A, which has no style rules, against 13 in the references.

Two tests on the **tune** set (100 segments, never golden) followed:

| | chrF | 44 with a fuzzy match | 56 without | Cost |
|---|---:|---:|---:|---:|
| B, claude-sonnet-5-5 | 63.2 | 69.2 | 60.5 | $0.14 |
| B, claude-opus-5-5 | 63.6 | 70.2 | 60.6 | $0.27 |
| D (fuzzy matches) | 66.9 | 82.2 | 59.9 | $0.15 |
| E (D + concordance) | 70.4 | 83.0 | 64.7 | $0.22 |

- A stronger model is not the fix: Opus 5.5 adds 0.4 chrF for twice the cost.
- What is missing is the house wording. Concordance search, the CAT-tool
  feature that finds earlier translations of a phrase, gives every segment
  examples (`l10n_pipeline/tm.py`). It lifts the segments without a fuzzy
  match by 4.8 chrF.
- Transparency note: the concordance idea came from reading golden errors.
  The method was then tested on the tune set only, and its two parameters
  (4 examples per segment, phrases in at most 3000 TM entries) were set before
  any run and not tuned.

## Open risk

v3 reports no major errors on the tune set, so the verifier may now be too
lenient. The human spot-check of auto-approved golden segments measures this.
