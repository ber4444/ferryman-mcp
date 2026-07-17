#!/usr/bin/env python3
"""
Score LiteRT-LM on-device outputs against the Move Coach reason-faithfulness
contract. This is the scoring half of the bypass:

  chess app (EvalLiteRtDriver.kt)  →  litert-outputs.json  →  this script  →  scorecard

Generation and scoring are deliberately decoupled. The chess app drives the
real LiteRT-LM generator under the Move Coach prompt contract and captures raw
prose + latency; this script imports ferryman's ``_check_faithfulness`` (the pure
core of ``score_reason_faithfulness``) and scores whether each paraphrase stays
faithful to the deterministic tags the prompt supplied.

Why not run ferryman end-to-end here: ferryman has no LiteRT-LM provider (it's
an HTTP-only MCP host), and the Move Coach generator lives in the chess app.
The scorer is provider-agnostic — it checks a string of prose against a tag set
— so it scores LiteRT-LM output exactly as it scores any cloud provider's.

Usage:
    python3 eval_harness/score_litert_outputs.py <litert-outputs.json> [--write scorecard-litert.md]

The driver's JSON shape (one object per case, produced by EvalLiteRtDriver.kt):
    { "id", "fen", "bestMoveUci", "tags", "output", "route",
      "firstTokenMs", "completeMs" }

Requires the ferryman harness on the Python path (runs from the repo root).
No API keys, no network — pure local scoring.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Make the harness importable whether run as a script or a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval_harness.chess_scorers import _check_faithfulness, score_forbidden_phrases
from eval_harness.rule_scorers import ScoreResult


# Matches <think>...</think> blocks (case-insensitive, DOTALL so newlines match).
# LiteRT-LM's Qwen3 model emits chain-of-thought before its answer; the faithfulness
# scorer must judge only the delivered answer (what the user sees), not the model's
# internal deliberation. Stripping here makes the scorer robust even if the generator
# itself doesn't strip (defense in depth — the chess-app generator strips too, but a
# model swap or a generation hiccup could leak CoT back).
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_UNTERMINATED = re.compile(r"<think>.*", re.IGNORECASE | re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> chain-of-thought blocks from a model output.

    Handles both terminated blocks and an unterminated trailing <think> (if the
    model started reasoning and never closed it). Returns the cleaned text with
    collapsed whitespace. If no <think> is present, returns the text unchanged.
    """
    cleaned = _THINK_BLOCK.sub("", text or "")
    # Catch an unterminated <think> that runs to end-of-string (no closing tag).
    cleaned = _THINK_UNTERMINATED.sub("", cleaned)
    return cleaned.strip()


def _expected_piece_name(fen: str, uci_move: str) -> str | None:
    """Derive the piece name the prompt hands the model, for piece-type faithfulness.

    Mirrors MoveCoachPromptBuilder.describeMove: look up the piece at the UCI
    from-square in the FEN, return its English name (Knight/Bishop/Rook/Queen/
    King/Pawn). Returns None if the FEN can't be parsed or the move is illegal
    (the caller treats None as 'cannot check — skip piece-type').
    """
    try:
        import chess
    except ImportError:
        return None
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci_move)
        piece = board.piece_at(move.from_square)
        if piece is None:
            return None
        return {
            chess.PAWN: "pawn",
            chess.KNIGHT: "knight",
            chess.BISHOP: "bishop",
            chess.ROOK: "rook",
            chess.QUEEN: "queen",
            chess.KING: "king",
        }[piece.piece_type]
    except (ValueError, Exception):
        return None


def _check_piece_type(output: str, expected_piece: str) -> ScoreResult:
    """Check the output names the right piece type.

    The Move Coach prompt hands the model 'Knight g1→f3' (describeMove's output);
    if the model writes 'this pawn advances' the piece type is wrong — a factual
    error no tag-faithfulness check catches. This is a hard gate: a wrong piece
    type is unambiguously false regardless of tag coverage.
    """
    lowered = (output or "").lower()
    # Castling is described as 'Castles …' by describeMove, not a piece name —
    # skip the piece-type check when the expected piece is a king and the output
    # mentions castling (the app's convention).
    if expected_piece == "king" and re.search(r"\bcastl", lowered):
        return ScoreResult("pieceType", True, "output describes castling (king move)")
    # The model may pluralize or capitalize; check the bare word as a substring.
    # A pawn is also just a square reference (e.g. 'e4'), so accept 'pawn' OR
    # no explicit piece claim for pawn moves (many correct explanations say
    # 'advances to e4' without naming the pawn).
    if expected_piece == "pawn" and expected_piece not in lowered:
        return ScoreResult("pieceType", True, "pawn move — piece name optional")
    if expected_piece in lowered:
        return ScoreResult("pieceType", True, f"output names the correct piece ({expected_piece})")
    return ScoreResult(
        "pieceType",
        False,
        f"output does not name the expected piece ({expected_piece}) — possible misidentification",
    )


def score_file(outputs_path: Path) -> list[dict]:
    """Score every record in the driver's JSON output. Returns one row per case."""
    records = json.loads(outputs_path.read_text())
    if not isinstance(records, list):
        raise SystemExit(f"expected a JSON array in {outputs_path}, got {type(records).__name__}")

    rows: list[dict] = []
    for rec in records:
        raw_output = rec.get("output", "")
        # Strip <think> blocks so faithfulness judges only the delivered answer,
        # not the model's chain-of-thought deliberation. The honesty gate runs on
        # the stripped text too — engine-provenance claims in CoT are still real
        # claims, but LiteRT-LM's CoT is deliberative and we don't want a phrase
        # like "I shouldn't say stockfish thinks" to fail the gate.
        output = strip_think_blocks(raw_output)
        tags = set(rec.get("tags", []))

        # The faithfulness core, run against the pre-supplied tags (the chess
        # app's candidates.json carries tags per case — no FEN derivation needed).
        faithfulness = _check_faithfulness(output, tags)

        # Piece-type faithfulness: does the output name the right piece? The
        # prompt hands the model the piece name; misidentifying it (calling a
        # knight a pawn) is a factual error no tag check catches. Skipped
        # gracefully when python-chess is absent or the FEN/move can't resolve.
        expected_piece = _expected_piece_name(rec.get("fen", ""), rec.get("bestMoveUci", ""))
        piece_type = (
            _check_piece_type(output, expected_piece)
            if expected_piece
            else ScoreResult("pieceType", False, "skip: could not derive piece type")
        )

        # Also run the forbidden-phrase honesty gate, the same one the chess
        # app's MoveCoachResponseValidator enforces client-side. This lets the
        # scorecard show both axes: honest (no engine-provenance lies) AND
        # faithful (no invented reasons).
        forbidden = score_forbidden_phrases(output, {})

        rows.append({
            "id": rec.get("id", "?"),
            "route": rec.get("route", "?"),
            "tags": sorted(tags),
            "faithfulness_passed": faithfulness.passed,
            "faithfulness_reason": faithfulness.reason,
            "piece_type_passed": piece_type.passed,
            "piece_type_reason": piece_type.reason,
            "honesty_passed": forbidden.passed,
            "honesty_reason": forbidden.reason,
            "firstTokenMs": rec.get("firstTokenMs"),
            "completeMs": rec.get("completeMs"),
            "output_preview": (output[:120] + "…") if len(output) > 120 else output,
        })
    return rows


def render_markdown(rows: list[dict], source: str) -> str:
    """Render a scorecard from the scored rows."""
    total = len(rows)
    litert_rows = [r for r in rows if r["route"] == "litert"]
    fallback_rows = [r for r in rows if r["route"].startswith("fallback")]
    faithful = sum(1 for r in rows if r["faithfulness_passed"])
    piece_ok = sum(1 for r in rows if r["piece_type_passed"])
    honest = sum(1 for r in rows if r["honesty_passed"])
    # All-three-pass is the honest "fully correct" count: a case that's honest
    # but misidentifies the piece or invents a reason is not fully correct.
    all_pass = sum(
        1 for r in rows if r["faithfulness_passed"] and r["piece_type_passed"] and r["honesty_passed"]
    )
    ft_latencies = [r["firstTokenMs"] for r in litert_rows if r["firstTokenMs"] is not None]
    cmpl_latencies = [r["completeMs"] for r in litert_rows if r["completeMs"] is not None]

    lines = [
        "# LiteRT-LM reason-faithfulness scorecard",
        "",
        f"Source: `{source}`",
        f"Generated: scored by ferryman `score_litert_outputs.py`",
        f"Cases: {total} ({len(litert_rows)} litert, {len(fallback_rows)} fallback)",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| All-three pass (faithful + piece + honest) | {all_pass}/{total} ({all_pass/total:.0%}) |",
        f"| Reason-faithfulness pass | {faithful}/{total} ({faithful/total:.0%}) |",
        f"| Piece-type pass (names the right piece) | {piece_ok}/{total} ({piece_ok/total:.0%}) |",
        f"| Honesty-gate pass (no forbidden phrases) | {honest}/{total} ({honest/total:.0%}) |",
        f"| First-token latency (litert only) | "
        + (f"{sum(ft_latencies)//len(ft_latencies)} ms mean ({len(ft_latencies)} samples)" if ft_latencies else "—"),
        f"| Complete latency (litert only) | "
        + (f"{sum(cmpl_latencies)//len(cmpl_latencies)} ms mean ({len(cmpl_latencies)} samples)" if cmpl_latencies else "—"),
        "",
        "## Per-case results",
        "",
        "| Case | Route | Tags | Faithful | Piece | Honest | Failed check |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        tags_str = ", ".join(r["tags"]) or "—"
        faithful_str = "✓" if r["faithfulness_passed"] else "✗"
        piece_str = "✓" if r["piece_type_passed"] else "✗"
        honest_str = "✓" if r["honesty_passed"] else "✗"
        failed = []
        if not r["faithfulness_passed"]:
            failed.append(f"faithfulness: {r['faithfulness_reason']}")
        if not r["piece_type_passed"]:
            failed.append(f"piece: {r['piece_type_reason']}")
        if not r["honesty_passed"]:
            failed.append(f"honesty: {r['honesty_reason']}")
        failed_str = "; ".join(failed) or "—"
        lines.append(
            f"| {r['id']} | {r['route']} | {tags_str} | {faithful_str} | {piece_str} | {honest_str} | {failed_str} |"
        )

    lines += [
        "",
        "## Outputs (preview)",
        "",
    ]
    for r in rows:
        lines.append(f"**{r['id']}** ({r['route']}): {r['output_preview']}")
        lines.append("")

    lines.append(
        "---\n"
        "Scored by ferryman's `_check_faithfulness` against the pre-supplied tags "
        "(the chess app's candidates.json carries hand-authored tags per case). "
        "Faithfulness = paraphrase covers supplied tag concepts and asserts no "
        "unsupported high-stakes concept (check/mate/material/capture/castle/promo). "
        "Piece-type = output names the piece the prompt supplied (Knight/Bishop/etc.); "
        "a wrong piece type (e.g. calling a knight a pawn) is a factual error. "
        "Honesty = the forbidden-phrase gate (no 'Stockfish thinks' / 'engine depth' / etc.). "
        "Outputs are stripped of `<think>` chain-of-thought blocks before scoring, "
        "so the verdicts reflect the delivered answer the user sees.",
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score LiteRT-LM outputs against the Move Coach faithfulness contract.",
    )
    parser.add_argument(
        "outputs",
        type=Path,
        help="Path to litert-outputs.json from EvalLiteRtDriver.kt",
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Write the scorecard to this path (default: print to stdout)",
    )
    args = parser.parse_args(argv)

    if not args.outputs.exists():
        print(f"error: {args.outputs} not found.", file=sys.stderr)
        print(
            "Run the chess app's driver first:\n"
            "  ./gradlew :evals:runLiteRtDriver --args='10 litert-outputs.json'",
            file=sys.stderr,
        )
        return 1

    rows = score_file(args.outputs)
    md = render_markdown(rows, source=str(args.outputs))

    if args.write:
        args.write.write_text(md)
        print(f"Wrote scorecard for {len(rows)} cases to {args.write}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
