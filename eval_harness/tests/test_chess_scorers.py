"""
Hermetic tests for chess_scorers.py — the objective exact-match floor for the
chess-opening-coach skill. No network, no API keys. Mirrors test_rule_scorers.py
style: each scorer's pass/fail + reason is checked directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    # UCI cases run three scorers: forbidden-phrase gate, exact-move, and
    # reason-faithfulness. _TACTICS_CASE uses a placeholder FEN, so faithfulness
    # records a skip (not a pass) — the test asserts structure, not that every
    # scorer passes on a fake fixture.
    results = chess_scorers.score_for_case("FINAL ANSWER: e2e4\n", _TACTICS_CASE)
    keys = {r.key for r in results}
    assert keys == {"exactMove", "forbiddenPhrases", "reasonFaithfulness"}
    exact = next(r for r in results if r.key == "exactMove")
    forbidden = next(r for r in results if r.key == "forbiddenPhrases")
    assert exact.passed and forbidden.passed
    faithfulness = next(r for r in results if r.key == "reasonFaithfulness")
    # Placeholder FEN can't be parsed — faithfulness reports a fixture error
    # rather than passing silently.
    assert not faithfulness.passed
    assert "fixture error" in faithfulness.reason or "skip" in faithfulness.reason


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


# --- score_reason_faithfulness (Move Coach paraphrase contract) -------------
#
# These need a real FEN + correctAnswer so the tag set can be derived. Each
# case is a position with a known tag set; the test output either covers those
# tags faithfully, omits them, or invents a high-stakes concept the tags don't
# support.
#
# These tests require python-chess (the optional [chess] extra) to build real
# FEN cases. Skip the whole section gracefully when it's absent rather than
# failing collection — CI runs without the [chess] extra installed.
_chess = pytest.importorskip("chess")


def _real_tactics_case(fen: str, answer: str) -> dict:
    """A UCI tactics case with a parseable FEN, so faithfulness can derive tags."""
    return {
        "id": "t-real",
        "input": {"fen": fen, "question": "Find the best move for the side to move."},
        "correctAnswer": answer,
        "answerFormat": "uci",
        "taskCategory": "Short Tactics",
    }


# 1.e4 from the start: tags derive to {center-control, pawn-push, opening}.
_E4_CASE = _real_tactics_case(_chess.Board().fen(), "e2e4")
# Scholar's mate Qh5xf7#: tags derive to {capture, checkmate, material-swing, opening}.
_SCHOLAR_CASE = _real_tactics_case(
    "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
    "h5f7",
)
# Bf1-c4 (developing move): tags derive to {develops, opening}.
_DEVELOPS_CASE = _real_tactics_case(
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 4 3",
    "f1c4",
)


def test_faithfulness_passes_when_explanation_covers_supplied_tags():
    # e4: tags are {center-control, pawn-push, opening}. Coverage checks the
    # first two (opening is a phase marker, not coverage-gated). This output
    # mentions "center" and "advance" — covers both.
    r = chess_scorers.score_reason_faithfulness(
        "e4 advances the pawn to claim central space.\nFINAL ANSWER: e2e4",
        _E4_CASE,
    )
    assert r.passed and r.key == "reasonFaithfulness"


def test_faithfulness_fails_when_explanation_omits_supplied_tag():
    # e4: this output covers center but not the pawn-push concept
    # ("space"/"advance"/"push"), so coverage fails on pawn-push.
    r = chess_scorers.score_reason_faithfulness(
        "e4 stakes a claim in the center.\nFINAL ANSWER: e2e4",
        _E4_CASE,
    )
    assert not r.passed and "omits supplied tag concept" in r.reason
    assert "pawn-push" in r.reason


def test_faithfulness_catches_the_attacks_the_king_invention():
    # The roadmap's named failure mode: tags say {develops, opening} (a quiet
    # developing move), but the model writes "attacks the enemy king" — asserts
    # a high-stakes concept (a threat to the king) the tags don't support.
    r = chess_scorers.score_reason_faithfulness(
        "Bc4 develops the bishop and attacks the enemy king, forcing mate.\nFINAL ANSWER: f1c4",
        _DEVELOPS_CASE,
    )
    assert not r.passed
    # The invented concept surfaces in the reason — checkmate/forced-mate claim
    # is the catch.
    assert "unsupported concept" in r.reason


def test_faithfulness_catches_invented_check():
    # The tags derive to {develops, opening} — no check. Claiming "gives check"
    # is an unsupported invention.
    r = chess_scorers.score_reason_faithfulness(
        "Bc4 develops the bishop and gives check.\nFINAL ANSWER: f1c4",
        _DEVELOPS_CASE,
    )
    assert not r.passed and "check" in r.reason


def test_faithfulness_passes_on_scholar_mate_with_honest_explanation():
    # Qxf7# genuinely captures, mates, and wins material — an explanation that
    # says so is faithful to all four derived tags.
    r = chess_scorers.score_reason_faithfulness(
        "Qxf7 captures the pawn and delivers checkmate, winning material — "
        "the bishop on c4 supports the queen.\nFINAL ANSWER: h5f7",
        _SCHOLAR_CASE,
    )
    assert r.passed


def test_faithfulness_fails_when_mate_claimed_but_not_mate():
    # ChessQA case-001: Qf7-f8+ is a check, NOT checkmate (tags include `check`
    # but not `checkmate`). The output honestly covers the check, then falsely
    # claims checkmate — isolating the invention path (coverage passes, the
    # unsupported checkmate is what fails it).
    case = _real_tactics_case(
        "3r3k/p4Qpp/8/1P2p3/1B6/P2rb1P1/1q5P/5R1K w - - 1 33",
        "f7f8",
    )
    r = chess_scorers.score_reason_faithfulness(
        "Qf8 gives check and delivers checkmate. FINAL ANSWER: f7f8",
        case,
    )
    assert not r.passed
    assert "unsupported concept" in r.reason
    assert "checkmate" in r.reason


def test_faithfulness_skips_when_no_fen():
    # A case without a FEN can't derive tags — skip, never a silent pass.
    r = chess_scorers.score_reason_faithfulness(
        "FINAL ANSWER: e2e4", {"input": {}, "correctAnswer": "e2e4"}
    )
    assert not r.passed and "skip" in r.reason
