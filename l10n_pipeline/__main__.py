"""Command line entry point: python -m l10n_pipeline <command> ...

  eval       run one configuration on a JSONL test set and save the run, plus
             an XLIFF handoff file with the segments that need human review:
             python -m l10n_pipeline eval --config C --data data/golden.jsonl --out runs/C

  translate  XLIFF 1.2 in, XLIFF 1.2 out (the interface for other tools):
             python -m l10n_pipeline translate --in labels.xlf --out labels.zh-CN.xlf --content-type ui
"""

import argparse
import json
from pathlib import Path

from l10n_pipeline import llm, pipeline
from l10n_pipeline.xliff import read_xliff, write_xliff
from l10n_pipeline.units import Unit


def load_units(path: Path, limit: int | None) -> list[Unit]:
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    return [Unit(r["id"], r["source"], reference=r.get("reference")) for r in rows[:limit]]


def cmd_eval(args) -> None:
    llm.MODEL = args.model
    units = load_units(args.data, args.limit)
    results, stats = pipeline.run(units, args.config)
    pipeline.save_run(units, results, stats, args.config, args.out)
    # The vendor/reviewer handoff: only the segments a person has to look at.
    flagged = [r for r in results if r.status == "needs_review"]
    write_xliff(units, flagged, args.out / "handoff.xlf")

    review = sum(r.status == "needs_review" for r in results)
    print(f"Config {args.config}: {len(results)} segments, {review} routed to human review, "
          f"{sum(r.reverted for r in results)} edits reverted by the stage guard")
    for name, s in stats.stages.items():
        print(f"  {name:<9} ${s.cost_usd:.4f}  {s.requests} requests "
              f"({s.cached_requests} from cache)  in {s.input_tokens} / out {s.output_tokens} tokens")
    print(f"Saved to {args.out} ({len(flagged)} segments in handoff.xlf)")


def cmd_translate(args) -> None:
    units, _ = read_xliff(args.input, content_type=args.content_type)
    results, stats = pipeline.run(units, args.config)
    # All units go back, each with a state: "translated" or "needs-review-translation".
    write_xliff(units, results, args.output, target_lang=args.target_lang,
                original=args.input.name)
    review = sum(r.status == "needs_review" for r in results)
    cost = sum(s.cost_usd for s in stats.stages.values())
    print(f"{len(results)} units translated, {review} need review, ${cost:.4f}. "
          f"Written to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m l10n_pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("eval", help="run one configuration on a JSONL test set")
    p_eval.add_argument("--config", choices=sorted(pipeline.CONFIGS), default="C")
    p_eval.add_argument("--data", type=Path, default=Path("data/golden.jsonl"))
    p_eval.add_argument("--out", type=Path, required=True)
    p_eval.add_argument("--limit", type=int, default=None, help="only the first N segments")
    p_eval.add_argument("--model", choices=sorted(llm.PRICES), default=llm.MODEL)
    p_eval.set_defaults(func=cmd_eval)

    p_tr = sub.add_parser("translate", help="translate an XLIFF 1.2 file")
    p_tr.add_argument("--in", dest="input", type=Path, required=True)
    p_tr.add_argument("--out", dest="output", type=Path, required=True)
    p_tr.add_argument("--content-type", choices=["doc", "ui"], default="doc",
                      help="doc: XML with inline tags; ui: plain-text UI strings such as Custom Labels")
    # E is the configuration recommended in reports/decision_memo.md.
    p_tr.add_argument("--config", choices=sorted(pipeline.CONFIGS), default="E")
    p_tr.add_argument("--target-lang", default="zh-CN", help="only zh-CN has context assets today")
    p_tr.set_defaults(func=cmd_translate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
