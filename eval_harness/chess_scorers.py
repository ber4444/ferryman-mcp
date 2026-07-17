"""
Deterministic chess scorers for the chess-opening-coach skill.

Unlike the company-research rule scorers (positive-presence + citation checks),
these are **objective exact-match** scorers: the golden set (ChessQA-derived)
carries a single `correctAnswer` per case, and the model's answer is extracted
via the `FINAL ANSWER:` contract and compared. No judge, no substring fuzziness
— this is the maturity floor the chess-app repo's concept-substring scorer lacks.

Two answer formats, selected by the case's `answerFormat`:

  * "uci"             — a best move in UCI notation (e.g. "e2e4", "c2b1q").
                        Normalized to lowercase before comparison.
  * "centipawn-band"  — one of {-400, -200, 0, 200, 400}. Exact string match.

Plus a `forbiddenPhrases` honesty gate, ported verbatim from the chess app's
MoveCoachResponseValidator / PositionChatValidator: mentioning engine depth,
ELO, or unsupported certainty ("forced mate") is a hard fail regardless of
whether the final answer is correct.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Reused from rule_scorers so both skills share one verdict shape and the
# scorecard renderer needs no per-skill branching.
from eval_harness.rule_scorers import ScoreResult


# --- the FINAL ANSWER extraction contract (ported from ChessQA) -------------
#
# Source: CSSLab/chessqa-benchmark scripts/chessqa_prompt_utils.py, verbatim
# regex. The skill prompt instructs the model to end with a single line
# "FINAL ANSWER: <answer>". When multiple matches exist, the LAST one wins
# (a model that corrects itself mid-response). Fallback: a LaTeX \boxed{} form.
# Ported, not reimplemented, so our scoring matches ChessQA's published method.
_FINAL_ANSWER_PATTERN = re.compile(r"FINAL ANSWER:\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL)
_BOXED_PATTERN = re.compile(r"[Tt]he\s+final\s+answer\s+is\s+\$?\\boxed\{([^}]+)\}\$?")


def extract_final_answer(text: str) -> tuple[str, bool]:
    """
    Extract the model's final answer per the ChessQA contract.

    Returns ``(answer, found)``. ``found`` is False when neither the
    ``FINAL ANSWER:`` marker nor a ``\\boxed{}`` fallback is present.
    """
    matches = list(_FINAL_ANSWER_PATTERN.finditer(text or ""))
    if not matches:
        boxed = _BOXED_PATTERN.findall(text or "")
        if boxed:
            return boxed[-1].strip(), True
        return "", False
    answer = matches[-1].group(1).strip()
    answer = re.sub(r"^FINAL ANSWER:\s*", "", answer, flags=re.IGNORECASE).strip()
    answer = answer.strip("*").strip()
    return answer, True


# --- normalization ---------------------------------------------------------


def _normalize_uci(move: str) -> str:
    """
    Normalize a UCI move string for comparison.

    UCI is from-square + to-square + optional promotion piece, all lowercase
    (e.g. ``e2e4``, ``g1f3``, ``e7e8q``). We lowercase and strip so a model
    that emits ``E2E4`` or `` e2e4 `` still matches. We do NOT translate SAN —
    the skill prompt asks for UCI, and accepting SAN would require a board
    parser (a deliberate scope boundary; the canonical curation set can add it).
    """
    return re.sub(r"\s+", "", move).lower()


# --- scorers ---------------------------------------------------------------


def score_exact_move(output: str, case: dict) -> ScoreResult:
    """
    Score a UCI best-move case: extract the model's answer and exact-match it
    (after normalization) against ``case["correctAnswer"]``.
    """
    answer, found = extract_final_answer(output)
    if not found:
        return ScoreResult(
            "exactMove",
            False,
            "no FINAL ANSWER line found — cannot score a move case without it",
        )
    got = _normalize_uci(answer)
    want = _normalize_uci(case["correctAnswer"])
    if got == want:
        return ScoreResult("exactMove", True, f"move {got} matches expected {want}")
    return ScoreResult("exactMove", False, f"move {got!r} != expected {want!r}")


def score_eval_band(output: str, case: dict) -> ScoreResult:
    """
    Score a centipawn-band (position-judgment) case: exact-match the extracted
    answer against one of {-400, -200, 0, 200, 400}.
    """
    answer, found = extract_final_answer(output)
    if not found:
        return ScoreResult(
            "evalBand",
            False,
            "no FINAL ANSWER line found — cannot score a position-judgment case without it",
        )
    got = answer.strip()
    want = str(case["correctAnswer"]).strip()
    # Tolerate a leading '+' (some models write "+200"); the bands are unsigned
    # options except for the minus sign.
    if got.startswith("+"):
        got = got[1:]
    if got == want:
        return ScoreResult("evalBand", True, f"band {got} matches expected {want}")
    return ScoreResult("evalBand", False, f"band {got!r} != expected {want!r}")


# The forbidden-phrase block, verbatim from the chess app's validators
# (MoveCoachResponseValidator + PositionChatValidator). A coach must not assert
# engine provenance it cannot have, or claim certainty it cannot justify.
_FORBIDDEN_PHRASES = [
    "i think stockfish",
    "probably depth",
    "stockfish thinks",
    "engine depth",
    "elo ",
    "rating of",
    "forced mate",
    "guaranteed win",
    "winning by force",
    "forces checkmate",
]


def score_forbidden_phrases(output: str, case: dict) -> ScoreResult:
    """
    Hard honesty gate: fail if the output asserts engine provenance or
    unsupported certainty. Ported from the chess app so both skills enforce the
    same honesty floor.
    """
    lowered = output.lower()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            return ScoreResult(
                "forbiddenPhrases",
                False,
                f"output contains forbidden phrase {phrase!r} "
                "(engine-provenance or unsupported-certainty claim)",
            )
    return ScoreResult("forbiddenPhrases", True, "no forbidden phrases detected")


# --- dispatch --------------------------------------------------------------


def score_for_case(output: str, case: dict) -> list[ScoreResult]:
    """
    Run the correct exact-match scorer for the case's answer format, plus the
    always-on forbidden-phrase gate. Mirrors rule_scorers.score_all's shape so
    run_scorecard can treat both skills uniformly.
    """
    results: list[ScoreResult] = [score_forbidden_phrases(output, case)]
    fmt = case.get("answerFormat")
    if fmt == "uci":
        results.append(score_exact_move(output, case))
    elif fmt == "centipawn-band":
        results.append(score_eval_band(output, case))
    else:
        # Unknown format — surface it rather than silently passing.
        results.append(
            ScoreResult("answerFormat", False, f"unknown answerFormat: {fmt!r}")
        )
    return results
