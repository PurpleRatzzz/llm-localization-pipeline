"""Tests for scripts/style_cost.py (no API call, committed runs only)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))   # scripts/ is not a package

import style_cost  # noqa: E402


def test_word_is_deleted_only_where_the_reference_lacks_it():
    hyps = ["请单击保存。", "请单击保存。"]
    refs = ["单击“保存”。", "请单击“保存”。"]
    assert style_cost.without(hyps, refs, "请") == ["单击保存。", "请单击保存。"]
