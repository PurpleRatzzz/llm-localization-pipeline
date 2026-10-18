# Human review

Reviewer: Yanlin Chen (zh-CN). Sheet: `reviews/review_sheet.zh-CN.csv`, built by `scripts/make_review_sheet.py`; scored by `scripts/score_review.py`.

## Part 1: segments the pipeline routed to review

7 routed segments (configs B-E). Was the reason a real problem?

| Rating | Segments |
|---|---:|
| ok | 0 |
| minor | 3 |
| major | 4 |

Routing precision: 7 of 7 routed segments had a real problem.

## Part 2: blind comparison of auto-approved segments

30 random segments that both C and E auto-approved, shown as X/Y in random order without the configuration names or the reference.

| Preferred | Segments |
|---|---:|
| C | 10 |
| E | 12 |
| same | 8 |

Sign test (C vs E, ties dropped): two-sided p = 0.83.

Position check: the left column (X) was preferred 16 times and the right column (Y) 6 times (sign test p = 0.05). X was C in 13 pairs and E in 17, assigned at random, so a preference for the left column adds noise but favours neither configuration.

| Config | ok | minor | major | Auto-approved with a problem |
|---|---:|---:|---:|---:|
| C | 24 | 6 | 0 | 6/30 (20%) |
| E | 25 | 5 | 0 | 5/30 (17%) |

Blank ratings in part 2 count as ok (the reviewer marked only problems). With 30 segments, these are indicative counts, not precise rates.
