"""
LLM-as-judge scorer. Calls a separate model (different provider than the one
being evaluated, to avoid a model favoring its own outputs) with the rubric and
the skill's output, parses a structured score per criterion plus a justification.

Sanity check before trusting the judge: run the same case 3–5 times and confirm
the score doesn't swing by more than ~1.0 across runs (see tests/ for the
consistency helper). A wildly inconsistent judge is worse than no judge.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RUBRIC_PATH = Path(__file__).resolve().parent / "rubric.md"

# The judge provider. Deliberately separate from the evaluated providers — a
# judge never grades its own family (family-exclusion, see model_family()).
# Default to OpenAI gpt-4o-mini: family `gpt` has zero overlap with the
# configured evaluated providers (glm / gemini / meta), so no rows are ever
# skipped via family-exclusion. Override JUDGE_BASE_URL / JUDGE_MODEL to point
# at any other OpenAI-compatible endpoint (z.ai, a local Ollama, Azure, …).
JUDGE_BASE_URL = os.environ.get("JUDGE_BASE_URL", "https://api.openai.com/v1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-4o-mini")
JUDGE_API_KEY_ENV = "JUDGE_API_KEY"

# Company-research criterion keys (rubric.md). Kept as the module-level default
# CRITERIA so `from judge_scorer import CRITERIA` and the company-research path
# are unchanged.
CRITERIA = [
    "specificity",
    "factual_correctness",
    "source_traceability",
    "honesty_about_missing_data",
    "tone_and_structure",
]

# Chess-coach criterion keys (rubric-chess.md). These MUST match the criterion
# names the rubric actually defines — the previous code reused the company keys
# `source_traceability`/`honesty_about_missing_data`, which the chess rubric does
# not define, so the judge returned constant 5s for them (no signal). Criterion 3
# is grounding/no-fabrication, criterion 4 is reasoning quality.
CHESS_CRITERIA = [
    "specificity",
    "factual_correctness",
    "grounding",
    "reasoning_quality",
    "tone_and_structure",
]


@dataclass
class JudgeVerdict:
    """One criterion's judge score."""

    key: str
    passed: bool
    reason: str
    score: float  # 1.0–5.0

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "passed": self.passed, "reason": self.reason, "score": self.score}


def model_family(model_id: str) -> str:
    """
    Derive a model family from a model id, for the judge-exclusion rule.
    `glm-5.2` → `glm`, `claude-sonnet-4-5` → `claude`, `gpt-4o` → `gpt`.
    The family is the lowercase word before the first digit or version separator.
    """
    if not model_id:
        return "unknown"
    # Take the leading alphabetic run, stopping at a digit, '-', or '_'.
    import re
    match = re.match(r"^([a-zA-Z]+)", model_id)
    return match.group(1).lower() if match else "unknown"


@dataclass(frozen=True)
class RubricProfile:
    """A rubric's judge wiring: its criterion keys and its prompt builder.

    The prompt builder frames the grading task for the domain and injects the
    right ground truth (company-research reads expectedClaims; chess reads
    correctAnswer), so the judge's factual_correctness has something real to
    check against.
    """

    criteria: list[str]
    build_prompt: Callable[[str, str, dict], str]


def _profile_for_rubric(rubric_path: Path | None) -> RubricProfile:
    """Select the judge profile for a rubric, keyed off its filename.

    Chess uses its own criteria + correctAnswer ground truth; everything else
    uses the company-research profile. Keyed off the rubric filename so
    run_scorecard needs no change — it already passes the skill's rubric_path.
    """
    name = (rubric_path.name if rubric_path else "").lower()
    if "chess" in name:
        return RubricProfile(CHESS_CRITERIA, _build_chess_prompt)
    return RubricProfile(CRITERIA, _build_prompt)


class JudgeFamilyConflict(RuntimeError):
    """Raised when the judge model family matches the evaluated model family.

    A judge never grades its own family — per the z.ai/GLM addendum hard rule.
    The runner catches this and records a skip rather than letting it crash
    the scorecard.
    """


def judge(
    output: str,
    case: dict,
    *,
    evaluated_model: str | None = None,
    rubric_path: Path | None = None,
) -> list[JudgeVerdict]:
    """
    Score one skill output across all rubric criteria. Raises if the judge
    provider is unreachable so run_scorecard can catch and record a skip.

    If [evaluated_model] is provided, enforces the family-exclusion rule: a
    judge never grades its own family (GLM doesn't judge GLM, Claude doesn't
    judge Claude). Raises [JudgeFamilyConflict] on a collision.

    [rubric_path] overrides the default company-research rubric so a different
    skill (e.g. chess) is judged against its own rubric AND its own criterion
    keys + ground truth (see _profile_for_rubric). Defaults to RUBRIC_PATH.

    The grounding gate (company: honesty_about_missing_data; chess: grounding)
    is a hard fail below 3.0 regardless of the mean — inventing data (or board
    state / engine claims) is the failure mode this harness exists to catch.
    """
    api_key = os.environ.get(JUDGE_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"{JUDGE_API_KEY_ENV} not set — judge scorer needs a separate API key",
        )

    # Family-exclusion enforcement (z.ai/GLM addendum hard rule #5).
    judge_family = model_family(JUDGE_MODEL)
    if evaluated_model is not None:
        evaluated_family = model_family(evaluated_model)
        if judge_family == evaluated_family and judge_family != "unknown":
            raise JudgeFamilyConflict(
                f"judge family '{judge_family}' (model {JUDGE_MODEL}) cannot grade "
                f"evaluated family '{evaluated_family}' (model {evaluated_model}) — "
                "a judge never grades its own family",
            )

    profile = _profile_for_rubric(rubric_path)
    rubric = (rubric_path or RUBRIC_PATH).read_text()
    prompt = profile.build_prompt(rubric, output, case)
    raw = _call_judge(prompt, api_key)
    scores = _parse_verdicts(raw, profile.criteria)

    verdicts: list[JudgeVerdict] = []
    for crit in profile.criteria:
        score = scores.get(crit)
        if score is None:
            verdicts.append(JudgeVerdict(crit, False, f"judge did not score {crit}", 0.0))
            continue
        # All criteria pass at >=3.0. Each rubric names two hard gates (a sub-3.0
        # on either is a fail regardless of the mean): factual_correctness plus
        # honesty_about_missing_data for company-research, factual_correctness
        # plus grounding for chess. The per-criterion pass/fail is the same 3.0
        # threshold either way — the hard-gate aggregation happens downstream.
        passed = score >= 3.0
        reason = f"judge score {score:.1f}/5"
        verdicts.append(JudgeVerdict(crit, passed, reason, score))
    return verdicts


def _build_prompt(rubric: str, output: str, case: dict) -> str:
    return (
        "You are grading a mobile-engineer-fit research answer produced by another model.\n"
        "Apply the rubric below strictly. Score each criterion 1–5 and return ONLY "
        "a JSON object with these keys: "
        '{"specificity": <1-5>, "factual_correctness": <1-5>, '
        '"source_traceability": <1-5>, "honesty_about_missing_data": <1-5>, '
        '"tone_and_structure": <1-5>, '
        '"justification": "<one sentence per criterion>"}.\n\n'
        "For factual_correctness, compare the output's assertions to the case's "
        "expectedClaims ground truth below. Asserting a claim that is false in "
        "the ground truth is a 1. Correctly reporting 'no public evidence' for "
        "a false claim is a 5.\n\n"
        f"## Rubric\n\n{rubric}\n\n"
        f"## Case input\n\n{json.dumps(case.get('input', {}))}\n\n"
        f"## Ground-truth expectedClaims for this case\n\n{json.dumps(case.get('expectedClaims', {}))}\n\n"
        f"## Answer to grade\n\n{output}\n"
    )


def _build_chess_prompt(rubric: str, output: str, case: dict) -> str:
    """Judge prompt for the chess-opening-coach skill.

    Frames the task as chess coaching (not company research) and injects the
    case's objective `correctAnswer` (a UCI move or a centipawn band) as ground
    truth so factual_correctness can actually be checked. The criterion keys
    match rubric-chess.md: criterion 3 is grounding, criterion 4 is reasoning
    quality.
    """
    return (
        "You are grading a chess-coaching answer produced by another model.\n"
        "The model was given a FEN position and a question, had to reason step by "
        "step, then end with a single `FINAL ANSWER:` line — a UCI move for a "
        "tactics case, or a centipawn band (White's perspective) for a "
        "position-judgment case.\n\n"
        "Apply the rubric below strictly. Score each criterion 1–5 and return ONLY "
        "a JSON object with these keys (each key maps to the rubric criterion of "
        "the same intent): "
        '{"specificity": <1-5>, "factual_correctness": <1-5>, '
        '"grounding": <1-5>, "reasoning_quality": <1-5>, '
        '"tone_and_structure": <1-5>, '
        '"justification": "<one sentence per criterion>"}.\n\n'
        "Key → rubric criterion: specificity=Criterion 1, "
        "factual_correctness=Criterion 2, grounding=Criterion 3, "
        "reasoning_quality=Criterion 4, tone_and_structure=Criterion 5.\n\n"
        "For factual_correctness, the objectively correct answer for this case is "
        "given below as ground truth. If the model's FINAL ANSWER matches it and "
        "the reasoning explains why, score 5. If the FINAL ANSWER is a clear "
        "blunder / wrong eval band, or the reasoning contradicts the answer given, "
        "score 1. Do not reward confident-sounding prose that reaches the wrong "
        "answer.\n\n"
        f"## Rubric\n\n{rubric}\n\n"
        f"## Case input (FEN + question)\n\n{json.dumps(case.get('input', {}))}\n\n"
        f"## Ground-truth correct answer\n\n{json.dumps(case.get('correctAnswer'))}\n\n"
        f"## Answer to grade\n\n{output}\n"
    )


def _call_judge(prompt: str, api_key: str) -> str:
    """Call the OpenAI-compatible judge endpoint and return the raw text."""
    try:
        import urllib.error
        import urllib.request
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("urllib unavailable") from e

    payload = json.dumps(
        {
            "model": JUDGE_MODEL,
            "messages": [
                {"role": "system", "content": "You are a strict but fair grader. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 800,
            "temperature": 0.0,
        },
    ).encode()
    req = urllib.request.Request(
        f"{JUDGE_BASE_URL.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(f"judge endpoint unreachable: {e}") from e
    return body["choices"][0]["message"]["content"]


def _parse_verdicts(raw: str, criteria: list[str] | None = None) -> dict[str, float]:
    """Extract the per-criterion scores from the judge's JSON response."""
    criteria = criteria if criteria is not None else CRITERIA
    # Tolerate preamble/trailing text by locating the first {...} block.
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < 0:
        return {}
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return {
        crit: float(parsed[crit])
        for crit in criteria
        if crit in parsed and isinstance(parsed[crit], (int, float))
    }
