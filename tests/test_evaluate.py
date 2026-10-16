"""Tests for scripts/evaluate.py.

Fake runs are built in pytest's tmp_path from real golden segments, so no
test writes into runs/ or reports/ and no LLM is called. "dev NNNN" =
golden segment enzh_dev_000000NNNN.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sacrebleu.metrics import CHRF

REPO = Path(__file__).resolve().parent.parent
# scripts/ is not a package: put it on sys.path to import evaluate.py as a module.
sys.path.insert(0, str(REPO / "scripts"))

import evaluate  # noqa: E402
from l10n_pipeline.context import load_glossary  # noqa: E402

GOLDEN = evaluate.load_golden()
GLOSSARY = load_glossary()


def golden_row(short_id: str) -> dict:
    return next(row for seg_id, row in GOLDEN.items() if seg_id.endswith(short_id))


# A small real sample: 4 tagged segments (0015 nested, 0463 with a protected
# <filepath>) and 4 untagged ones; most contain required glossary terms.
SAMPLE = [golden_row(k) for k in ("0015", "0255", "0388", "0463", "0009", "0063", "0166", "0162")]


def write_run(run_dir: Path, rows: list[dict], targets: list[str]) -> Path:
    """A fake run in the pipeline's output format (segments.jsonl + summary.json)."""
    run_dir.mkdir(parents=True)
    with open(run_dir / "segments.jsonl", "w", encoding="utf-8") as f:
        for row, target in zip(rows, targets):
            segment = {"id": row["id"], "source": row["source"], "reference": row["reference"],
                       "target": target, "status": "auto_approved", "issues": [],
                       "drafts": {"translate": target, "edit": None},
                       "reverted": False, "mqm_penalty": None}
            f.write(json.dumps(segment, ensure_ascii=False) + "\n")
    summary = {"config": "A", "description": "fake run for tests", "model": "none",
               "segments": len(rows), "source_words": sum(row["n_words"] for row in rows),
               "stages": {"translate": {"cost_usd": 0.01, "input_tokens": 100,
                                        "output_tokens": 50, "requests": 1, "seconds": 1.0}},
               "total_cost_usd": 0.01}
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


def run_metrics(tmp_path: Path, rows: list[dict], targets: list[str]) -> dict:
    """Write a fake run of `rows` and evaluate it against those rows only."""
    golden = {row["id"]: row for row in rows}
    run_dir = write_run(tmp_path / "run", rows, targets)
    result, _ = evaluate.evaluate_run(run_dir, golden, GLOSSARY,
                                      evaluate.make_resamples(len(rows)))
    return result


# ------------------------------------------------- the four required cases

def test_reference_as_target_scores_100(tmp_path):
    result = run_metrics(tmp_path, SAMPLE, [row["reference"] for row in SAMPLE])
    assert result["chrf"]["overall"] == pytest.approx(100)
    assert result["chrf"]["ci95"] == pytest.approx([100, 100])
    assert result["tag_integrity"]["pct"] == 100
    reference = evaluate.gate_metrics(SAMPLE, [row["reference"] for row in SAMPLE], GLOSSARY)
    assert result["term_accuracy"]["n"] > 0
    assert result["term_accuracy"] == reference["term_accuracy"]


def test_untranslated_target_scores_low(tmp_path):
    result = run_metrics(tmp_path, SAMPLE, [row["source"] for row in SAMPLE])
    # Some characters still match (product names, digits, code), so not 0.
    assert result["chrf"]["overall"] < 40
    # No Chinese term can be found in an English target.
    assert result["term_accuracy"]["ok"] == 0


def test_missing_tag_lowers_tag_integrity_but_not_chrf(tmp_path):
    targets = [row["reference"] for row in SAMPLE]
    targets[1] = "单击自动关联设置。"   # dev 0255 without its <uicontrol> tags
    result = run_metrics(tmp_path, SAMPLE, targets)
    assert result["tag_integrity"]["ok"] == 3
    assert result["tag_integrity"]["n"] == 4
    assert result["tag_integrity"]["failed_ids"] == [SAMPLE[1]["id"]]
    # chrF scores the text only, and the text is unchanged.
    assert result["chrf"]["overall"] == pytest.approx(100)


def test_full_width_punctuation_scores_like_half_width(tmp_path):
    # The references use half-width , : ? (dataset NFKC); the style guide asks
    # the model for full-width ，：？, so both must score the same.
    full_width = str.maketrans(",:;()!?", "，：；（）！？")
    references = [row["reference"] for row in SAMPLE]
    targets = [ref.translate(full_width) for ref in references]
    assert targets != references   # the sample really contains such punctuation
    result = run_metrics(tmp_path, SAMPLE, targets)
    assert result["chrf"]["overall"] == pytest.approx(100)
    # Without the normalization, sacrebleu alone would penalize it.
    assert CHRF().corpus_score(targets, [references]).score < 100


# ------------------------------------------------------------- other rules

def test_plain_text_strips_tags_decodes_entities_and_applies_nfkc():
    segment = "单击 <uicontrol>保存</uicontrol>，然后 &lt;b&gt;"
    assert evaluate.plain_text(segment) == "单击 保存,然后 <b>"
    # Broken XML falls back to a regex, so the text is still scored.
    assert evaluate.plain_text("启用 <b>Chatter</i>（测试）") == "启用 Chatter(测试)"


def test_translated_or_unparseable_protected_content_is_not_intact(tmp_path):
    rows = [golden_row("0463")]   # A <filepath>removecodepkg</filepath> folder ...
    translated = rows[0]["reference"].replace("removecodepkg", "删除代码包")
    result = run_metrics(tmp_path / "translated", rows, [translated])
    assert result["protected_intact"]["ok"] == 0
    # Broken XML: the gate cannot check the protected text, so it is not a pass.
    broken = rows[0]["reference"].replace("</filepath>", "")
    result = run_metrics(tmp_path / "broken", rows, [broken])
    assert result["protected_intact"]["ok"] == 0
    assert result["tag_integrity"]["ok"] == 0


def test_edit_metrics_count_changed_and_reverted_edits():
    segments = [
        {"id": "1", "drafts": {"translate": "甲", "edit": "乙"}, "reverted": False},
        {"id": "2", "drafts": {"translate": "甲", "edit": "甲"}, "reverted": False},
        {"id": "3", "drafts": {"translate": "甲", "edit": "丙"}, "reverted": True},
    ]
    assert evaluate.edit_metrics(segments) == {
        "segments": 3, "changed": 2, "changed_pct": pytest.approx(200 / 3),
        "reverted": 1, "reverted_ids": ["3"]}
    no_edit = [{"id": "1", "drafts": {"translate": "甲", "edit": None}, "reverted": False}]
    assert evaluate.edit_metrics(no_edit) is None


def test_paired_comparison_of_a_run_with_itself_shows_no_difference():
    boot = [50.0, 51.0, 49.5]
    assert evaluate.paired_comparison(boot, boot, 0.0) == {
        "delta": 0.0, "ci95": [0.0, 0.0], "share_le_0": 1.0}


def test_partial_run_is_refused(tmp_path):
    run_dir = write_run(tmp_path / "run", SAMPLE[:3], [row["reference"] for row in SAMPLE[:3]])
    golden = {row["id"]: row for row in SAMPLE}
    with pytest.raises(ValueError, match="exactly once"):
        evaluate.load_run(run_dir, golden)


# ------------------------------------------- sanity run of the whole script

def test_whole_script_on_the_real_golden_set(tmp_path):
    rows = list(GOLDEN.values())
    source_run = write_run(tmp_path / "runs" / "source", rows, [row["source"] for row in rows])
    reference_run = write_run(tmp_path / "runs" / "reference", rows,
                              [row["reference"] for row in rows])
    out = tmp_path / "report" / "results.md"   # never the repo's reports/

    # Run exactly as documented (isolated mode), with the source run as baseline.
    completed = subprocess.run(
        [sys.executable, "-I", str(REPO / "scripts/evaluate.py"),
         str(source_run), str(reference_run), "--out", str(out)],
        capture_output=True, text=True, cwd=REPO)
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""   # no warnings (word count, cost totals)

    results = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    source, reference = results["runs"]
    assert results["baseline"] == "source"
    assert reference["chrf"]["overall"] == pytest.approx(100)
    # target = reference gives exactly the human-reference row. (Not 100% tag
    # integrity: the reference of dev 1539 is broken, see gate_calibration.md.)
    for metric in ("tag_integrity", "protected_intact", "term_accuracy", "gate_pass"):
        assert reference[metric] == results["human_reference"][metric]
    assert source["chrf"]["overall"] < 40
    assert reference["vs_baseline"]["delta"] > 50
    assert reference["vs_baseline"]["share_le_0"] == 0

    report = out.read_text(encoding="utf-8")
    assert "| Human reference |" in report
    assert "## Known reference problems" in report
