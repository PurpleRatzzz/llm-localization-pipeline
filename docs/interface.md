# Interface: XLIFF 1.2 in, XLIFF 1.2 out

Other tools call the pipeline with one command. Example files:
[`examples/custom_labels.en.xlf`](../examples/custom_labels.en.xlf) (input) and
[`examples/custom_labels.zh-CN.xlf`](../examples/custom_labels.zh-CN.xlf) (real output).

```
python -m l10n_pipeline translate --in labels.xlf --out labels.zh-CN.xlf --content-type ui
```

| Option | Values | Default |
|---|---|---|
| `--content-type` | `ui`: plain-text UI strings such as Salesforce Custom Labels; `doc`: XML fragments with inline tags | `doc` |
| `--config` | `A` translate only, `B` + style rules and glossary, `C` + edit and verify | `C` |
| `--target-lang` | only `zh-CN` has a glossary and style rules today | `zh-CN` |

Requires `ANTHROPIC_API_KEY` in the environment.

## Input: what the caller puts in the file

| Need | XLIFF 1.2 | Example |
|---|---|---|
| One string | `<trans-unit id="…"><source>…</source>` | `id="Close_Case_Button"` |
| Context for the translator / model | `<note from="developer">` (a note without `from` also counts) | "Button on the Case page; closes the support case" |
| Hard length limit | `maxwidth="N" size-unit="char"` (pixels are ignored) | `maxwidth="10"` |
| Parameters | plain text `{0}`, `{1}`; merge fields `{!Name}` | `{0} records updated` |

## Output: what comes back

Every `<trans-unit>` comes back with a `<target state="…">`:

- `translated`: passed the QA gate and the AI verifier.
- `needs-review-translation`: something blocks auto-approval. One
  `<note from="qa" priority="1|5" annotates="target">` per reason
  (priority 1 = error, 5 = warning), e.g.
  `[error] length: Target is 12 characters, max_length is 10`.

For `ui` content, the QA gate checks placeholders (`{0}`, `{!Name}`), numbers,
required glossary terms, `max_length` and Chinese-Latin spacing. XML tag checks
apply only to `doc` content.

## Python

```python
from l10n_pipeline.units import Unit
from l10n_pipeline import pipeline

units = [Unit(id="Close_Case_Button", source="Close", content_type="ui",
              note="Button on the Case page", max_length=10)]
results, stats = pipeline.run(units, config="C")
results[0].target, results[0].status, results[0].issues
```
