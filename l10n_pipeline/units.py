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
    """One approved term.

    scope "ui": a UI label, matched against the whole text of a <uicontrol> or
    <parmname> (docs must cite UI strings exactly). scope "text": a term in
    running text outside those tags. The same word can need different rules in
    the two places: the "New" button is 新建, "new features" are 新功能.
    """

    source: str
    target: str     # one or more acceptable translations separated by "|"; the first is preferred
    kind: Literal["required", "ambiguous"] = "required"
    note: str = ""
    scope: Literal["ui", "text"] = "text"

    @property
    def targets(self) -> list[str]:
        """All acceptable translations listed for this term."""
        return [t.strip() for t in self.target.split("|") if t.strip()]

    @property
    def enforced(self) -> bool:
        """Whether the QA gate requires one of the targets.

        required                  any listed translation is fine (variants such
                                  as 详细信息页面|详细信息页); enforced.
        ambiguous, several listed a closed set: the right one depends on context,
                                  but nothing outside the set is acceptable;
                                  enforced, and the editor double-checks the choice.
        ambiguous, one listed     guidance only ("usually X"): the reviewer could
                                  not list every correct translation (e.g. a UI
                                  word used freely in running text); not enforced.
        """
        return self.kind == "required" or len(self.targets) > 1


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
