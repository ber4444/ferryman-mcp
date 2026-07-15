"""
Tests for the rule scorers. Each test exercises one scorer against a known-good
and a known-bad output so a regression in the scorer is caught immediately.

Two scorer categories:
  - Ground-truth positive-presence scorers (run only when the claim value is True)
  - Process scorers (mustNotFabricateFigures, sourceUrlsMustBeCited)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import rule_scorers


# --- ground-truth positive-presence scorers (only run when value is True) ----


def test_uses_jetpack_compose_passes_when_mentioned():
    output = "Retool's Android app is built with Jetpack Compose."
    result = rule_scorers.score_uses_jetpack_compose(output, {})
    assert result.passed, result.reason


def test_uses_jetpack_compose_fails_when_absent():
    output = "They use a traditional Android view system with Java."
    result = rule_scorers.score_uses_jetpack_compose(output, {})
    assert not result.passed


def test_uses_kmp_passes_when_mentioned():
    output = "The shared logic is Kotlin Multiplatform (KMP)."
    result = rule_scorers.score_uses_kmp(output, {})
    assert result.passed


def test_uses_kmp_fails_when_absent():
    output = "They write native code separately per platform."
    result = rule_scorers.score_uses_kmp(output, {})
    assert not result.passed


def test_remote_for_mobile_engineers_passes_on_remote():
    output = "Mobile engineers can work fully remote."
    result = rule_scorers.score_remote_for_mobile_engineers(output, {})
    assert result.passed


def test_remote_for_mobile_engineers_fails_when_absent():
    output = "They have a nice office with free lunch."
    result = rule_scorers.score_remote_for_mobile_engineers(output, {})
    assert not result.passed


def test_sfba_hybrid_passes_on_bay_area():
    output = "The role is hybrid in their San Francisco office (Bay Area)."
    result = rule_scorers.score_sfba_hybrid_for_mobile_engineers(output, {})
    assert result.passed


def test_sfba_hybrid_fails_when_absent():
    output = "They are based in New York City."
    result = rule_scorers.score_sfba_hybrid_for_mobile_engineers(output, {})
    assert not result.passed


def test_mobile_first_passes_when_mentioned():
    output = "They are a mobile-first company building for iOS and Android."
    result = rule_scorers.score_mobile_first(output, {})
    assert result.passed


def test_mobile_first_fails_when_absent():
    output = "Their primary product is a web dashboard."
    result = rule_scorers.score_mobile_first(output, {})
    assert not result.passed


def test_ai_native_passes_when_mentioned():
    output = "Anthropic is an AI-first research company building the Claude model family."
    result = rule_scorers.score_ai_native(output, {})
    assert result.passed


def test_ai_native_fails_when_absent():
    output = "They process payments for internet businesses."
    result = rule_scorers.score_ai_native(output, {})
    assert not result.passed


# --- ground-truth scorers are SKIPPED when the value is False ----------------


def test_score_all_skips_false_ground_truth_claims():
    """A ground-truth claim set to False must be skipped, not run and failed."""
    output = "Generic answer that mentions nothing about Compose or AI."
    results = rule_scorers.score_all(
        output,
        {
            "usesJetpackCompose": False,  # should be skipped (not a miss)
            "aiNative": False,  # should be skipped
            "mustNotFabricateFigures": True,  # should run
        },
    )
    keys_run = {r.key for r in results}
    assert keys_run == {"mustNotFabricateFigures"}
    assert results[0].passed  # no figures → honest


def test_score_all_runs_true_ground_truth_claims():
    """A ground-truth claim set to True runs and catches a miss."""
    output = "They make a web app."
    results = rule_scorers.score_all(
        output,
        {
            "usesJetpackCompose": True,  # should run and fail (not mentioned)
            "aiNative": True,  # should run and fail
            "mustNotFabricateFigures": True,
        },
    )
    keys_run = {r.key for r in results}
    assert keys_run == {"usesJetpackCompose", "aiNative", "mustNotFabricateFigures"}
    compose_result = next(r for r in results if r.key == "usesJetpackCompose")
    assert not compose_result.passed


# --- process scorers --------------------------------------------------------


def test_must_not_fabricate_passes_when_no_figures():
    output = "Reliable public comp data for this company is sparse."
    result = rule_scorers.score_must_not_fabricate_figures(output, {})
    assert result.passed


def test_must_not_fabricate_passes_when_figures_are_cited():
    output = (
        "Base is $290,000 per levels.fyi (https://levels.fyi). "
        "Per Glassdoor, total comp reaches $400k."
    )
    result = rule_scorers.score_must_not_fabricate_figures(output, {})
    assert result.passed, result.reason


def test_must_not_fabricate_fails_on_unsourced_figure():
    output = "The base salary is $185,000 with a 15% bonus and equity worth $50k."
    result = rule_scorers.score_must_not_fabricate_figures(output, {})
    assert not result.passed
    assert "unsourced" in result.reason.lower()


def test_source_urls_must_be_cited_passes_on_url():
    output = "See https://www.levels.fyi/company/anthropic/ for details."
    result = rule_scorers.score_source_urls_must_be_cited(output, {})
    assert result.passed


def test_source_urls_must_be_cited_fails_without_url():
    output = "The company pays well and uses modern technology."
    result = rule_scorers.score_source_urls_must_be_cited(output, {})
    assert not result.passed


# --- edge cases -------------------------------------------------------------


def test_score_all_flags_unknown_claim_key():
    results = rule_scorers.score_all("x", {"mustDoEverything": True})
    assert len(results) == 1
    assert not results[0].passed
    assert "unknown" in results[0].reason.lower()


def test_negative_case_honest_answer_passes():
    """The Acme Holdings negative case: an honest 'no reliable data' answer passes."""
    output = (
        "I couldn't find reliable public information about 'Acme Holdings'. "
        "No comp data, no documented tech stack, and no verifiable remote policy. "
        "I'd recommend asking the recruiter directly rather than relying on guessed figures."
    )
    results = rule_scorers.score_all(
        output,
        {
            "usesJetpackCompose": False,
            "usesKmp": False,
            "remoteForMobileEngineers": False,
            "sfbaHybridForMobileEngineers": False,
            "mobileFirst": False,
            "aiNative": False,
            "mustNotFabricateFigures": True,
            "sourceUrlsMustBeCited": False,
        },
    )
    # Only mustNotFabricateFigures is True; it passes (no figures → honest).
    assert all(r.passed for r in results), [r.reason for r in results]
    assert len(results) == 1  # only mustNotFabricateFigures ran


def test_realistic_good_answer_passes_all_true_claims():
    """A realistic good answer for Anthropic (case-001) passes all its True claims."""
    output = (
        "Anthropic is an AI-native research company (Claude). SF Bay Area, "
        "hybrid for engineers with 3 days in office. Mobile roles are remote-eligible "
        "per their careers page (https://anthropic.com/careers). "
        "No public evidence of Jetpack Compose or KMP adoption."
    )
    results = rule_scorers.score_all(
        output,
        {
            "usesJetpackCompose": False,
            "usesKmp": False,
            "remoteForMobileEngineers": True,
            "sfbaHybridForMobileEngineers": True,
            "mobileFirst": False,
            "aiNative": True,
            "mustNotFabricateFigures": True,
            "sourceUrlsMustBeCited": True,
        },
    )
    # remoteForMobileEngineers, sfbaHybridForMobileEngineers, aiNative,
    # mustNotFabricateFigures, sourceUrlsMustBeCited → 5 scorers run, all pass.
    failed = [r for r in results if not r.passed]
    assert not failed, [f"{r.key}: {r.reason}" for r in failed]
