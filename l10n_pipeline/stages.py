"""The three LLM stages: translate, edit, verify.

Each stage sends one batch of segments in one request and returns a result
per segment id. The prompts live in prompts/*.md, so changing a prompt is a
reviewable diff in Git, not a code change.
"""

import json
from pathlib import Path

from l10n_pipeline import llm
from l10n_pipeline.units import Issue, Unit

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Same JSON shape for translate and edit: one target per segment id.
TARGETS_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "target": {"type": "string"}},
                "required": ["id", "target"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}

MQM_CATEGORIES = ["accuracy", "terminology", "fluency", "style", "locale_convention", "markup"]
# "not_an_error" is an escape hatch: in a tune run the verifier wrote "no
# real error" in its explanation and still had to pick a severity. Those
# entries are dropped in code.
MQM_SEVERITIES = ["not_an_error", "minor", "major", "critical"]
# Standard MQM penalty weights. The total is computed in code, not by the model,
# so the score is consistent: the model only classifies each error.
MQM_WEIGHTS = {"minor": 1, "major": 5, "critical": 25}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "errors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            # The model writes fields in this order: the
                            # explanation first, then the verdict based on it.
                            "properties": {
                                "explanation": {"type": "string"},
                                "category": {"type": "string", "enum": MQM_CATEGORIES},
                                "severity": {"type": "string", "enum": MQM_SEVERITIES},
                            },
                            "required": ["explanation", "category", "severity"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "errors"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evaluations"],
    "additionalProperties": False,
}


def _system_prompt(stage: str, context: str) -> str:
    template = (PROMPTS_DIR / f"{stage}.md").read_text(encoding="utf-8")
    # str.replace, not str.format: the context contains literal braces like {0}.
    return template.replace("{context}", context)


def _user_message(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, indent=1)


def _unit_payload(unit: Unit) -> dict:
    item = {"id": unit.id, "source": unit.source}
    # Only send what exists, so doc segments and UI strings share one prompt.
    if unit.note:
        item["note"] = unit.note
    if unit.max_length:
        item["max_length"] = unit.max_length
    return item


def translate(units: list[Unit], context: str) -> tuple[dict[str, str], llm.LLMResponse]:
    response = llm.call_json(
        system=_system_prompt("translate", context),
        user=_user_message([_unit_payload(u) for u in units]),
        schema=TARGETS_SCHEMA,
    )
    return {t["id"]: t["target"] for t in response.data["translations"]}, response


def edit(units: list[Unit], drafts: dict[str, str], findings: dict[str, list[Issue]],
         context: str, choices: dict[str, list[str]] | None = None
         ) -> tuple[dict[str, str], llm.LLMResponse]:
    items = []
    for unit in units:
        item = _unit_payload(unit)
        item["draft"] = drafts[unit.id]
        # The editor sees the gate's errors, so it fixes known problems first.
        # Warnings are left out: on human references most of them are false
        # positives (numbers 10/11, tag_order 12/12), and in the first tune run
        # the editor "fixed" a numbers warning by turning a correct 1000 万
        # back into "10 million".
        item["qa_findings"] = [f"{i.check}: {i.message}"
                               for i in findings[unit.id] if i.severity == "error"]
        # Context-dependent terms where the draft picked one of the approved
        # options: the gate cannot judge the choice, the editor can.
        if choices and choices.get(unit.id):
            item["term_choices"] = choices[unit.id]
        items.append(item)
    response = llm.call_json(
        system=_system_prompt("edit", context),
        user=_user_message(items),
        schema=TARGETS_SCHEMA,
    )
    return {t["id"]: t["target"] for t in response.data["translations"]}, response


def verify(units: list[Unit], targets: dict[str, str],
           context: str) -> tuple[dict[str, list[dict]], llm.LLMResponse]:
    items = [{"id": u.id, "source": u.source, "target": targets[u.id]} for u in units]
    response = llm.call_json(
        system=_system_prompt("verify", context),
        user=_user_message(items),
        schema=VERIFY_SCHEMA,
    )
    errors = {e["id"]: [err for err in e["errors"] if err["severity"] != "not_an_error"]
              for e in response.data["evaluations"]}
    return errors, response


def mqm_penalty(errors: list[dict]) -> int:
    return sum(MQM_WEIGHTS[e["severity"]] for e in errors)


def mqm_issues(errors: list[dict]) -> list[Issue]:
    # Major and critical errors block auto-approval, like a gate error.
    return [Issue(f"mqm_{e['category']}", "error" if e["severity"] != "minor" else "warning",
                  f"[{e['severity']}] {e['explanation']}")
            for e in errors]
