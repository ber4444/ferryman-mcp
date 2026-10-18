"""Tests for compare_runs.py — the paired before/after comparison."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import compare_runs


def _row(case_id: str, passed: bool, *, provider="hf-llama", error=None, hardness="easy", split="hillclimb", sample_rows=0):
    return {
        "id": case_id,
        "provider": provider,
        "error": error,
        "rule_scores": [] if error else [{"key": "executionAccuracy", "passed": passed, "reason": ""}],
        "meta": {"hardness": hardness, "split": split, "sampleRows": sample_rows},
    }


@pytest.mark.parametrize(
    "fixed, broke, expected",
    [
        (0, 0, 1.0),
        (5, 5, 1.0),
        (10, 0, 2 / 1024),  # 2 * P(X <= 0), X ~ Bin(10, 0.5)
        (0, 10, 2 / 1024),
        (9, 1, 2 * 11 / 1024),  # 2 * (C(10,0) + C(10,1)) / 2^10
    ],
)
def test_mcnemar_exact_p(fixed, broke, expected):
    assert compare_runs.mcnemar_exact_p(fixed, broke) == pytest.approx(expected)


def test_compare_counts_flips_and_excludes_errored_pairs():
    before = [_row("a", False), _row("b", True), _row("c", True), _row("d", False), _row("e", False)]
    after = [_row("a", True), _row("b", False), _row("c", True), _row("d", False, error="timeout"), _row("f", True)]
    (cmp,) = compare_runs.compare(before, after, "executionAccuracy")
    t = cmp.total
    assert (t.pairs, t.before, t.after, t.fixed, t.broke) == (3, 2, 2, 1, 1)
    assert t.fixed_ids == ["a"] and t.broke_ids == ["b"]
    assert cmp.errored == 1 and cmp.unmatched == 2  # d errored; e and f unmatched


def test_compare_breaks_down_by_meta():
    before = [_row("a", False, hardness="easy"), _row("b", False, hardness="hard")]
    after = [_row("a", True, hardness="easy"), _row("b", False, hardness="hard")]
    (cmp,) = compare_runs.compare(before, after, "executionAccuracy", by=("hardness",))
    assert cmp.groups["hardness"]["easy"].fixed == 1
    assert cmp.groups["hardness"]["hard"].fixed == 0


def test_render_reports_delta_pvalue_and_setting_change(tmp_path):
    before = [_row(str(i), i < 5) for i in range(10)]
    after = [_row(str(i), i < 8, sample_rows=3) for i in range(10)]
    text = compare_runs.render(Path("b.json"), Path("a.json"), before, after, "executionAccuracy", ("hardness",))
    assert "| hf-llama | 10 | 5/10 (50%) | 8/10 (80%) | +30 pp | 3 | 0 | 0.25 |" in text
    assert "sampleRows [0] → [3]" in text
    assert "## By hardness" in text and "fixed: 5, 6, 7" in text


def test_main_reads_two_snapshots(tmp_path, capsys):
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    b.write_text(json.dumps([_row("x", False)]))
    a.write_text(json.dumps([_row("x", True)]))
    assert compare_runs.main([str(b), str(a), "--out", str(tmp_path / "cmp.md")]) == 0
    assert "+100 pp" in capsys.readouterr().out
    assert (tmp_path / "cmp.md").exists()
