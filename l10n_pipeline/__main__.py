"""Command line entry point: python -m l10n_pipeline <command> ...

  eval   run one configuration on a JSONL test set and save the run:
         python -m l10n_pipeline eval --config C --data data/golden.jsonl --out runs/C
"""

import argparse
import json
from pathlib import Path

from l10n_pipeline import pipeline
from l10n_pipeline.units import Unit


def load_units(path: Path, limit: int | None) -> list[Unit]:
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    return [Unit(r["id"], r["source"], reference=r.get("reference")) for r in rows[:limit]]


def cmd_eval(args) -> None:
    units = load_units(args.data, args.limit)
    results, stats = pipeline.run(units, args.config)
    pipeline.save_run(units, results, stats, args.config, args.out)

    review = sum(r.status == "needs_review" for r in results)
    print(f"Config {args.config}: {len(results)} segments, {review} routed to human review, "
          f"{sum(r.reverted for r in results)} edits reverted by the stage guard")
    for name, s in stats.stages.items():
        print(f"  {name:<9} ${s.cost_usd:.4f}  {s.requests} requests "
              f"({s.cached_requests} from cache)  in {s.input_tokens} / out {s.output_tokens} tokens")
    print(f"Saved to {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m l10n_pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("eval", help="run one configuration on a JSONL test set")
    p_eval.add_argument("--config", choices=sorted(pipeline.CONFIGS), default="C")
    p_eval.add_argument("--data", type=Path, default=Path("data/golden.jsonl"))
    p_eval.add_argument("--out", type=Path, required=True)
    p_eval.add_argument("--limit", type=int, default=None, help="only the first N segments")
    p_eval.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
