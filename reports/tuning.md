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

## Open risk

v3 reports no major errors on the tune set, so the verifier may now be too
lenient. The human spot-check of auto-approved golden segments measures this.
