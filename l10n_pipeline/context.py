"""Context assembly: everything the model is told besides the source text.

The model gets structured context, not a bare string: the zh-CN style rules
and only the glossary entries that occur in the segments of this request.
Terms are found with qa_gate.find_terms, so the model is shown exactly the
terms the gate will later enforce.
"""

import csv
import re
from pathlib import Path

from l10n_pipeline.qa_gate import find_terms
from l10n_pipeline.units import GlossaryEntry, Unit

CONTEXT_DIR = Path(__file__).resolve().parent.parent / "context"
GLOSSARY_PATH = CONTEXT_DIR / "glossary.zh-CN.csv"
STYLE_PATH = CONTEXT_DIR / "style.zh-CN.md"


def load_glossary(path: Path = GLOSSARY_PATH) -> list[GlossaryEntry]:
    # Extra columns in the CSV (e.g. "evidence") are for the human reviewer only.
    with open(path, encoding="utf-8", newline="") as f:
        return [GlossaryEntry(row["source"], row["target"], row["kind"], row.get("note", ""))
                for row in csv.DictReader(f)]


def load_style_rules(path: Path = STYLE_PATH) -> str:
    """Only the "## Rules" section goes to the model; the evidence is for humans."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^## Rules\n(.*?)(?=^## )", text, re.S | re.M)
    return match.group(1).strip()


def glossary_block(units: list[Unit], glossary: list[GlossaryEntry]) -> str:
    # One request covers several segments; list each matching term once.
    entries = {e.source: e for unit in units for e in find_terms(unit.source, glossary)}
    lines = []
    for e in sorted(entries.values(), key=lambda e: e.source.lower()):
        if e.kind == "required":
            lines.append(f"- {e.source} → {e.target} (required)")
        else:
            lines.append(f"- {e.source} → usually {e.target}; {e.note or 'choose by context'}")
    return "\n".join(lines)


def build_context(units: list[Unit], glossary: list[GlossaryEntry], style_rules: str) -> str:
    """The text that replaces {context} in a prompt template."""
    parts = ["## Style rules\n\n" + style_rules]
    terms = glossary_block(units, glossary)
    if terms:
        parts.append("## Glossary for these segments\n\n" + terms)
    return "\n" + "\n\n".join(parts) + "\n"
