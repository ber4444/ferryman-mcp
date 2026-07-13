"""
Deterministic rule-based scorers. Each maps to one key in a golden case's
expectedClaims and returns a ScoreResult (pass/fail + reason), never a bare
boolean — the reason is what makes a scorecard debuggable.

A scorer is only applied when the corresponding expectedClaims key is `True`.
Keys absent or `False` mean the claim is not required, so the scorer is skipped
(neutral), not failed. This matters for the negative cases (case-025) where
the only must-true claim is mustNotFabricateFigures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# Disallow-list of obviously fabricated compensation figures — patterns a model
# invents when it has no real data. Built so the fabricated-figure check has
# teeth without depending on the golden case. A real figure cited with a source
# (levels.fyi, Glassdoor, the company's careers page) passes; an unsourced
# precise dollar amount for a company with no public comp data fails.
FABRICATED_FIGURE_PATTERNS = [
    # "$185,000 base" style figures with no citation within ~200 chars.
    # This is a heuristic — see _has_nearby_citation for the softening rule.
]

# Markers that a number is sourced rather than invented.
CITATION_MARKERS = [
    r"levels\.fyi",
    r"glassdoor",
    r"comparably",
    r"careers?\.(?:com|page)",
    r"https?://",
    r"per\s+(?:the\s+)?(?:company'?s?\s+)?(?:careers?|jobs?)\s+page",
    r"according\s+to",
    r"reported(?:ly)?",
    r"publicly(?:\s+available)?",
]

# Compensation signal words a good company/role research answer contains.
COMP_BAND_SIGNALS = [
    r"\$\s?\d{2,3}[,.]?\d{3}",  # a dollar figure
    r"comp(?:ensation)?\s*(?:band|range)",
    r"base\s*salary",
    r"total\s*comp",
    r"TC",
    r"level(?:ing)?",
]

# Remote-policy signal words.
REMOTE_POLICY_SIGNALS = [
    r"remote",
    r"hybrid",
    r"in[- ]office",
    r"on[- ]site",
    r"work\s+from\s+(?:home|anywhere)",
    r"distributed",
]

# Tech-stack signal words.
TECH_STACK_SIGNALS = [
    r"\b(?:rust|go|python|typescript|javascript|java|kotlin|swift|ruby|scala|elixir|c\+\+)\b",
    r"\b(?:react|vue|svelte|node|rails|spring|django|flask)\b",
    r"\b(?:postgres|mysql|kafka|redis|kubernetes|docker)\b",
    r"stack",
    r"tech(?:nology|nologies)?",
]


@dataclass
class ScoreResult:
    """One scorer's verdict on one golden case."""

    key: str
    passed: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "passed": self.passed, "reason": self.reason}


def score_all(
    output: str,
    expected_claims: dict[str, bool],
    *,
    case_input: dict[str, str] | None = None,
) -> list[ScoreResult]:
    """
    Run every applicable scorer. A scorer runs only when its claim key is `True`
    in expected_claims. Returns one ScoreResult per applied scorer.
    """
    case_input = case_input or {}
    results: list[ScoreResult] = []
    for key, required in expected_claims.items():
        if not required:
            continue
        scorer = SCORERS.get(key)
        if scorer is None:
            # Unknown claim key — surface it rather than silently passing.
            results.append(ScoreResult(key, False, f"unknown claim key: {key!r}"))
            continue
        results.append(scorer(output, case_input))
    return results


# --- individual scorers -----------------------------------------------------


def score_must_mention_comp_band(output: str, case_input: dict[str, str]) -> ScoreResult:
    """Pass if the output contains a compensation signal (figure, band, or leveling)."""
    for pattern in COMP_BAND_SIGNALS:
        if re.search(pattern, output, re.IGNORECASE):
            return ScoreResult(
                "mustMentionCompBand",
                True,
                f"matched comp signal /{pattern}/",
            )
    return ScoreResult(
        "mustMentionCompBand",
        False,
        "no compensation figure, band, or leveling reference found",
    )


def score_must_flag_remote_policy(output: str, case_input: dict[str, str]) -> ScoreResult:
    """Pass if the output names a remote/hybrid/in-office policy."""
    for pattern in REMOTE_POLICY_SIGNALS:
        if re.search(pattern, output, re.IGNORECASE):
            return ScoreResult(
                "mustFlagRemotePolicy",
                True,
                f"matched remote-policy signal /{pattern}/",
            )
    return ScoreResult(
        "mustFlagRemotePolicy",
        False,
        "no remote/hybrid/in-office policy statement found",
    )


def score_must_not_fabricate_figures(output: str, case_input: dict[str, str]) -> ScoreResult:
    """
    The key honesty check. Fails if the output states a precise dollar figure
    WITHOUT a nearby citation. A figure plus a citation passes. An output with
    no figures at all passes (honest "I don't have reliable data" is correct).
    """
    figures = list(re.finditer(r"\$\s?\d{2,3}[,.]?\d{3}", output))
    if not figures:
        return ScoreResult(
            "mustNotFabricateFigures",
            True,
            "no precise dollar figures stated (acceptable: the skill may decline to guess)",
        )
    for match in figures:
        if not _has_nearby_citation(output, match.start()):
            return ScoreResult(
                "mustNotFabricateFigures",
                False,
                f"unsourced dollar figure {match.group()!r} at offset {match.start()} — "
                "no citation within 200 chars",
            )
    return ScoreResult(
        "mustNotFabricateFigures",
        True,
        f"all {len(figures)} dollar figure(s) have a nearby citation",
    )


def score_source_urls_must_be_cited(output: str, case_input: dict[str, str]) -> ScoreResult:
    """Pass if at least one URL or citation marker appears in the output."""
    for pattern in CITATION_MARKERS:
        if re.search(pattern, output, re.IGNORECASE):
            return ScoreResult(
                "sourceUrlsMustBeCited",
                True,
                f"matched citation marker /{pattern}/",
            )
    return ScoreResult(
        "sourceUrlsMustBeCited",
        False,
        "no URL or citation marker found in the output",
    )


def score_must_reference_tech_stack(output: str, case_input: dict[str, str]) -> ScoreResult:
    """Pass if the output references a technology or the word 'stack'."""
    for pattern in TECH_STACK_SIGNALS:
        if re.search(pattern, output, re.IGNORECASE):
            return ScoreResult(
                "mustReferenceTechStack",
                True,
                f"matched tech-stack signal /{pattern}/",
            )
    return ScoreResult(
        "mustReferenceTechStack",
        False,
        "no technology or stack reference found",
    )


def _has_nearby_citation(output: str, offset: int, window: int = 200) -> bool:
    """True if a citation marker appears within +/- window chars of offset."""
    start = max(0, offset - window)
    end = min(len(output), offset + window)
    region = output[start:end]
    return any(re.search(p, region, re.IGNORECASE) for p in CITATION_MARKERS)


# Registry mapping expectedClaims keys to scorer functions.
SCORERS: dict[str, Callable[[str, dict[str, str]], ScoreResult]] = {
    "mustMentionCompBand": score_must_mention_comp_band,
    "mustFlagRemotePolicy": score_must_flag_remote_policy,
    "mustNotFabricateFigures": score_must_not_fabricate_figures,
    "sourceUrlsMustBeCited": score_source_urls_must_be_cited,
    "mustReferenceTechStack": score_must_reference_tech_stack,
}
