"""
Hermetic tests for score_litert_outputs.py — the LiteRT-LM bypass scorer.

Covers the three fixes layered on the foundation scorer:
  - <think> block stripping (CoT must not contaminate the verdict)
  - piece-type faithfulness (a knight called a pawn is a factual error)
  - the bypass script's integration of both, plus its scorecard rendering
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import chess_scorers
from eval_harness import score_litert_outputs as slo

chess = pytest.importorskip("chess", reason="python-chess not installed — piece-type tests need it")


# --- strip_think_blocks ----------------------------------------------------


def test_strip_think_removes_closed_block():
    raw = "<think>Let me reason. Does it check? No.</think>\nNf3 develops the knight."
    assert slo.strip_think_blocks(raw) == "Nf3 develops the knight."


def test_strip_think_removes_block_with_newlines():
    raw = "<think>\nline one\nline two\n</think>\nAnswer."
    assert slo.strip_think_blocks(raw) == "Answer."


def test_strip_think_removes_unterminated_block():
    raw = "Answer. <think>Wait, reconsidering"
    assert slo.strip_think_blocks(raw) == "Answer."


def test_strip_think_no_block_returns_unchanged():
    assert slo.strip_think_blocks("plain answer") == "plain answer"


def test_strip_think_case_insensitive():
    assert slo.strip_think_blocks("<THINK>x</THINK>Answer.") == "Answer."


def test_strip_think_only_think_returns_empty():
    assert slo.strip_think_blocks("<think>all reasoning</think>") == ""


# --- <think> stripping defangs the check-verb false positive ---------------


def test_faithfulness_passes_when_check_appears_only_in_think_block():
    # The roadmap's failure case: tags say {develops, opening}, but the CoT
    # deliberates "does this give check?" — which the old scorer flagged as
    # inventing check. After stripping <think>, the clean answer is faithful.
    raw = (
        "<think>Does this move give check? No. Does it win material? No.</think>\n"
        "Nh3 develops the knight toward the edge of the board."
    )
    cleaned = slo.strip_think_blocks(raw)
    result = chess_scorers._check_faithfulness(cleaned, {"develops", "opening"})
    assert result.passed, f"should pass after CoT strip: {result.reason}"


# --- check-verb guard (fix 2) ----------------------------------------------


def test_check_as_verb_not_flagged_as_invented():
    # "check if the king is safe" is an English verb, not the chess concept.
    # The tag set has no `check`, so the old substring test invented one.
    result = chess_scorers._check_faithfulness(
        "Nf3 develops the knight. We can check whether the king is exposed later.",
        {"develops"},
    )
    assert result.passed, f"verb 'check whether' must not count as inventing check: {result.reason}"


def test_check_as_chess_concept_still_flagged_when_unsupplied():
    # "gives check" IS the chess concept — must still be caught as invented.
    result = chess_scorers._check_faithfulness(
        "Nf3 develops the knight and gives check to the king.",
        {"develops"},
    )
    assert not result.passed
    assert "check" in result.reason


def test_check_as_chess_concept_passes_when_supplied():
    # When `check` IS a supplied tag, "gives check" satisfies coverage.
    result = chess_scorers._check_faithfulness(
        "Qb5 gives check and develops toward the queenside.",
        {"check", "develops"},
    )
    assert result.passed, result.reason


# --- piece-type faithfulness (fix 3) ---------------------------------------


def test_expected_piece_name_knight():
    # g1h3 from the start position: knight on g1.
    assert slo._expected_piece_name(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "g1h3"
    ) == "knight"


def test_expected_piece_name_pawn():
    assert slo._expected_piece_name(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e2e4"
    ) == "pawn"


def test_expected_piece_name_reports_piece_even_for_illegal_move():
    # An illegal move still has a piece at the from-square; the scorer's job is
    # to identify that piece (the prompt would still hand the model the move),
    # not to validate legality. e2e5 isn't legal, but e2 has a pawn.
    assert slo._expected_piece_name(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e2e5"
    ) == "pawn"


def test_piece_type_passes_when_output_names_correct_piece():
    # Output names "knight" — matches the g1h3 knight move.
    result = slo._check_piece_type("Nh3 develops the knight toward the edge.", "knight")
    assert result.passed


def test_piece_type_fails_when_output_names_wrong_piece():
    # The real failure observed: g1h3 is a knight move, but the model called it
    # a pawn. This must fail — it's a factual error no tag check catches.
    result = slo._check_piece_type("This pawn advances to h3.", "knight")
    assert not result.passed
    assert "knight" in result.reason


def test_piece_type_pawn_optional():
    # A pawn move: correct explanations often say "advances to e4" without
    # naming the pawn. Don't fail just because "pawn" isn't mentioned.
    result = slo._check_piece_type("Advances to e4, staking a claim in the center.", "pawn")
    assert result.passed


def test_piece_type_castling_skipped_for_king():
    # Castling is described as "Castles kingside" by the app, not "king" —
    # so a king move that's castling shouldn't fail the piece-type check.
    result = slo._check_piece_type("Castles kingside, tucking the king to safety.", "king")
    assert result.passed


# --- integration: score_file ties it together ------------------------------


def _synthetic_record(case_id: str, *, output: str, fen: str, uci: str, tags: list[str]) -> dict:
    return {
        "id": case_id,
        "fen": fen,
        "bestMoveUci": uci,
        "tags": tags,
        "output": output,
        "route": "litert",
        "firstTokenMs": 300,
        "completeMs": 5000,
    }


def test_score_file_strips_think_and_checks_piece_type(tmp_path):
    # opening-001 from the real run: g1h3 is a KNIGHT move, tags {develops, opening},
    # but the model emitted <think>…</think> then "this pawn advance to h3".
    # After fixes: think stripped, faithfulness should fail on "material/attack"
    # invention in the answer, AND piece-type should fail (knight ≠ pawn).
    record = _synthetic_record(
        "opening-001",
        output=(
            "<think>Let me think. Is this a pawn move? The user said Knight g1→h3 but "
            "I think it's a pawn. Check if it gives check. No.</think>\n"
            "This pawn advance to h3 provides material and sets up a potential attack."
        ),
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        uci="g1h3",
        tags=["opening", "develops"],
    )
    path = tmp_path / "litert-outputs.json"
    path.write_text(json.dumps([record]))

    rows = slo.score_file(path)
    assert len(rows) == 1
    r = rows[0]
    # Piece-type caught the misidentification.
    assert not r["piece_type_passed"], r["piece_type_reason"]
    # The CoT "check if" must NOT have caused a false check-invention — only the
    # answer's "material/attack" should drive the faithfulness failure.
    assert not r["faithfulness_passed"]
    # The faithfulness reason should name material/attack from the ANSWER, not
    # "check" from the stripped CoT.
    assert "check" not in r["faithfulness_reason"].lower(), r["faithfulness_reason"]


def test_score_file_clean_output_passes_all_three(tmp_path):
    record = _synthetic_record(
        "opening-clean",
        output="Nh3 develops the knight toward the edge of the board.",
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        uci="g1h3",
        tags=["opening", "develops"],
    )
    path = tmp_path / "litert-outputs.json"
    path.write_text(json.dumps([record]))

    rows = slo.score_file(path)
    r = rows[0]
    assert r["faithfulness_passed"], r["faithfulness_reason"]
    assert r["piece_type_passed"], r["piece_type_reason"]
    assert r["honesty_passed"], r["honesty_reason"]
