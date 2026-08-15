"""
Hermetic tests for the Move Coach device-run scripts. No network, no API keys —
the judge call is the only networked part and is not exercised here; everything
tested is a pure function over a row.

The row shapes are verbatim from the 2026-08-15 Pixel 10 Pro XL run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import judge_move_coach_run as judge
from eval_harness import score_move_coach_run as score


_CASES = {
    "opening-001": {"id": "opening-001", "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "bestMoveUci": "g1h3"},
}


def _row(**over):
    row = {
        "caseId": "opening-001",
        "isFallbackGolden": False,
        "factsPopulated": True,
        "tags": ["opening", "develops"],
        "moveDisplay": "Nh3",
        "deterministicExplanation": "It drops your winning chances by 7%. e4 was stronger.",
        "moveClassName": "INACCURACY",
        "motifs": ["develops"],
        "winPercentLost": 7.5,
        "betterMoveDisplay": "e4",
        "rawOutput": "Nh3 develops the knight but drops your winning chances by 7%.",
    }
    row.update(over)
    return row


def test_facts_block_states_a_loss_as_a_loss():
    """`winPercentLost` is positive-means-lost. Phrasing it as "changed by X%" had the judge read
    a loss as a gain, and call a correct "drops your winning chances by 3%" a contradiction of the
    facts it was given."""
    assert "cost the player 7.5%" in judge._facts_block(_row())
    assert "gained the player 2.0%" in judge._facts_block(_row(winPercentLost=-2.0))


def test_facts_block_says_no_reason_is_given_for_the_engines_move():
    """The assessment knows the engine chose a move, never why. The judge has to be told that, or
    it cannot tell an invented rationale from a withheld one."""
    assert "no reason for this preference is given" in judge._facts_block(_row())


def test_placeholder_rows_are_never_scored():
    """A placeholder run's rows read exactly like real ones — fluent text about a position the model
    was told nothing about. The chess app marks them so this cannot happen by accident."""
    report = score.score_run([_row(factsPopulated=False), _row(isFallbackGolden=True)], _CASES)
    assert report["compared"] == 0
    assert report["skipped"]["placeholder"] == 2


def test_a_row_with_no_answer_is_skipped_not_counted_as_a_failure():
    report = score.score_run([_row(rawOutput=None)], _CASES)
    assert report["compared"] == 0
    assert report["skipped"]["no answer"] == 1


def test_coverage_is_reported_but_does_not_fail_a_candidate():
    """Coverage — every supplied tag's concept mentioned — is a recall rule written for a
    reasoning-length answer. The Move Coach panel is two sentences under a 300-char cap and is
    handed up to five tags, so it fails coverage by construction: measured 81/100 for the model and
    99/100 for the deterministic line, which is the shipping product."""
    row = _row(motifs=["develops", "center-control", "threatens"],
               rawOutput="Nh3 develops the knight.")
    report = score.score_run([row], _CASES)
    assert report["compared"] == 1
    assert report["coverage"]["model"] == 0  # does not mention centre or threats
    assert report["model_clear"] == 1  # …and is not failed for it
