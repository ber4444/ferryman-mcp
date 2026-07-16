"""
promptfoo Python-assertion bridge for ferryman's rule scorers.

promptfoo invokes `get_assert(output, context)` for every test whose assertion
`type` is `python` and `value` is `file://eval_harness/promptfoo_assert.py:get_assert`.

This file does NOT reimplement the scorers. It imports `score_all` from
`eval_harness.rule_scorers` and translates its `ScoreResult` list into
promptfoo's `GradingResult` shape. The same scorers CI runs (via
`run_scorecard.py`) are the ones promptfoo runs here — one source of truth.

Semantic caveat (documented in eval_harness/README.md): promptfoo calls the model
directly with the skill prompt as a plain chat completion. The ferryman skill is
written to drive a `fetch` MCP tool; promptfoo cannot invoke that tool, so the
model is answering from parametric knowledge only. Outputs here are not
comparable 1:1 to a full ferryman pipeline run scored by `run_scorecard.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `eval_harness` importable when promptfoo runs this from the worktree root
# with an arbitrary cwd (promptfoo's Python provider does not set PYTHONPATH).
_THIS_DIR = Path(__file__).resolve().parent
_WORKTREE_ROOT = _THIS_DIR.parent
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))

from eval_harness.rule_scorers import score_all  # noqa: E402


def _vars_to_dict(context) -> dict:
    """Accept context as either an object with a `.vars` attr or a plain dict."""
    if context is None:
        return {}
    if isinstance(context, dict):
        return context.get("vars", {}) or {}
    return getattr(context, "vars", {}) or {}


def _coerce_expected_claims(raw) -> dict[str, bool]:
    """expectedClaims may arrive as a JSON string or a native dict."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def get_assert(output: str, context) -> dict:
    """
    Run every applicable rule scorer against `output` and return a promptfoo
    GradingResult.

    Each test bakes its golden case's `expectedClaims` into `vars` (see
    promptfoo_tests.py). We read them back here, call `score_all`, and fail the
    assertion if any applicable scorer returned `passed=False`. An empty result
    list means no claim was `true` for this case — that is itself a pass.
    """
    vars_ = _vars_to_dict(context)
    expected_claims = _coerce_expected_claims(vars_.get("expected_claims"))
    case_input = {
        "company": vars_.get("company", ""),
        "role": vars_.get("role", ""),
    }

    results = score_all(output, expected_claims, case_input=case_input)
    failures = [r for r in results if not r.passed]

    if not failures:
        reasons = "; ".join(r.reason for r in results) if results else "no applicable scorers (all claims false)"
        return {
            "pass": True,
            "score": 1.0,
            "reason": f"{len(results)} scorer(s) ran, all passed — {reasons}",
        }

    detail = "; ".join(f"[{r.key}] {r.reason}" for r in failures)
    passed = len(results) - len(failures)
    return {
        "pass": False,
        "score": passed / len(results) if results else 0.0,
        "reason": f"{len(failures)} of {len(results)} applicable scorer(s) failed — {detail}",
    }
