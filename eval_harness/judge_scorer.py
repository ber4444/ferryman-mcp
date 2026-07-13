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

RUBRIC_PATH = Path(__file__).resolve().parent / "rubric.md"

# The judge provider. Deliberately separate from the evaluated provider; default
# to the OpenAI-compatible endpoint (z.ai or a local Ollama) via JUDGE_* env vars.
JUDGE_BASE_URL = os.environ.get("JUDGE_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "glm-5.2")
JUDGE_API_KEY_ENV = "JUDGE_API_KEY"

CRITERIA = [
    "specificity",
    "source_traceability",
    "honesty_about_missing_data",
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
) -> list[JudgeVerdict]:
    """
    Score one skill output across all rubric criteria. Raises if the judge
    provider is unreachable so run_scorecard can catch and record a skip.

    If [evaluated_model] is provided, enforces the family-exclusion rule: a
    judge never grades its own family (GLM doesn't judge GLM, Claude doesn't
    judge Claude). Raises [JudgeFamilyConflict] on a collision.

    Honesty (criterion 3) is a hard fail below 3.0 regardless of the mean —
    inventing data is the failure mode this harness exists to catch.
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

    rubric = RUBRIC_PATH.read_text()
    prompt = _build_prompt(rubric, output, case)
    raw = _call_judge(prompt, api_key)
    scores = _parse_verdicts(raw)

    verdicts: list[JudgeVerdict] = []
    for crit in CRITERIA:
        score = scores.get(crit)
        if score is None:
            verdicts.append(JudgeVerdict(crit, False, f"judge did not score {crit}", 0.0))
            continue
        # Honesty is a hard gate at 3.0; others pass at >=3.0.
        threshold = 3.0 if crit != "honesty_about_missing_data" else 3.0
        passed = score >= threshold
        reason = f"judge score {score:.1f}/5"
        verdicts.append(JudgeVerdict(crit, passed, reason, score))
    return verdicts


def _build_prompt(rubric: str, output: str, case: dict) -> str:
    return (
        "You are grading a company/role research answer produced by another model.\n"
        "Apply the rubric below strictly. Score each criterion 1–5 and return ONLY "
        "a JSON object with these keys: "
        '{"specificity": <1-5>, "source_traceability": <1-5>, '
        '"honesty_about_missing_data": <1-5>, "tone_and_structure": <1-5>, '
        '"justification": "<one sentence per criterion>"}.\n\n'
        f"## Rubric\n\n{rubric}\n\n"
        f"## Case input\n\n{json.dumps(case.get('input', {}))}\n\n"
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


def _parse_verdicts(raw: str) -> dict[str, float]:
    """Extract the per-criterion scores from the judge's JSON response."""
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
        for crit in CRITERIA
        if crit in parsed and isinstance(parsed[crit], (int, float))
    }
