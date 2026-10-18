"""Orchestration: run the stages for a batch, apply the QA gate after each
stage, and route every segment to auto-approval or human review.

The configurations compared in the evaluation:
  A  translate only, no context (a plain MT prompt)
  B  translate with style rules + glossary
  C  B + edit + verify (the full pipeline)
  D  B + translation memory: fuzzy matches (>= 75%) from the train split
  E  D + TM concordance: approved translations of specific phrases, for every segment
  F  E + verify (no edit): E's translations, with the AI verifier routing to review
B and C send byte-identical translate requests, so the LLM cache serves C's
translate stage from B's run (or the other way round) at no extra cost.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from l10n_pipeline import context as ctx
from l10n_pipeline import llm, stages, tm
from l10n_pipeline.qa_gate import (check_segment, find_terms, find_ui_labels, parse_fragment,
                                   passed, text_nodes)
from l10n_pipeline.units import GlossaryEntry, Issue, Result, Unit

CONFIGS = {
    "A": {"context": False, "tm": False, "concordance": False, "edit": False, "verify": False,
          "description": "Translate only, no style rules or glossary"},
    "B": {"context": True, "tm": False, "concordance": False, "edit": False, "verify": False,
          "description": "Translate with style rules + glossary"},
    "C": {"context": True, "tm": False, "concordance": False, "edit": True, "verify": True,
          "description": "Full pipeline: translate + edit + verify, gate after each stage"},
    "D": {"context": True, "tm": True, "concordance": False, "edit": False, "verify": False,
          "description": "Translate with style rules + glossary + translation memory "
                         "(fuzzy matches >= 75%)"},
    "E": {"context": True, "tm": True, "concordance": True, "edit": False, "verify": False,
          "description": "D + TM concordance (approved translations of specific phrases, "
                         "for every segment)"},
    "F": {"context": True, "tm": True, "concordance": True, "edit": False, "verify": True,
          "description": "E + verify: the AI verifier routes segments to review (no edit)"},
}

BATCH_SIZE = 10   # segments per request: enough to share the prompt cost, small enough to check
MAX_WORKERS = 4   # batches processed in parallel


@dataclass
class StageStats:
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    cached_requests: int = 0   # served from the disk cache: no money spent this run
    seconds: float = 0.0       # sum of API call durations (batches run in parallel)

    def add(self, response: llm.LLMResponse) -> None:
        self.cost_usd += response.cost_usd
        self.input_tokens += response.usage["input_tokens"]
        self.output_tokens += response.usage["output_tokens"]
        self.requests += 1
        self.cached_requests += response.cached
        self.seconds += response.seconds


@dataclass
class RunStats:
    stages: dict[str, StageStats] = field(default_factory=dict)
    # Batches run in parallel threads; "x += y" is not atomic, so updates
    # take a lock to avoid losing a count.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, stage: str, response: llm.LLMResponse) -> None:
        with self._lock:
            self.stages.setdefault(stage, StageStats()).add(response)


def gate(unit: Unit, target: str, glossary: list[GlossaryEntry]) -> list[Issue]:
    return check_segment(unit.source, target, glossary=glossary,
                         max_length=unit.max_length, content_type=unit.content_type)


def term_choices(unit: Unit, draft: str, glossary: list[GlossaryEntry]) -> list[str]:
    """Ambiguous entries whose wording in the draft needs a human-like look.

    The gate can only check membership in a set; whether the choice fits this
    context needs judgement, so the editor double-checks it against the note.
    Missing an enforced entry is not listed here: that is already a gate error.
    """
    entries = find_terms(unit.source, glossary) + [e for _, e in find_ui_labels(unit.source, glossary)]
    hints = {}
    for e in entries:
        if e.kind != "ambiguous":
            continue
        used = [t for t in e.targets if t in draft]
        if used:
            hints[e.source] = (f'"{e.source}": the draft uses {used[0]}; options: '
                               f'{" / ".join(e.targets)}. {e.note}').strip()
        elif not e.enforced:
            hints[e.source] = (f'"{e.source}": usually {e.targets[0]}, which the draft does '
                               f'not use; keep the draft unless {e.targets[0]} fits better. '
                               f'{e.note}').strip()
    return list(hints.values())


def _errors(issues: list[Issue]) -> set[tuple[str, str]]:
    return {(i.check, i.message) for i in issues if i.severity == "error"}


def process_batch(units: list[Unit], config: str, glossary: list[GlossaryEntry],
                  style_rules: str, stats: RunStats) -> list[Result]:
    cfg = CONFIGS[config]
    tm_matches = {u.id: tm.default_tm().lookup(u.source) for u in units} if cfg["tm"] else None
    concordance = ({u.id: tm.default_tm().concordance(u.source) for u in units}
                   if cfg["concordance"] else None)
    context = (ctx.build_context(units, glossary, style_rules, tm_matches, concordance)
               if cfg["context"] else "")

    drafts, response = stages.translate(units, context)
    stats.add("translate", response)
    # A segment the model skipped gets an empty draft, which fails the gate
    # and goes to a human instead of crashing the run.
    targets = {u.id: drafts.get(u.id, "") for u in units}
    findings = {u.id: gate(u, targets[u.id], glossary) for u in units}
    results = {u.id: Result(u.id, u.source, targets[u.id], "auto_approved",
                            drafts={"translate": targets[u.id], "edit": None})
               for u in units}

    if cfg["edit"]:
        choices = {u.id: term_choices(u, targets[u.id], glossary) for u in units}
        edited, response = stages.edit(units, targets, findings, context, choices)
        stats.add("edit", response)
        for u in units:
            new_target = edited.get(u.id, targets[u.id])
            results[u.id].drafts["edit"] = new_target
            new_findings = gate(u, new_target, glossary)
            # Stage guard ("fix one thing, break another"): if the edit
            # introduced an error the draft did not have, keep the draft.
            if _errors(new_findings) - _errors(findings[u.id]):
                results[u.id].reverted = True
            else:
                targets[u.id], findings[u.id] = new_target, new_findings

    mqm = {}
    if cfg["verify"]:
        mqm, response = stages.verify(units, targets, context)
        stats.add("verify", response)

    for u in units:
        r = results[u.id]
        r.target = targets[u.id]
        r.issues = list(findings[u.id])
        if cfg["verify"]:
            errors = mqm.get(u.id, [])
            r.mqm_penalty = stages.mqm_penalty(errors)
            r.issues += stages.mqm_issues(errors)
        # Routing: anything with an error-level issue (gate or verifier) goes to a human.
        r.status = "auto_approved" if passed(r.issues) else "needs_review"
    return [results[u.id] for u in units]


def run(units: list[Unit], config: str = "C", glossary: list[GlossaryEntry] | None = None,
        style_rules: str | None = None) -> tuple[list[Result], RunStats]:
    """Translate units with one configuration. This is the entry point for
    other tools (e.g. the CLI's XLIFF-in/XLIFF-out command)."""
    glossary = ctx.load_glossary() if glossary is None else glossary
    style_rules = ctx.load_style_rules() if style_rules is None else style_rules
    stats = RunStats()
    # Fixed batches in input order, so identical runs send identical requests
    # (and hit the cache).
    batches = [units[i:i + BATCH_SIZE] for i in range(0, len(units), BATCH_SIZE)]
    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        batch_results = pool.map(lambda b: process_batch(b, config, glossary, style_rules, stats),
                                 batches)
        results = [r for batch in batch_results for r in batch]
    return results, stats


def count_words(source: str) -> int:
    root, _ = parse_fragment(source)
    text = "".join(text_nodes(root)) if root is not None else source
    return len(text.split())


def save_run(units: list[Unit], results: list[Result], stats: RunStats, config: str,
             run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    references = {u.id: u.reference for u in units}
    with open(run_dir / "segments.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            row = {"id": r.id, "source": r.source, "reference": references[r.id],
                   "target": r.target, "status": r.status,
                   "issues": [asdict(i) for i in r.issues], "drafts": r.drafts,
                   "reverted": r.reverted, "mqm_penalty": r.mqm_penalty}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "config": config,
        "description": CONFIGS[config]["description"],
        "model": llm.MODEL,
        "segments": len(results),
        "source_words": sum(count_words(u.source) for u in units),
        "stages": {name: {k: round(v, 6) if isinstance(v, float) else v
                          for k, v in asdict(s).items()}
                   for name, s in stats.stages.items()},
        "total_cost_usd": round(sum(s.cost_usd for s in stats.stages.values()), 6),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
