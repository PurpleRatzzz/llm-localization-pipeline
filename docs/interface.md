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
| `--config` | `A` translate only, `B` + style rules and glossary, `C` + edit and verify, `D` B + translation memory, `E` D + concordance (recommended), `F` E + verify | `E` |
| `--target-lang` | `zh-CN` or `zh_CN` (the Salesforce metadata spelling). Only zh-CN has a glossary and style rules; any other value exits with code 2 before any API call | `zh-CN` |

Requires `ANTHROPIC_API_KEY` in the environment. Configurations D-F also need
the translation memory, built once by `.venv/bin/python -I scripts/prepare_data.py`.

Exit codes: `0` success; `1` a runtime problem, with a one-line `error:`
message on stderr (missing or rejected API key, missing input file or
translation memory, model refusal); `2` invalid arguments (e.g. an unsupported
`--target-lang`). The output file keeps the input file's `original` and
`source-language` attributes.

## Input: what the caller puts in the file

| Need | XLIFF 1.2 | Example |
|---|---|---|
| One string | `<trans-unit id="…"><source>…</source>` | `id="Close_Case_Button"` |
| Context for the translator / model | `<note from="developer">`, `<note from="context">`, or a note without `from`. All of them reach the model; `qa` notes are ignored | "Button on the Case page; closes the support case"; `component: c-hero`, `ui: button` |
| Hard length limit | `maxwidth="N" size-unit="char"` (pixels are ignored), or a `maxlen: N` marker in a context note when there is no `maxwidth` | `maxwidth="10"`, `maxlen: 40` |
| Parameters | plain text `{0}`, `{1}`; merge fields `{!Name}` | `{0} records updated` |

## Output: what comes back

Every `<trans-unit>` comes back with a `<target state="…">`:

- `translated`: passed the QA gate, and the AI verifier too in configurations
  that run it (C and F). The default configuration E has no verifier.
- `needs-review-translation`: something blocks auto-approval. One
  `<note from="qa" priority="1|5" annotates="target">` per reason
  (priority 1 = error, 5 = warning), e.g.
  `[error] length: Target is 12 characters, max_length is 10`.

For `ui` content, the QA gate checks placeholders (`{0}`, `{!Name}`), numbers,
glossary terms (a whole string that is a glossary UI label must use its
translation, e.g. "Save" → 保存), untranslated text, `max_length` and
Chinese-Latin spacing. XML tag checks apply only to `doc` content.

## Python

```python
from l10n_pipeline.units import Unit
from l10n_pipeline import pipeline

units = [Unit(id="Close_Case_Button", source="Close", content_type="ui",
              note="Button on the Case page", max_length=10)]
results, stats = pipeline.run(units, config="E")
results[0].target, results[0].status, results[0].issues
```
