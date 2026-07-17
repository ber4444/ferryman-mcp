"""
Hermetic tests for chess_scorers.py — the objective exact-match floor for the
chess-opening-coach skill. No network, no API keys. Mirrors test_rule_scorers.py
style: each scorer's pass/fail + reason is checked directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import chess_scorers


# A tactics (UCI best-move) case and a position-judgment (centipawn-band) case,
# in the normalized golden shape (see eval_harness/golden/chess_golden.json).
_TACTICS_CASE = {
    "id": "t1",
    "input": {"fen": "...", "question": "Find the best move for the side to move."},
    "correctAnswer": "e2e4",
    "answerFormat": "uci",
    "taskCategory": "Short Tactics",
}

_EVAL_CASE = {
    "id": "p1",
    "input": {"fen": "...", "question": "Estimate the Stockfish evaluation."},
    "correctAnswer": "200",
    "answerFormat": "centipawn-band",
    "taskCategory": "Position Judgment",
    "options": ["-400", "-200", "0", "200", "400"],
}


# --- extract_final_answer (the ChessQA contract) ---------------------------


def test_extract_final_answer_basic():
    text = "Some reasoning.\nFINAL ANSWER: e2e4\n"
    assert chess_scorers.extract_final_answer(text) == ("e2e4", True)


def test_extract_final_answer_takes_last_match():
    # A model that corrects itself: the LAST FINAL ANSWER wins.
    text = "FINAL ANSWER: d2d4\n...actually\nFINAL ANSWER: e2e4\n"
    assert chess_scorers.extract_final_answer(text)[0] == "e2e4"


def test_extract_final_answer_case_insensitive_and_bold():
    text = "reasoning\n**final answer: g1f3**"
    got, found = chess_scorers.extract_final_answer(text)
    assert found and got.replace("*", "") == "g1f3"


def test_extract_final_answer_boxed_fallback():
    text = "The final answer is $\\boxed{e2e4}$"
    assert chess_scorers.extract_final_answer(text) == ("e2e4", True)


def test_extract_final_answer_missing_returns_not_found():
    got, found = chess_scorers.extract_final_answer("no answer here at all")
    assert found is False and got == ""


# --- score_exact_move (UCI best-move) --------------------------------------


def test_exact_move_passes_on_match():
    r = chess_scorers.score_exact_move("reasoning\nFINAL ANSWER: e2e4", _TACTICS_CASE)
    assert r.passed and r.key == "exactMove"


def test_exact_move_normalizes_case_and_whitespace():
    # The skill asks for lowercase UCI; tolerate uppercase/space so a model's
    # minor formatting doesn't cause a false miss.
    r = chess_scorers.score_exact_move("FINAL ANSWER:  E2E4 ", _TACTICS_CASE)
    assert r.passed


def test_exact_move_fails_on_wrong_move():
    r = chess_scorers.score_exact_move("FINAL ANSWER: d2d4", _TACTICS_CASE)
    assert not r.passed and "e2e4" in r.reason


def test_exact_move_fails_without_final_answer_line():
    r = chess_scorers.score_exact_move("the best move is e2e4 obviously", _TACTICS_CASE)
    assert not r.passed and "no FINAL ANSWER" in r.reason


def test_exact_move_handles_promotion_suffix():
    case = {**_TACTICS_CASE, "correctAnswer": "c7b8q"}
    r = chess_scorers.score_exact_move("FINAL ANSWER: c7b8q", case)
    assert r.passed


# --- score_eval_band (centipawn band) --------------------------------------


def test_eval_band_passes_on_match():
    r = chess_scorers.score_eval_band("FINAL ANSWER: 200", _EVAL_CASE)
    assert r.passed and r.key == "evalBand"


def test_eval_band_tolerates_leading_plus():
    # Some models write "+200"; the bands are unsigned except for the minus.
    r = chess_scorers.score_eval_band("FINAL ANSWER: +200", _EVAL_CASE)
    assert r.passed


def test_eval_band_negative_matches():
    case = {**_EVAL_CASE, "correctAnswer": "-400"}
    r = chess_scorers.score_eval_band("FINAL ANSWER: -400", case)
    assert r.passed


def test_eval_band_fails_on_wrong_band():
    r = chess_scorers.score_eval_band("FINAL ANSWER: 0", _EVAL_CASE)
    assert not r.passed and "200" in r.reason


# --- score_forbidden_phrases (honesty gate) --------------------------------


def test_forbidden_phrases_clean_output_passes():
    r = chess_scorers.score_forbidden_phrases("Nf3 develops the knight toward the center.", {})
    assert r.passed


def test_forbidden_phrases_engine_provenance_fails():
    r = chess_scorers.score_forbidden_phrases("FINAL ANSWER: e2e4\nStockfish thinks this is best.", {})
    assert not r.passed and "stockfish thinks" in r.reason.lower()


def test_forbidden_phrases_unsupported_certainty_fails():
    r = chess_scorers.score_forbidden_phrases("This leads to a forced mate.", {})
    assert not r.passed and "forced mate" in r.reason.lower()


def test_forbidden_phrases_elo_claim_fails():
    r = chess_scorers.score_forbidden_phrases("A 2500 elo move.", {})
    assert not r.passed and "elo " in r.reason.lower()


# --- score_for_case (dispatch) ---------------------------------------------


def test_score_for_case_dispatches_uci():
    results = chess_scorers.score_for_case("FINAL ANSWER: e2e4\n", _TACTICS_CASE)
    keys = {r.key for r in results}
    assert "exactMove" in keys and "forbiddenPhrases" in keys
    assert all(r.passed for r in results)


def test_score_for_case_dispatches_centipawn_band():
    results = chess_scorers.score_for_case("FINAL ANSWER: 200\n", _EVAL_CASE)
    keys = {r.key for r in results}
    assert "evalBand" in keys and "forbiddenPhrases" in keys
    assert all(r.passed for r in results)


def test_score_for_case_unknown_format_surfaces():
    case = {**_TACTICS_CASE, "answerFormat": "san"}
    results = chess_scorers.score_for_case("FINAL ANSWER: Nf3\n", case)
    unknown = [r for r in results if r.key == "answerFormat"]
    assert len(unknown) == 1 and not unknown[0].passed
    assert "san" in unknown[0].reason
