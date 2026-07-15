"""
Deterministic rule-based scorers. Each maps to one key in a golden case's
expectedClaims and returns a ScoreResult (pass/fail + reason), never a bare
boolean.

Two kinds of claim:

1. **Ground-truth correctness claims** (usesJetpackCompose, usesKmp,
   remoteForMobileEngineers, sfbaHybridForMobileEngineers, mobileFirst, aiNative).
   These carry a per-company true/false value that is the actual ground truth
   (verified by manual research at freeze time). When the value is `true`, a rule
   scorer does a cheap positive-presence check — if the output never mentions the
   concept, that's a definite miss. When the value is `false`, a rule can't tell
   "correctly reports no evidence" from "forgot to mention it" — that's the
   judge's job. So ground-truth scorers only run when the claim value is `true`.

2. **Process claims** (mustNotFabricateFigures, sourceUrlsMustBeCited). These run
   whenever the claim key is present and `true`, regardless of ground-truth value.
   They're the honesty gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

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
    Run every applicable scorer. For ground-truth claims, the scorer runs only
    when the value is `true` (the case where a cheap positive check has teeth).
    For process claims, the scorer runs whenever the key is present and `true`.
    """
    case_input = case_input or {}
    results: list[ScoreResult] = []
    for key, value in expected_claims.items():
        scorer = SCORERS.get(key)
        if scorer is None:
            # Unknown claim key — surface it rather than silently passing.
            results.append(ScoreResult(key, False, f"unknown claim key: {key!r}"))
            continue
        # Ground-truth scorers only run when the value is True.
        # Process scorers run when present and True.
        if value is not True:
            continue
        results.append(scorer(output, case_input))
    return results


# --- process scorers --------------------------------------------------------


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


# --- ground-truth positive-presence scorers ---------------------------------
#
# Each runs only when the ground-truth value is `true`. If the output never
# mentions the concept, that's a definite miss. When the value is `false`, the
# scorer is skipped — only the judge can assess whether the skill correctly
# reported "no public evidence" vs. lazily omitted it.


def score_uses_jetpack_compose(output: str, case_input: dict[str, str]) -> ScoreResult:
    return _positive_presence(
        "usesJetpackCompose",
        output,
        [r"jetpack\s*compose", r"\bcompose\b(?!.*music)"],
        "Jetpack Compose",
    )


def score_uses_kmp(output: str, case_input: dict[str, str]) -> ScoreResult:
    return _positive_presence(
        "usesKmp",
        output,
        [r"kotlin\s*multiplatform", r"\bkmp\b", r"kotlin/native"],
        "Kotlin Multiplatform",
    )


def score_remote_for_mobile_engineers(output: str, case_input: dict[str, str]) -> ScoreResult:
    return _positive_presence(
        "remoteForMobileEngineers",
        output,
        [r"remote", r"work\s+from\s+(?:home|anywhere)", r"distributed"],
        "remote work",
    )


def score_sfba_hybrid_for_mobile_engineers(output: str, case_input: dict[str, str]) -> ScoreResult:
    return _positive_presence(
        "sfbaHybridForMobileEngineers",
        output,
        [r"hybrid", r"bay\s*area", r"san\s+francisco", r"\bsf\b", r"palo\s+alto", r"mountain\s+view"],
        "SF Bay Area hybrid",
    )


def score_mobile_first(output: str, case_input: dict[str, str]) -> ScoreResult:
    return _positive_presence(
        "mobileFirst",
        output,
        [r"mobile[- ]first", r"mobile[- ]native", r"mobile[- ]centric", r"android.{0,30}ios.{0,30}primary"],
        "mobile-first",
    )


def score_ai_native(output: str, case_input: dict[str, str]) -> ScoreResult:
    return _positive_presence(
        "aiNative",
        output,
        [r"ai[- ]native", r"ai[- ]first", r"ai\s+company", r"ai\s+research", r"llm", r"machine\s+learning\s+company"],
        "AI-native",
    )


def _positive_presence(key: str, output: str, patterns: list[str], label: str) -> ScoreResult:
    """Shared positive-presence check: fail if the concept is absent (ground truth is true)."""
    for pattern in patterns:
        if re.search(pattern, output, re.IGNORECASE):
            return ScoreResult(key, True, f"matched {label} signal /{pattern}/")
    return ScoreResult(key, False, f"ground truth says {label}=true but output never mentions it")


def _has_nearby_citation(output: str, offset: int, window: int = 200) -> bool:
    """True if a citation marker appears within +/- window chars of offset."""
    start = max(0, offset - window)
    end = min(len(output), offset + window)
    region = output[start:end]
    return any(re.search(p, region, re.IGNORECASE) for p in CITATION_MARKERS)


# Registry mapping expectedClaims keys to scorer functions.
# Ground-truth keys only run when value is True (see score_all).
SCORERS: dict[str, Callable[[str, dict[str, str]], ScoreResult]] = {
    "usesJetpackCompose": score_uses_jetpack_compose,
    "usesKmp": score_uses_kmp,
    "remoteForMobileEngineers": score_remote_for_mobile_engineers,
    "sfbaHybridForMobileEngineers": score_sfba_hybrid_for_mobile_engineers,
    "mobileFirst": score_mobile_first,
    "aiNative": score_ai_native,
    "mustNotFabricateFigures": score_must_not_fabricate_figures,
    "sourceUrlsMustBeCited": score_source_urls_must_be_cited,
}
