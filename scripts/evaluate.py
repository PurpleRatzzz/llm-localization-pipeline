"""Evaluate pipeline runs on the golden set (task T8).

Compares the configurations (A = translate only, B = + style rules and
glossary, C = full pipeline) on the 200 golden segments:
- chrF of the text against the professional reference, with a bootstrap
  95% confidence interval and a paired comparison with the first run;
- tag integrity, protected content, UI label and term accuracy and gate pass rate, by
  re-running the deterministic QA gate on every final target;
- routing to human review, edit-stage activity and cost, read from the run.

Usage (from the repo root):
    .venv/bin/python -I scripts/evaluate.py [RUN_DIR ...] [--out reports/results.md]
Without RUN_DIR, every subdirectory of runs/ that has a summary.json is used,
sorted by name. The first run is the baseline for the paired comparison.
Writes the Markdown report and, next to it, a JSON file with all numbers
(reports/results.md -> reports/results.json).
"""

import argparse
import html
import json
import random
import re
import statistics
import sys
import unicodedata
from pathlib import Path

# "python -I" (isolated mode) does not put the repo on sys.path, so add it
# here to be able to import the local l10n_pipeline package.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sacrebleu.metrics import CHRF  # noqa: E402

from l10n_pipeline import qa_gate  # noqa: E402
from l10n_pipeline.context import GLOSSARY_PATH, load_glossary  # noqa: E402

GOLDEN_FILE = REPO / "data/golden.jsonl"
RUNS_DIR = REPO / "runs"
DEFAULT_OUT = REPO / "reports/results.md"

# 1000 bootstrap resamples is the usual choice in MT evaluation (Koehn 2004).
# The fixed seed makes every run of this script print identical numbers.
N_RESAMPLES = 1000
SEED = 42

# The gate checks that say whether the inline tags survived. tag_order is not
# included: Chinese word order may legitimately move a tag (it is a warning).
TAG_CHECKS = {"xml_wellformed", "tag_set", "tag_nesting"}

# sacrebleu defaults: character n-grams up to 6, no word n-grams (plain chrF,
# not chrF++), beta 2 (recall weighs twice as much as precision). chrF works
# on characters, so Chinese needs no word segmentation (BLEU would).
CHRF_METRIC = CHRF()


# ------------------------------------------------------------------ loading

def load_golden(path: Path = GOLDEN_FILE) -> dict[str, dict]:
    """Golden segments by id, in file order (the file is sorted by id)."""
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return {row["id"]: row for row in rows}


def load_run(run_dir: Path, golden: dict[str, dict]) -> tuple[dict, list[dict]]:
    """A run's summary.json and its segments, in golden order.

    Partial or mismatched runs are refused: every number compares the
    configurations on the same segments, and the paired bootstrap needs
    segment i to be the same segment in every run.
    """
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    with open(run_dir / "segments.jsonl", encoding="utf-8") as f:
        segments = [json.loads(line) for line in f]
    by_id = {seg["id"]: seg for seg in segments}
    if len(by_id) != len(segments) or by_id.keys() != golden.keys():
        raise ValueError(f"{run_dir}: segments.jsonl must contain each of the {len(golden)} "
                         f"golden segments exactly once (found {len(segments)} lines, "
                         f"{len(by_id)} distinct ids)")
    for seg_id, seg in by_id.items():
        # The gate checks the target against the source, so they must match.
        if seg["source"] != golden[seg_id]["source"]:
            raise ValueError(f"{run_dir}: the source of {seg_id} differs from the golden set")
    return summary, [by_id[seg_id] for seg_id in golden]


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def percent(count: int, n: int) -> float | None:
    return 100 * count / n if n else None   # None: nothing to measure (shown as n/a)


# --------------------------------------------------------------------- chrF

def plain_text(segment: str) -> str:
    """The text chrF scores: tags removed (their text kept), entities decoded, NFKC.

    Tags are measured separately (tag integrity), so chrF should score the
    words only; otherwise every "<uicontrol>" would count as matching text.
    NFKC turns full-width ，：；（）！？ into , : ; ( ) ! ? and keeps 。、“”,
    which is exactly how the references were preprocessed (style guide R8).
    Without it, a correct "，" in model output would count as a mismatch.
    """
    root, _ = qa_gate.parse_fragment(segment)
    if root is not None:
        text = "".join(qa_gate.text_nodes(root))   # the parser already decoded &lt; &gt; &amp;
    else:
        # Broken XML (a model error): strip anything tag-like with a regex,
        # so the text is still scored instead of the segment being dropped.
        text = html.unescape(re.sub(r"<[^>]*>", "", segment))
    # NFKC only after the tags are gone: it turns a full-width "＜" into "<",
    # which could break the XML parse.
    return unicodedata.normalize("NFKC", text)


def corpus_chrf(hypotheses: list[str], references: list[str]) -> float | None:
    """sacrebleu's corpus-level chrF (0-100), or None for an empty subset."""
    if not hypotheses:
        return None
    return CHRF_METRIC.corpus_score(hypotheses, [references]).score


def chrf_from_stats(stats: list[list[int]]) -> float:
    """Corpus chrF from per-segment n-gram counts.

    Corpus chrF is NOT the average of segment scores: sacrebleu sums the
    n-gram counts (hypothesis, reference, matching, for each n-gram order)
    over all segments, then computes one F-score. _extract_corpus_statistics
    and _aggregate_and_compute are the two halves of its corpus_score (its
    significance tests use them the same way). Calling them directly lets
    the bootstrap re-sum the counts of 1000 resamples in about a second,
    instead of re-extracting n-grams every time (~40 s per run).
    """
    return CHRF_METRIC._aggregate_and_compute(stats).score


def make_resamples(n_segments: int, n_resamples: int = N_RESAMPLES,
                   seed: int = SEED) -> list[list[int]]:
    """Bootstrap resamples: each one draws n_segments indices with replacement.

    The same list is used for every run, so a run and the baseline are always
    scored on the same resampled segments (a paired comparison).
    """
    rng = random.Random(seed)
    return [rng.choices(range(n_segments), k=n_segments) for _ in range(n_resamples)]


def ci95(values: list[float]) -> list[float]:
    """Percentile 95% interval: the 2.5th and 97.5th percentiles."""
    cuts = statistics.quantiles(values, n=40, method="inclusive")   # cut points every 2.5%
    return [cuts[0], cuts[-1]]


def chrf_metrics(rows: list[dict], targets: list[str],
                 resamples: list[list[int]]) -> tuple[dict, list[float]]:
    """chrF overall (with CI), on the tagged and on the untagged segments.

    Also returns the chrF of every bootstrap resample, for the paired comparison.
    """
    hyps = [plain_text(target) for target in targets]
    refs = [plain_text(row["reference"]) for row in rows]
    tagged = [i for i, row in enumerate(rows) if row["has_tags"]]
    untagged = [i for i, row in enumerate(rows) if not row["has_tags"]]
    overall = corpus_chrf(hyps, refs)

    stats = CHRF_METRIC._extract_corpus_statistics(hyps, [refs])
    # Self-check of the fast path: summing all counts must reproduce
    # corpus_score, so a sacrebleu upgrade cannot silently change the CIs.
    assert abs(chrf_from_stats(stats) - overall) < 1e-9
    # Bootstrap: score the corpus again on each resample. How much the score
    # moves between resamples shows how much it depends on which segments
    # happened to be in the golden set.
    boot = [chrf_from_stats([stats[i] for i in sample]) for sample in resamples]

    metrics = {
        "overall": overall,
        "ci95": ci95(boot),
        "tagged": corpus_chrf([hyps[i] for i in tagged], [refs[i] for i in tagged]),
        "untagged": corpus_chrf([hyps[i] for i in untagged], [refs[i] for i in untagged]),
    }
    return metrics, boot


def paired_comparison(run_boot: list[float], base_boot: list[float],
                      delta: float) -> dict:
    """Is the run's chrF really different from the baseline's, or is it noise?

    On each resample both are scored on the same segments, so the difference
    is not affected by how easy the drawn segments happen to be. The share of
    resamples where run - baseline <= 0 approximates a one-sided p-value for
    "the run is better": near 0 = better on almost every resample; about 0.5
    = no real difference.
    """
    diffs = [run - base for run, base in zip(run_boot, base_boot)]
    return {"delta": delta,
            "ci95": ci95(diffs),
            "share_le_0": sum(d <= 0 for d in diffs) / len(diffs)}


# ------------------------------------------------------------- QA metrics

def has_protected(source: str) -> bool:
    root, _ = qa_gate.parse_fragment(source)
    return root is not None and any(elem.tag in qa_gate.PROTECTED_TAGS for elem in root.iter())


def gate_metrics(rows: list[dict], targets: list[str], glossary) -> dict:
    """Re-run the QA gate on the final targets and count what survived.

    The issues stored in the run are ignored on purpose: every configuration,
    and the human reference, is judged by the same gate code and glossary.
    """
    tag_failed, protected_failed, gate_failed, term_failed, ui_failed = [], [], [], [], []
    n_protected = n_term_pairs = n_term_missed = n_ui_pairs = n_ui_missed = 0
    for row, target in zip(rows, targets):
        issues = qa_gate.check_segment(row["source"], target, glossary=glossary)
        errors = [issue.check for issue in issues if issue.severity == "error"]

        if row["has_tags"] and TAG_CHECKS & set(errors):
            tag_failed.append(row["id"])

        if has_protected(row["source"]):
            n_protected += 1
            # If the target is not well-formed XML, the gate cannot compare the
            # protected text and reports no protected_content error, so count
            # it as not intact rather than as a pass.
            if "protected_content" in errors or "xml_wellformed" in errors:
                protected_failed.append(row["id"])

        # Same rule as the gate: one terminology error per required term that
        # occurs in the source but whose approved translation is missing.
        required = [e for e in qa_gate.find_terms(row["source"], glossary) if e.kind == "required"]
        missed = errors.count("terminology")
        n_term_pairs += len(required)
        n_term_missed += missed
        if missed:
            term_failed.append(row["id"])

        # UI labels: one ui_label error per required label the target lacks.
        ui_required = [e for _, e in qa_gate.find_ui_labels(row["source"], glossary)
                       if e.kind == "required"]
        ui_missed = errors.count("ui_label")
        n_ui_pairs += len(ui_required)
        n_ui_missed += ui_missed
        if ui_missed:
            ui_failed.append(row["id"])

        if not qa_gate.passed(issues):
            gate_failed.append(row["id"])

    def outcome(n: int, failed_ids: list[str]) -> dict:
        return {"ok": n - len(failed_ids), "n": n, "pct": percent(n - len(failed_ids), n),
                "failed_ids": failed_ids}

    n_term_ok = n_term_pairs - n_term_missed
    return {
        "tag_integrity": outcome(sum(row["has_tags"] for row in rows), tag_failed),
        "protected_intact": outcome(n_protected, protected_failed),
        # Counted per (segment, term) pair; failed_ids = segments with a miss.
        "term_accuracy": {"ok": n_term_ok, "n": n_term_pairs,
                          "pct": percent(n_term_ok, n_term_pairs), "failed_ids": term_failed},
        "ui_label_accuracy": {"ok": n_ui_pairs - n_ui_missed, "n": n_ui_pairs,
                              "pct": percent(n_ui_pairs - n_ui_missed, n_ui_pairs),
                              "failed_ids": ui_failed},
        "gate_pass": outcome(len(rows), gate_failed),
    }


def edit_metrics(segments: list[dict]) -> dict | None:
    """How active the edit stage was; None if the run has no edit stage."""
    edited = [seg for seg in segments if seg["drafts"].get("edit") is not None]
    if not edited:
        return None
    # Exact string comparison: any change the editor made counts.
    changed = sum(seg["drafts"]["edit"] != seg["drafts"]["translate"] for seg in edited)
    reverted = [seg["id"] for seg in segments if seg["reverted"]]
    return {"segments": len(edited), "changed": changed,
            "changed_pct": percent(changed, len(edited)),
            "reverted": len(reverted), "reverted_ids": reverted}


def cost_metrics(summary: dict, source_words: int) -> dict:
    total = summary["total_cost_usd"]
    return {"total_usd": total, "source_words": source_words,
            "usd_per_1k_words": 1000 * total / source_words,
            "stages": summary.get("stages", {})}


# --------------------------------------------------------------- evaluation

def evaluate_run(run_dir: Path, golden: dict[str, dict], glossary,
                 resamples: list[list[int]]) -> tuple[dict, list[float]]:
    """All metrics for one run, plus its bootstrap chrF scores."""
    summary, segments = load_run(run_dir, golden)
    rows = list(golden.values())
    targets = [seg["target"] for seg in segments]

    # Words come from the golden set (whitespace words of the tag-stripped
    # source, data/README.md), so $/1k words uses the same count for every run.
    words = sum(row["n_words"] for row in rows)
    if summary.get("source_words") != words:
        warn(f"{run_dir.name}: summary.json says {summary.get('source_words')} source words, "
             f"the golden set has {words}; using {words}")
    stage_sum = sum(stage["cost_usd"] for stage in summary.get("stages", {}).values())
    if abs(stage_sum - summary["total_cost_usd"]) > 1e-4:
        warn(f"{run_dir.name}: stage costs add up to ${stage_sum:.6f}, "
             f"but total_cost_usd is ${summary['total_cost_usd']:.6f}")

    chrf, boot = chrf_metrics(rows, targets, resamples)
    routed = sum(seg["status"] == "needs_review" for seg in segments)
    result = {
        "name": run_dir.name,
        "config": summary["config"],
        "description": summary["description"],
        "model": summary["model"],
        "segments": len(segments),
        "chrf": chrf,
        **gate_metrics(rows, targets, glossary),
        # Taken from the run: routing is the pipeline's own decision.
        "routed": {"count": routed, "n": len(segments), "pct": percent(routed, len(segments))},
        "edit": edit_metrics(segments),
        "cost": cost_metrics(summary, words),
    }
    return result, boot


def reference_problems(golden: dict[str, dict], glossary) -> list[dict]:
    """Golden references that fail the gate themselves (with the same glossary)."""
    problems = []
    for row in golden.values():
        issues = qa_gate.check_segment(row["source"], row["reference"], glossary=glossary)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            checks = sorted({issue.check for issue in errors})
            problems.append({"id": row["id"], "checks": checks,
                             # Terminology-only = the reference uses another word than
                             # the glossary; anything else is a structural problem.
                             "terminology_only": checks == ["terminology"],
                             "messages": [issue.message for issue in errors]})
    return problems


def evaluate(run_dirs: list[Path], golden: dict[str, dict], glossary) -> dict:
    """Every number of the report, as one JSON-ready dict."""
    resamples = make_resamples(len(golden))
    runs, boots = [], []
    for run_dir in run_dirs:
        result, boot = evaluate_run(run_dir, golden, glossary, resamples)
        runs.append(result)
        boots.append(boot)

    baseline = runs[0]
    for run, boot in zip(runs, boots):
        if run is baseline:
            run["vs_baseline"] = None
        else:
            delta = run["chrf"]["overall"] - baseline["chrf"]["overall"]
            run["vs_baseline"] = paired_comparison(boot, boots[0], delta)

    rows = list(golden.values())
    return {
        "golden": {"file": str(GOLDEN_FILE.relative_to(REPO)), "segments": len(rows),
                   "tagged": sum(row["has_tags"] for row in rows),
                   "source_words": sum(row["n_words"] for row in rows)},
        "glossary": {"file": str(GLOSSARY_PATH.relative_to(REPO)), "entries": len(glossary),
                     "required": sum(entry.kind == "required" for entry in glossary)},
        "chrf_signature": CHRF_METRIC.get_signature().format(),
        "bootstrap": {"resamples": N_RESAMPLES, "seed": SEED},
        "baseline": baseline["name"],
        "runs": runs,
        "human_reference": gate_metrics(rows, [row["reference"] for row in rows], glossary),
        "reference_problems": reference_problems(golden, glossary),
    }


# ------------------------------------------------------------------ report

def fmt_pct(metric: dict) -> str:
    return "n/a" if metric["pct"] is None else f"{metric['pct']:.1f}%"


def fmt_num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def short_id(seg_id: str) -> str:
    # Last 4 digits of the dev id, as in reports/gate_calibration.md.
    return seg_id[-4:]


def markdown_report(results: dict) -> str:
    runs, ref, golden = results["runs"], results["human_reference"], results["golden"]
    baseline = results["baseline"]
    out = ["# Evaluation results", "",
           f"{golden['segments']} golden segments ({golden['tagged']} with inline tags, "
           f"{golden['segments'] - golden['tagged']} without), scored against professional "
           "reference translations. Generated by `scripts/evaluate.py`; every number is also "
           "in the .json file of the same name. See Method at the end.", "",
           "## Runs", ""]
    for run in runs:
        out.append(f"- **{run['name']}** (config {run['config']}): {run['description']}. "
                   f"Model `{run['model']}`.")

    out += ["", "## Main comparison", "",
            "| Run | chrF [95% CI] | chrF tagged | chrF untagged | Tag integrity | "
            "Protected intact | UI labels | Terms in text | Gate pass | Routed to review | $ / 1k words |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for run in runs:
        chrf = run["chrf"]
        low, high = chrf["ci95"]
        out.append(f"| {run['name']} | {chrf['overall']:.1f} [{low:.1f}, {high:.1f}] | "
                   f"{fmt_num(chrf['tagged'])} | {fmt_num(chrf['untagged'])} | "
                   f"{fmt_pct(run['tag_integrity'])} | {fmt_pct(run['protected_intact'])} | "
                   f"{fmt_pct(run['ui_label_accuracy'])} | "
                   f"{fmt_pct(run['term_accuracy'])} | {fmt_pct(run['gate_pass'])} | "
                   f"{fmt_pct(run['routed'])} | ${run['cost']['usd_per_1k_words']:.3f} |")
    out.append(f"| Human reference | n/a | n/a | n/a | {fmt_pct(ref['tag_integrity'])} | "
               f"{fmt_pct(ref['protected_intact'])} | {fmt_pct(ref['ui_label_accuracy'])} | "
               f"{fmt_pct(ref['term_accuracy'])} | "
               f"{fmt_pct(ref['gate_pass'])} | n/a | n/a |")
    out += ["", f"Denominators: tag integrity over the {ref['tag_integrity']['n']} tagged "
            f"segments; protected intact over the {ref['protected_intact']['n']} segments with "
            f"a protected tag (a small sample); UI labels over "
            f"{ref['ui_label_accuracy']['n']} (segment, required label) pairs; terms in text over "
            f"{ref['term_accuracy']['n']} (segment, required term) pairs; gate pass and routing "
            f"over all {golden['segments']} segments."]

    out += ["", f"## Paired comparison with the baseline ({baseline})", ""]
    others = [run for run in runs if run["vs_baseline"] is not None]
    if others:
        out += [f"| Run | chrF − {baseline} | 95% CI of the difference | "
                "Resamples with difference ≤ 0 |", "|---|---:|---:|---:|"]
        for run in others:
            diff = run["vs_baseline"]
            low, high = diff["ci95"]
            out.append(f"| {run['name']} | {diff['delta']:+.1f} | [{low:+.1f}, {high:+.1f}] | "
                       f"{diff['share_le_0']:.3f} |")
    else:
        out.append("Only one run, so there is nothing to compare.")

    out += ["", "## Cost per stage", "",
            "| Run | Stage | Requests | Input tokens | Output tokens | API seconds | Cost (USD) |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for run in runs:
        for name, stage in run["cost"]["stages"].items():
            cached = stage.get("cached_requests")   # not in older summaries
            requests = f"{stage['requests']}" + (f" ({cached} cached)" if cached else "")
            out.append(f"| {run['name']} | {name} | {requests} | {stage['input_tokens']:,} | "
                       f"{stage['output_tokens']:,} | {stage['seconds']:.0f} | "
                       f"${stage['cost_usd']:.4f} |")
        out.append(f"| {run['name']} | **total** | | | | | **${run['cost']['total_usd']:.4f}** |")

    out += ["", "## Edit stage", ""]
    edit_runs = [run for run in runs if run["edit"] is not None]
    if edit_runs:
        out += ["| Run | Segments with an edit output | Edit changed the draft | "
                "Edits reverted by the stage guard |", "|---|---:|---:|---:|"]
        for run in edit_runs:
            edit = run["edit"]
            out.append(f"| {run['name']} | {edit['segments']} | {edit['changed']} "
                       f"({edit['changed_pct']:.1f}%) | {edit['reverted']} |")
        out += ["", "Reverted = the edit introduced a gate error the draft did not have, "
                "so the draft was kept."]
    else:
        out.append("No run has an edit stage.")

    problems = results["reference_problems"]
    out += ["", "## Known reference problems", "",
            f"{len(problems)} of {golden['segments']} golden references fail the gate "
            "themselves (same gate and glossary as the runs). Segment = last 4 digits of the "
            "dev id, as in `reports/gate_calibration.md`.", ""]
    if problems:
        out += ["| Segment | Failed checks | First error |", "|---|---|---|"]
        for problem in problems:
            message = problem["messages"][0].replace("|", "\\|")
            out.append(f"| {short_id(problem['id'])} | {', '.join(problem['checks'])} | "
                       f"`{message}` |")
        out.append("")
        if any(not problem["terminology_only"] for problem in problems):
            out.append("- Tag or protected-content errors: broken references (verdicts in "
                       "`reports/gate_calibration.md`, which ran the same gate on all dev "
                       "references). A correct translation can disagree with them, so chrF "
                       "and the QA metrics are unreliable on these segments.")
        if any(problem["terminology_only"] for problem in problems):
            out.append("- Terminology only: the reference does not use the glossary's required "
                       "term (the calibration ran without a glossary). These keep the human "
                       "reference below 100% term accuracy.")

    glossary = results["glossary"]
    out += ["", "## Method", "",
            f"- **Golden set**: `{golden['file']}`, dev segments with professional references. "
            "Tagged segments are oversampled to 50% (25.9% in dev), so the overall score "
            "over-weights them; chrF tagged and untagged are the per-group scores.",
            f"- **chrF**: sacrebleu `{results['chrf_signature']}` (character 6-grams, beta 2), "
            "corpus level: n-gram counts are summed over the segments, then one F-score is "
            "computed. Both sides are normalized first: inline tags removed (their text kept, "
            "including code in protected tags), entities decoded (`&lt;` → `<`), Unicode NFKC. "
            "NFKC is needed because the references are NFKC-normalized (full-width ，：；（）！？ "
            "became half-width in the dataset, style guide R8); without it a correct full-width "
            "comma would count as an error. chrF ignores spaces (`space:no`), so CJK-Latin "
            "spacing does not change it. Tags are measured by tag integrity instead.",
            f"- **Bootstrap**: {results['bootstrap']['resamples']} resamples of the segments "
            f"with replacement (seed {results['bootstrap']['seed']}), the same resamples for "
            "every run. 95% CI = 2.5th to 97.5th percentile of the resampled corpus chrF. "
            "Paired comparison: on each resample, chrF(run) − chrF(baseline); the table gives "
            "the observed difference, the 95% CI of the resampled differences, and the share "
            "of resamples where the difference is ≤ 0 (an approximate one-sided p-value for "
            f"\"better than the baseline\"; resolution 1/{results['bootstrap']['resamples']}).",
            "- **QA metrics** re-run the deterministic gate (`check_segment` in "
            "`l10n_pipeline/qa_gate.py`, with the glossary) on every final target and ignore "
            "the issues stored in the run, so all configurations and the human reference are "
            "judged by identical code.",
            "  - *Tag integrity*: tagged segments with no `xml_wellformed`, `tag_set` or "
            "`tag_nesting` error. Tag order may change (Chinese word order).",
            f"  - *Protected intact*: segments whose source has a "
            f"{'/'.join(f'`{t}`' for t in sorted(qa_gate.PROTECTED_TAGS))} element, with no "
            "`protected_content` error. A target that is not well-formed XML counts as not "
            "intact, because its protected text cannot be checked.",
            "  - *UI labels*: (segment, required label) pairs where the whole text of a "
            "`uicontrol`/`parmname` in the source is a \"ui\" glossary label (`find_ui_labels`); "
            "correct = a target tag of the same kind has exactly the approved translation "
            "(no `ui_label` error).",
            "  - *Terms in text*: (segment, required term) pairs where the gate's `find_terms` "
            "finds a \"text\" glossary term in the running text of the source (outside UI and "
            "code tags); correct = the approved translation appears in the target (no "
            "`terminology` error for it).",
            "  - *Gate pass*: segments with no error-level issue. The gate checks format, "
            "protected content and required terms, not meaning or fluency: it is a routing "
            "filter, not a quality score.",
            "- **Routed to review**: share of segments the run itself sent to human review "
            "(`status` = `needs_review`): any gate error, plus error-level verifier findings "
            "when the verify stage runs.",
            f"- **Glossary**: `{glossary['file']}`, {glossary['entries']} entries, "
            f"{glossary['required']} required. Only required terms are enforced; ambiguous "
            "ones are shown to the model with a note but not checked.",
            "- **Cost**: from each run's `summary.json`, computed from the API token counts of "
            "every request (a request served from the local cache reports the cost of its "
            f"original call). $ / 1k words uses the {golden['source_words']:,} source words of "
            "the golden set. API seconds = sum of call durations; batches run in parallel, so "
            "the wall-clock time is shorter.",
            "- **Human reference** row: the professional translation evaluated as if it were a "
            "run: the ceiling for the QA metrics and a sanity check of the glossary. chrF does "
            "not apply (it is the reference).", ""]
    return "\n".join(out)


def rounded(value, digits: int = 6):
    """Round every float in nested dicts and lists, so the JSON has no float noise."""
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: rounded(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [rounded(item, digits) for item in value]
    return value


# -------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate pipeline runs on the golden set.")
    parser.add_argument("runs", nargs="*", type=Path,
                        help="run directories; the first is the baseline "
                             "(default: every runs/* with a summary.json, sorted by name)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="Markdown report; the .json with all numbers is written next to it")
    args = parser.parse_args(argv)

    run_dirs = args.runs or sorted(p for p in RUNS_DIR.glob("*") if (p / "summary.json").exists())
    if not run_dirs:
        sys.exit(f"No runs to evaluate: no directory with a summary.json in {RUNS_DIR}")
    missing = [str(p) for p in run_dirs if not (p / "summary.json").exists()]
    if missing:
        sys.exit(f"Not a run directory (no summary.json): {', '.join(missing)}")
    names = [run_dir.name for run_dir in run_dirs]
    if len(set(names)) != len(names):
        sys.exit(f"Run directories need distinct names (used as table labels): {names}")

    try:
        results = evaluate(run_dirs, load_golden(), load_glossary())
    except ValueError as err:   # a partial or mismatched run
        sys.exit(str(err))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown_report(results), encoding="utf-8")
    json_path = args.out.with_suffix(".json")
    json_path.write_text(json.dumps(rounded(results), ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(f"Evaluated {', '.join(names)} (baseline {names[0]}); "
          f"wrote {args.out} and {json_path}")
    for run in results["runs"]:
        print(f"  {run['name']}: chrF {run['chrf']['overall']:.1f}, "
              f"gate pass {fmt_pct(run['gate_pass'])}, routed {fmt_pct(run['routed'])}, "
              f"${run['cost']['total_usd']:.4f}")


if __name__ == "__main__":
    main()
