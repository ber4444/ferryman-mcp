"""
Derive the deterministic tags a Move Coach prompt would hand a model, from a
FEN + UCI move. Used by the chess reason-faithfulness scorer so the Move Coach
paraphrase contract (chess app's ``MoveCoachContextExtractor.deterministicTags``)
can be scored in the ferryman harness.

This is a **port** of the chess app's Kotlin tag derivation, not a
reimplementation: the same tag constants, the same detection rules, the same
precedence. Drift between the two would silently invalidate every faithfulness
score, so when the app's tag set changes this file must change with it (and the
tag-derivation tests will flag the drift).

Requires python-chess (pure Python, optional — install via the ``[chess]`` extra).
The scorer degrades gracefully if absent.
"""
from __future__ import annotations

# Tag constants — ported verbatim from MoveCoachFallback.kt. The Move Coach
# prompt's `describeTags` translates these to the phrases the model paraphrases;
# the faithfulness scorer checks a paraphrase against those same phrases.
TAG_CAPTURE = "capture"
TAG_CHECK = "check"
TAG_CHECKMATE = "checkmate"
TAG_CASTLE_KS = "castle-kingside"
TAG_CASTLE_QS = "castle-queenside"
TAG_PROMOTION = "promotion"
TAG_MATERIAL_SWING = "material-swing"
TAG_HANGS_PIECE = "no-hanging-piece"
TAG_DEFENDS = "defends"
TAG_THREATENS = "threatens"
TAG_DEVELOPS = "develops"
TAG_CENTER_CONTROL = "center-control"
TAG_KING_SAFETY = "king-safety"
TAG_PAWN_PUSH = "pawn-push"
TAG_RECAPTURE = "recapture"
TAG_OPENING = "opening"


class TagDerivationUnavailable(RuntimeError):
    """Raised when python-chess is not installed. Callers catch and record a skip."""


try:
    import chess  # type: ignore[import-untyped]

    _CHESS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via the skip path
    _CHESS_AVAILABLE = False


def derive_tags(fen: str, uci_move: str) -> list[str]:
    """
    Compute the deterministic tags for ``uci_move`` played in position ``fen``.

    Ported from ``MoveCoachContextExtractor.deterministicTags``. Mirrors its
    detection rules: capture (incl. en passant), castling, promotion, check /
    checkmate, material swing, development (minor piece off the back rank),
    center control (move lands on d4/d5/e4/e5), pawn push, king safety (king
    step off the back rank, non-castling), opening phase (≤10 fullmoves).

    ``TAG_DEFENDS`` / ``TAG_THREATENS`` / ``TAG_RECAPTURE`` are intentionally
    not derived here: the app's logic for them is heuristic and depends on move
    history it reconstructs from ``GameUiState`` (not available from a single
    FEN). Deriving them incorrectly would inflate faithfulness scores, so they
    are omitted — the scorer only checks the tags it can derive faithfully.

    Raises :class:`TagDerivationUnavailable` if python-chess is not installed,
    so the scorer can record a skip rather than fail the case.
    """
    if not _CHESS_AVAILABLE:
        raise TagDerivationUnavailable(
            "python-chess not installed — install the [chess] extra to score "
            "reason faithfulness"
        )

    board = chess.Board(fen)
    move = chess.Move.from_uci(uci_move)
    if move not in board.legal_moves:
        # A golden case with an illegal "correct" move is a fixture bug, not a
        # scoring question. Surface it loudly.
        raise ValueError(f"illegal move {uci_move} for FEN {fen!r}")

    moving_piece = board.piece_at(move.from_square)
    mover_side = board.turn  # side to move = the side making the coached move

    # Capture: an enemy piece on the destination before the move, or en passant.
    # python-chess's is_capture covers both (and is exactly the Kotlin logic's
    # `toSquare in enemyBeforePos || enPassantTarget`).
    was_capture = board.is_capture(move)

    tags: list[str] = []

    if was_capture:
        tags.append(TAG_CAPTURE)

    # Castling: king moves two files. python-chess classifies this directly.
    if board.is_castling(move):
        # King toward the h-file (king-side); toward the a-file (queen-side).
        from_file = chess.square_file(move.from_square)
        to_file = chess.square_file(move.to_square)
        if to_file > from_file:
            tags.append(TAG_CASTLE_KS)
        else:
            tags.append(TAG_CASTLE_QS)

    # Promotion
    if move.promotion is not None:
        tags.append(TAG_PROMOTION)

    # Check / checkmate from the post-move state.
    board.push(move)
    if board.is_check():
        tags.append(TAG_CHECKMATE if board.is_checkmate() else TAG_CHECK)

    # Material swing: did the moving side's material balance improve? Computed
    # exactly as in the Kotlin original — piece-value sums per side, before and
    # after. We need the pre-move board for the before snapshot.
    board.pop()
    material_before = _material_balance(board, mover_side)
    board.push(move)
    material_after = _material_balance(board, mover_side)
    if material_after - material_before > 0:
        tags.append(TAG_MATERIAL_SWING)

    # Development: minor piece (N/B) moving off its own back rank.
    if moving_piece is not None and moving_piece.piece_type in (chess.KNIGHT, chess.BISHOP):
        back_rank = 0 if mover_side == chess.WHITE else 7
        if chess.square_rank(move.from_square) == back_rank and chess.square_rank(move.to_square) != back_rank:
            tags.append(TAG_DEVELOPS)

    # Center control: the move lands on a central square.
    center_squares = {chess.D4, chess.D5, chess.E4, chess.E5}
    if move.to_square in center_squares:
        tags.append(TAG_CENTER_CONTROL)

    # Pawn push: any pawn advancing along its file.
    if moving_piece is not None and moving_piece.piece_type == chess.PAWN:
        if chess.square_file(move.from_square) == chess.square_file(move.to_square):
            tags.append(TAG_PAWN_PUSH)

    # King safety: king step (non-castling) off its own back rank.
    if moving_piece is not None and moving_piece.piece_type == chess.KING:
        back_rank = 0 if mover_side == chess.WHITE else 7
        if (
            chess.square_rank(move.from_square) == back_rank
            and chess.square_rank(move.to_square) != back_rank
            and not board.is_castling(move)  # castling already tagged above
        ):
            tags.append(TAG_KING_SAFETY)

    # Opening phase: first 10 fullmoves. The FEN's fullmove number is the
    # pre-move count; board.fullmove_number after push reflects the position
    # after the coached move, matching the app's stateAfter.fullmoveNumber.
    if board.fullmove_number <= 10:
        tags.append(TAG_OPENING)

    board.pop()  # restore — leave the board as we found it

    # Distinct, preserving discovery order — matches the Kotlin original's
    # `.distinct()`.
    seen: set[str] = set()
    distinct: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            distinct.append(t)
    return distinct


def _material_balance(board: "chess.Board", side: "chess.Color") -> int:
    """
    Material for ``side`` minus material for the opponent, matching the app's
    piece values (P=100, N=320, B=330, R=500, Q=900, K=0).
    """
    values = {
        chess.PAWN: 100,
        chess.KNIGHT: 320,
        chess.BISHOP: 330,
        chess.ROOK: 500,
        chess.QUEEN: 900,
        chess.KING: 0,
    }
    own = sum(
        len(board.pieces(pt, side)) * values[pt] for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING)
    )
    opp = sum(
        len(board.pieces(pt, not side)) * values[pt]
        for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING)
    )
    return own - opp
