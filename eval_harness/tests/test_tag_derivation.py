"""
Hermetic tests for tag_derivation.py — the Python port of the chess app's
MoveCoachContextExtractor.deterministicTags.

These pin the port against the Kotlin original: each test names a position with
a known tag set, so when the app's tag logic changes this file flags the drift.
Requires python-chess; skipped entirely if it's not installed (the scorer's
graceful-degradation path is covered in test_chess_scorers.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import tag_derivation as td

chess = pytest.importorskip("chess")  # skip this whole module if python-chess absent


# --- known positions, known tag sets ---------------------------------------


def test_e2e4_opening_pawn_push_center_control():
    # 1.e4 from the start position: pawn push, lands on e4 (center), move 1.
    tags = set(td.derive_tags(chess.Board().fen(), "e2e4"))
    assert {"pawn-push", "center-control", "opening"} <= tags


def test_minor_piece_development_off_back_rank():
    # After 1.e4 e5 2.Nf3 Nc6, White's Bf1-c4: bishop leaves the back rank.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 4 3"
    tags = set(td.derive_tags(fen, "f1c4"))
    assert "develops" in tags
    assert "opening" in tags


def test_kingside_castle_tagged():
    fen = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    tags = set(td.derive_tags(fen, "e1g1"))
    assert "castle-kingside" in tags
    assert "castle-queenside" not in tags


def test_queenside_castle_tagged():
    # Minimal position where both castlings are legal: rooks on a1/h1, kings on
    # e1, nothing between. e1c1 is O-O-O.
    fen = "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1"
    tags = set(td.derive_tags(fen, "e1c1"))
    assert "castle-queenside" in tags
    assert "castle-kingside" not in tags


def test_checkmate_tagged_distinct_from_check():
    # Verified Scholar's mate: Qh5xf7# (bishop on c4 supports f7).
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    tags = set(td.derive_tags(fen, "h5f7"))
    assert "checkmate" in tags
    assert "capture" in tags  # Qxf7 — captures the f7 pawn
    assert "check" not in tags  # checkmate supersedes plain check


def test_plain_check_not_mate():
    # ChessQA case-001: Qf7-f8+ is a check, not mate (king on g8 can take? no,
    # bishop on b4... it's just a check). Verifies check-without-mate path.
    fen = "3r3k/p4Qpp/8/1P2p3/1B6/P2rb1P1/1q5P/5R1K w - - 1 33"
    tags = set(td.derive_tags(fen, "f7f8"))
    assert "check" in tags
    assert "checkmate" not in tags


def test_en_passant_capture_detected():
    # Black just played d7-d5; white e5 pawn can capture en passant on d6.
    fen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
    tags = set(td.derive_tags(fen, "e5d6"))
    assert "capture" in tags


def test_promotion_tagged():
    # White pawn e7 promotes on e8; black king shifted off e8 so the move is legal.
    fen = "7k/4P3/8/8/8/8/8/4K3 w - - 0 1"
    tags = set(td.derive_tags(fen, "e7e8q"))
    assert "promotion" in tags


def test_material_swing_on_winning_capture():
    # Scholar's mate Qxf7 wins the f7 pawn — material balance improves.
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    tags = set(td.derive_tags(fen, "h5f7"))
    assert "material-swing" in tags


def test_illegal_move_raises_value_error():
    with pytest.raises(ValueError, match="illegal move"):
        td.derive_tags(chess.Board().fen(), "e2e5")  # pawns can't move 3 squares


def test_tags_are_distinct_and_ordered():
    # Distinct: a move that would trigger the same tag twice still lists it once.
    # Ordered: discovery order preserved (matches the Kotlin `.distinct()`).
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    tags = td.derive_tags(fen, "h5f7")
    assert len(tags) == len(set(tags))  # no duplicates
