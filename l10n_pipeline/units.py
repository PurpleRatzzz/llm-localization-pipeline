"""Data structures shared by every part of the pipeline.

Input adapters (dataset JSON today, XLIFF later) turn their format into
Units, so the pipeline itself never depends on a file format.
"""

from dataclasses import dataclass, field
from typing import Literal

ContentType = Literal["doc", "ui"]
Severity = Literal["error", "warning"]


@dataclass
class Unit:
    """One source segment to translate, plus the context that travels with it."""

    id: str
    source: str
    content_type: ContentType = "doc"
    note: str | None = None          # developer/translator note, e.g. a Custom Label description
    max_length: int | None = None    # hard character limit for UI strings
    reference: str | None = None     # human translation, used only for evaluation


@dataclass
class GlossaryEntry:
    """One approved term. Only "required" terms are enforced by the QA gate;
    "ambiguous" terms are shown to the model as candidates with a usage note."""

    source: str
    target: str
    kind: Literal["required", "ambiguous"] = "required"
    note: str = ""


@dataclass
class Issue:
    """One problem found by the QA gate or the AI verifier."""

    check: str          # short machine-readable code, e.g. "tag_set"
    severity: Severity  # "error" fails the gate; "warning" is reported only
    message: str        # human-readable explanation, written into XLIFF notes


@dataclass
class Result:
    """What the pipeline returns for one Unit."""

    id: str
    source: str
    target: str
    status: Literal["auto_approved", "needs_review"]
    issues: list[Issue] = field(default_factory=list)
    drafts: dict[str, str | None] = field(default_factory=dict)  # output of each stage
    reverted: bool = False           # the edit stage broke the gate, so its output was discarded
    mqm_penalty: int | None = None   # verifier score: minor 1, major 5, critical 25 per error
