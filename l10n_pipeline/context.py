"""Context assembly: everything the model is told besides the source text.

The model gets structured context, not a bare string: the zh-CN style rules
and only the glossary entries that occur in the segments of this request,
split by scope (UI labels inside <uicontrol>/<parmname>, terms in running
text). Entries are found with the gate's own matchers (find_ui_labels,
find_terms), so the model is shown exactly what the gate will enforce.
"""

import csv
import re
from pathlib import Path

from l10n_pipeline.qa_gate import find_terms, find_ui_labels
from l10n_pipeline.units import GlossaryEntry, Unit

CONTEXT_DIR = Path(__file__).resolve().parent.parent / "context"
GLOSSARY_PATH = CONTEXT_DIR / "glossary.zh-CN.csv"
STYLE_PATH = CONTEXT_DIR / "style.zh-CN.md"


def load_glossary(path: Path = GLOSSARY_PATH) -> list[GlossaryEntry]:
    # Extra columns in the CSV ("review", "evidence") are for the human reviewer only.
    # utf-8-sig also reads files without a byte-order mark, so a CSV saved by
    # Excel ("CSV UTF-8") or by a text editor both load.
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [GlossaryEntry(row["source"], row["target"], row["kind"], row.get("note", ""),
                              row.get("scope", "text"))
                for row in csv.DictReader(f)]


def load_style_rules(path: Path = STYLE_PATH) -> str:
    """Only the "## Rules" section goes to the model; the evidence is for humans."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^## Rules\n(.*?)(?=^## )", text, re.S | re.M)
    return match.group(1).strip()


def _lines(entries: list[GlossaryEntry]) -> str:
    # One request covers several segments; list each matching entry once.
    unique = {e.source: e for e in entries}
    lines = []
    for e in sorted(unique.values(), key=lambda e: e.source.lower()):
        if e.kind == "required":
            lines.append(f"- {e.source} → {e.target} (required)")
        else:
            lines.append(f"- {e.source} → usually {e.target}; {e.note or 'choose by context'}")
    return "\n".join(lines)


def tm_block(units: list[Unit], tm_matches: dict[str, list[dict]]) -> str:
    lines = []
    for unit in units:
        for match in tm_matches.get(unit.id, []):
            lines += [f"- For segment {unit.id} ({match['score']}% match):",
                      f"  EN: {match['source']}",
                      f"  ZH: {match['target']}"]
    return "\n".join(lines)


def build_context(units: list[Unit], glossary: list[GlossaryEntry], style_rules: str,
                  tm_matches: dict[str, list[dict]] | None = None) -> str:
    """The text that replaces {context} in a prompt template.

    tm_matches (config D only): fuzzy matches per unit id. They go into the
    context block rather than the prompt template, so configs A-C send exactly
    the same requests as before and the comparison isolates the TM's effect.
    """
    parts = ["## Style rules\n\n" + style_rules]
    ui = [e for unit in units for _, e in find_ui_labels(unit.source, glossary)]
    if ui:
        parts.append("## UI labels in these segments\n\n"
                     "When the WHOLE text of a <uicontrol> or <parmname> is one of these "
                     "labels, translate it exactly like this:\n\n" + _lines(ui))
    text = [e for unit in units for e in find_terms(unit.source, glossary)]
    if text:
        parts.append("## Terms in the running text of these segments\n\n" + _lines(text))
    tm = tm_block(units, tm_matches or {})
    if tm:
        parts.append("## Translation memory for these segments\n\n"
                     "Approved translations of similar (not identical) segments from the same "
                     "documentation. Reuse their terminology and phrasing where they apply, but "
                     "translate what each source actually says.\n\n" + tm)
    return "\n" + "\n\n".join(parts) + "\n"
