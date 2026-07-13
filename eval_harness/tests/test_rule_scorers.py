"""
Tests for the rule scorers. Each test exercises one scorer against a known-good
and a known-bad output so a regression in the scorer is caught immediately.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import rule_scorers


def test_must_mention_comp_band_passes_on_dollar_figure():
    output = "Anthropic's L4 base is around $290,000 per levels.fyi."
    result = rule_scorers.score_must_mention_comp_band(output, {})
    assert result.passed, result.reason


def test_must_mention_comp_band_passes_on_band_keyword():
    output = "Comp band for this level is published on their careers page."
    result = rule_scorers.score_must_mention_comp_band(output, {})
    assert result.passed


def test_must_mention_comp_band_fails_when_absent():
    output = "Anthropic makes the Claude model family. They have an SF office."
    result = rule_scorers.score_must_mention_comp_band(output, {})
    assert not result.passed


def test_must_flag_remote_policy_passes_on_hybrid():
    output = "The role is hybrid — 3 days in the SF office."
    result = rule_scorers.score_must_flag_remote_policy(output, {})
    assert result.passed


def test_must_flag_remote_policy_fails_when_absent():
    output = "They use Python and TypeScript for the backend."
    result = rule_scorers.score_must_flag_remote_policy(output, {})
    assert not result.passed


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


def test_must_reference_tech_stack_passes_on_named_language():
    output = "The backend is primarily Rust with some Go services."
    result = rule_scorers.score_must_reference_tech_stack(output, {})
    assert result.passed


def test_must_reference_tech_stack_fails_when_absent():
    output = "They are a well-known company with good benefits."
    result = rule_scorers.score_must_reference_tech_stack(output, {})
    assert not result.passed


def test_score_all_skips_non_required_claims():
    """A claim key set to False must be skipped (neutral), not failed."""
    output = "Generic answer with no specifics."
    results = rule_scorers.score_all(
        output,
        {
            "mustMentionCompBand": False,
            "mustFlagRemotePolicy": False,
            "mustNotFabricateFigures": True,
            "sourceUrlsMustBeCited": False,
            "mustReferenceTechStack": False,
        },
    )
    # Only mustNotFabricateFigures was required; it passes (no figures stated).
    assert len(results) == 1
    assert results[0].key == "mustNotFabricateFigures"
    assert results[0].passed


def test_score_all_flags_unknown_claim_key():
    results = rule_scorers.score_all(
        "x",
        {"mustDoEverything": True},
    )
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
            "mustMentionCompBand": False,
            "mustFlagRemotePolicy": False,
            "mustNotFabricateFigures": True,
            "sourceUrlsMustBeCited": False,
            "mustReferenceTechStack": False,
        },
    )
    assert all(r.passed for r in results), [r.reason for r in results]
