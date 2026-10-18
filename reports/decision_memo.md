# Decision memo: which pipeline for help documentation (EN→ZH)

Author: Yanlin Chen · 2026-10-18 · Personal project on Salesforce's public
localization dataset, not a production system.

## Question

Which pipeline stages and which kinds of context are worth their cost for
help-center documentation, and where should human review go?

## What was compared

Six configurations, all `claude-sonnet-5-5` at effort `low`, on 200 golden
segments (`reports/results.md`), plus a blind human review of 30 segments
(`reports/human_review.md`).

| Config | What the model gets | chrF [95% CI] | UI labels / terms | Routed to review | $ / 1k words |
|---|---|---|---|---:|---:|
| A | source only | 57.1 [54.0, 60.4] | 77% / 89% | 6.5% | $0.063 |
| B | + style rules, glossary | 57.6 [54.4, 60.9] | 100% / 98% | 0.5% | $0.077 |
| C | B + AI edit + AI verify | 57.9 [54.6, 61.2] | 100% / 100% | 2.0% | $0.232 |
| D | B + TM fuzzy matches | 61.3 [57.8, 64.9] | 100% / 96% | 1.0% | $0.084 |
| **E** | **D + TM concordance** | **67.3 [63.7, 71.2]** | **100% / 100%** | **0%** | **$0.121** |
| F | E + AI verify | 67.3 [63.7, 71.2] | 100% / 100% | 1.0% | $0.236 |

## Findings

1. **Context matters more than the model or extra stages.** E is +10.3 chrF
   over A (95% CI +8.0 to +12.9). On the tune set, switching B from Sonnet 5.5
   to Opus 5.5 added only 0.4 chrF at twice the cost.
2. **Glossary and style rules buy consistency, not chrF.** B is +0.5 chrF over
   A (not significant), but UI labels went from 77% to 100% correct and
   routing to review from 6.5% to 0.5%, for $0.014 more per 1k words.
3. **Translation memory is what teaches the house wording.** Fuzzy matches
   lifted the 80 segments that have one by 16.2 chrF but did nothing for the
   other 120. Concordance search (earlier translations of a segment's key
   phrases) brings house terms such as 会计年度 and 批准者 to every segment.
4. **The AI edit stage is not worth it for this content.** C costs 3x B for
   +0.3 chrF and changed 7 of 200 drafts. In the blind review the reviewer
   preferred C 10 times and E 12 times (8 ties, p = 0.83); neither had a major
   error among 30 auto-approved segments.
5. **The AI verifier is precise but expensive here.** Every segment that
   configurations B-D routed had a real problem (7 of 7, human-confirmed). On
   top of E, the verifier doubles the cost because it reads E's long TM
   context, routes 2 of 200 segments, and misses 2 known minor issues.

## Recommendation

- **Default for help documentation: E + the deterministic QA gate.** No AI
  edit stage.
- **Spend human review on sampling, not on an AI verifier.** Among 30
  auto-approved E segments: 0 major and 5 minor (17%) problems. Zero in 30
  only bounds the major rate below roughly 10%, so keep sampling (e.g. 10-15%
  of each release) until the bound is tight enough.
- **Re-test the verifier with a short context** (style rules and glossary,
  without the TM examples) before using it for routing. Its precision is
  good; its cost is the problem.
- **Keep the review loop running.** This review found four rule and glossary
  changes for the next iteration:
  - Lowercase "case" → 个案, as guidance.
  - Limit R12 (贵组织) to "your org": the model also applied it to "your package".
  - Reconcile R5 with house practice on sample data.
  - Build the keep-in-English product-name list (R2) from TM statistics.

## Limits

- chrF compares with one reference, so it measures closeness to Salesforce's
  existing wording, not overall quality. The human review covers 30 segments.
- The dataset has been public since 2019, so the model may have seen it. This
  can inflate absolute scores but affects every configuration alike.
- Two reviewer style decisions (账户 for login accounts, 请 in instructions)
  deliberately differ from most references and cost about 0.4 chrF.
