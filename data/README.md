# Data

## Source

EN→ZH split of [salesforce/localization-xml-mt](https://github.com/salesforce/localization-xml-mt)
(`data/enzh/`). The segments come from the Salesforce online help documentation, translated and
maintained by human professionals, and aligned from Salesforce's internal parallel XML files (not
web-crawled). Segments keep their inline DITA-style tags (`<uicontrol>`, `<ph>`, `<xref>`, ...).

Citation:

> Kazuma Hashimoto, Raffaella Buschiazzo, James Bradbury, Teresa Marshall, Richard Socher, and
> Caiming Xiong. 2019. A High-Quality Multilingual Dataset for Structured Documentation Translation.
> In *Proceedings of the Fourth Conference on Machine Translation (WMT 2019), Volume 1: Research
> Papers*, pages 116–127, Florence, Italy. ACL. https://aclanthology.org/W19-5212/

## License

The dataset is licensed under **Creative Commons Attribution-NonCommercial 4.0 International
(CC BY-NC 4.0)** (`data/LICENSE.txt` in the source repo; its README calls it "CC-NC-4"). The repo's
scripts are MIT. This project uses the data only for a non-commercial portfolio, with the
attribution above. `golden.jsonl` and `tune.jsonl` contain unmodified dataset segments, so they
remain under CC BY-NC 4.0.

## Files

| File | In git | Contents |
|---|---|---|
| `raw/*.json` | no | original dataset files (downloaded if missing) |
| `raw/train.jsonl` | no | all 97,730 aligned train pairs `{id, source, reference}`, for terminology extraction |
| `golden.jsonl` | yes | 200 evaluation segments |
| `tune.jsonl` | yes | 100 prompt-tuning segments, disjoint from golden |

Fields in `golden.jsonl` / `tune.jsonl`: `id`, `source`, `reference`, `has_tags`, `nested`,
`n_words`, `length_bucket`, `max_train_sim`. `source` and `reference` are byte-for-byte as in the
dataset: tags as raw `<tag>`, literal `< > &` escaped as `&lt; &gt; &amp;`.

## Method

The test split ships English sources only (no public references), so both sets are sampled from
**dev** (2,000 pairs). Train is used only for the near-duplicate check and terminology.

1. **Exclude** dev pairs that are
   - empty (source or reference);
   - not well-formed XML (source or reference wrapped in a root element and parsed with
     `xml.etree.ElementTree`);
   - **near-duplicates of train**: `max_train_sim` = best `rapidfuzz.fuzz.ratio` against any train
     source, on normalized text (tags stripped, lowercased, whitespace collapsed), rounded. A segment
     with `max_train_sim >= 95` would be a translation-memory match in production, not an MT job.
2. **Features**: `has_tags` = the parsed source has any element; `nested` = some element contains
   another element. `n_words` = whitespace-separated words of the tag-stripped source;
   `length_bucket` = short (≤5), medium (6–25), long (≥26).
3. **Sample** with `random.Random(42)`:
   - golden: 100 tagged (20 nested + 80 non-nested) + 100 untagged;
   - tune: 50 tagged + 50 untagged, drawn from what golden left (natural nested mix).

   Tagged segments are oversampled on purpose (50% vs 25.9% in dev) because tags are where
   pipelines break. Report tagged and untagged scores separately; the overall golden score is not
   representative of the natural dev mix. Length buckets are reported, not forced. Files are
   sorted by `id`.

## Statistics (actual run)

- Train: 97,730 pairs (every ID present in both files, none empty).
- Dev: 2,000 pairs, 518 with tags (25.9%).
- Near-duplicates: 164 with `max_train_sim >= 95`, of which 18 are exact (100) after normalization.
- Empty: 0. Unparseable: 0.
- Eligible pool: 1,836 (474 tagged incl. 36 nested; 1,362 untagged).

| Set | Segments | Tagged (nested) | Untagged | Short | Medium | Long |
|---|---|---|---|---|---|---|
| golden | 200 | 100 (20) | 100 | 50 | 105 | 45 |
| tune | 100 | 50 (2) | 50 | 22 | 50 | 28 |

## Notes for the QA gates

- Only three entities occur: `&lt;`, `&gt;`, `&amp;`. Tags never have attributes and are never
  self-closing.
- Every reference has the same multiset of tags as its source (all of train and dev), but the
  order can differ (14 of 518 tagged dev pairs), so compare tag sets, not sequences.
- The ZH references look NFKC-normalized: full-width `，：；（）！？` never occur; ASCII `, : ; ( ) ! ?`
  are used instead, while `。、“”` stay full-width. Normalize punctuation before comparing model
  output with references.
- 69 dev pairs have reference identical to source (identifiers, code); golden has 4, tune has 5.

## Regenerate

From the repo root (downloads missing raw files; takes ~30 s, mostly the near-duplicate check):

```
.venv/bin/python -I scripts/prepare_data.py
```

The same seed and inputs produce byte-identical output files.
