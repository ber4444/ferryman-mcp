"""
Judge consistency sanity check.

Per the z.ai/GLM addendum (M3) and the eval-harness plan: before trusting judge
scores in the scorecard, run the same case through the judge 3–5 times and
confirm the score doesn't swing by more than ~1.0 across runs. A wildly
inconsistent judge is worse than no judge.

This file provides a `judge_variance()` helper that does the repeats and reports
the per-criterion spread, plus unit tests that:
  1. Verify the variance helper computes the spread correctly on a stub.
  2. Verify `model_family()` derives the family used by the exclusion rule.
  3. Verify the family-exclusion rule raises on a collision and passes on a
     cross-family judge/evaluated pair.

The live 3–5× repeat requires a real judge endpoint (JUDGE_API_KEY) and is run
manually, not in CI — see README.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import judge_scorer
from eval_harness.judge_scorer import CRITERIA, JudgeFamilyConflict, model_family


def judge_variance(
    judge_fn,
    output: str,
    case: dict,
    *,
    repeats: int = 5,
) -> dict[str, dict[str, float]]:
    """
    Run [judge_fn] (a callable matching judge_scorer.judge's signature)
    [repeats] times on the same case and report per-criterion stats.

    Returns {criterion: {"mean": float, "stdev": float, "spread": float}} where
    spread is max−min. A spread > 1.0 is the "don't trust this judge" signal.
    """
    per_criterion: dict[str, list[float]] = {c: [] for c in CRITERIA}
    for _ in range(repeats):
        verdicts = judge_fn(output, case)
        for v in verdicts:
            per_criterion[v.key].append(v.score)
    return {
        c: {
            "mean": statistics.mean(scores),
            "stdev": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
            "spread": max(scores) - min(scores) if scores else 0.0,
        }
        for c, scores in per_criterion.items()
        if scores
    }


# --- tests ------------------------------------------------------------------


def test_model_family_extracts_leading_alpha():
    assert model_family("glm-5.2") == "glm"
    assert model_family("claude-sonnet-4-5") == "claude"
    assert model_family("gpt-4o") == "gpt"
    assert model_family("llama3") == "llama"


def test_model_family_handles_edge_cases():
    assert model_family("") == "unknown"
    assert model_family("123-abc") == "unknown"


def test_judge_variance_reports_spread_on_stub():
    """The variance helper correctly detects an inconsistent stub judge."""
    # A stub judge that returns different scores each call (inconsistent).
    call_count = {"n": 0}
    flaky_scores = [5.0, 3.0, 4.0, 2.0, 5.0]

    def flaky_judge(output, case, **kwargs):
        score = flaky_scores[call_count["n"] % len(flaky_scores)]
        call_count["n"] += 1
        return [judge_scorer.JudgeVerdict(c, score >= 3.0, f"stub {score}", score) for c in CRITERIA]

    stats = judge_variance(flaky_judge, "test output", {"input": {}}, repeats=5)
    # Spread should be 3.0 (max 5 - min 2) — well above the 1.0 trust threshold.
    for crit_stats in stats.values():
        assert crit_stats["spread"] == 3.0
        assert crit_stats["stdev"] > 0


def test_judge_variance_reports_zero_spread_on_consistent_stub():
    """A perfectly consistent stub judge reports zero spread."""

    def consistent_judge(output, case, **kwargs):
        return [judge_scorer.JudgeVerdict(c, True, "stub", 4.0) for c in CRITERIA]

    stats = judge_variance(consistent_judge, "test", {"input": {}}, repeats=5)
    for crit_stats in stats.values():
        assert crit_stats["spread"] == 0.0
        assert crit_stats["mean"] == 4.0


def test_family_exclusion_rule_collides_on_same_family(monkeypatch):
    """judge() raises JudgeFamilyConflict when judge and evaluated share a family."""
    # Force the judge model to be a GLM model, and evaluate a GLM model.
    monkeypatch.setattr(judge_scorer, "JUDGE_MODEL", "glm-5.2")
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key-for-test")

    try:
        judge_scorer.judge("output", {"input": {}}, evaluated_model="glm-4.6")
        assert False, "expected JudgeFamilyConflict"
    except JudgeFamilyConflict as e:
        assert "glm" in str(e).lower()


def test_family_exclusion_rule_passes_on_cross_family(monkeypatch):
    """judge() proceeds (to the network call) when families differ."""
    monkeypatch.setattr(judge_scorer, "JUDGE_MODEL", "glm-5.2")
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key-for-test")

    # Cross-family: GLM judging Claude. Should NOT raise JudgeFamilyConflict.
    # It will fail at the network call (no real endpoint), which is fine — the
    # exclusion rule itself passed.
    try:
        judge_scorer.judge("output", {"input": {}}, evaluated_model="claude-sonnet-4-5")
    except JudgeFamilyConflict:
        assert False, "cross-family judge should not raise JudgeFamilyConflict"
    except Exception:
        pass  # network error expected — the exclusion check already passed


def test_family_exclusion_skipped_without_evaluated_model(monkeypatch):
    """judge() with no evaluated_model skips the exclusion check (back-compat)."""
    monkeypatch.setattr(judge_scorer, "JUDGE_MODEL", "glm-5.2")
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key-for-test")

    try:
        judge_scorer.judge("output", {"input": {}}, evaluated_model=None)
    except JudgeFamilyConflict:
        assert False, "no evaluated_model means no exclusion check"
    except Exception:
        pass  # network error expected


# --- rubric-profile selection (domain-aware judge) -------------------------


def test_chess_rubric_selects_chess_profile():
    """A chess rubric path routes to the chess criteria + prompt builder."""
    from pathlib import Path

    profile = judge_scorer._profile_for_rubric(Path("eval_harness/rubric-chess.md"))
    assert profile.criteria == judge_scorer.CHESS_CRITERIA
    # The two degenerate company keys must not be graded for chess.
    assert "source_traceability" not in profile.criteria
    assert "honesty_about_missing_data" not in profile.criteria
    # The chess-specific criteria that used to be missing must be present.
    assert "grounding" in profile.criteria
    assert "reasoning_quality" in profile.criteria


def test_non_chess_rubric_uses_company_profile():
    """Any non-chess (or absent) rubric keeps the company-research profile."""
    from pathlib import Path

    assert judge_scorer._profile_for_rubric(Path("eval_harness/rubric.md")).criteria == judge_scorer.CRITERIA
    assert judge_scorer._profile_for_rubric(None).criteria == judge_scorer.CRITERIA


def test_chess_prompt_injects_correct_answer_not_expected_claims():
    """The chess judge prompt grades against correctAnswer, not expectedClaims."""
    case = {
        "input": {"fen": "8/8/8/8/8/8/8/8 w - -", "question": "Find best move."},
        "correctAnswer": "e2e4",
        "expectedClaims": {},  # chess cases carry none — must not be referenced
    }
    prompt = judge_scorer._build_chess_prompt("RUBRIC", "FINAL ANSWER: e2e4", case)
    assert "e2e4" in prompt and "Ground-truth correct answer" in prompt
    assert "chess-coaching answer" in prompt
    assert "expectedClaims" not in prompt
    assert "mobile-engineer" not in prompt
    # Judge is asked for the chess keys only.
    assert '"grounding"' in prompt and '"reasoning_quality"' in prompt
    assert "source_traceability" not in prompt
