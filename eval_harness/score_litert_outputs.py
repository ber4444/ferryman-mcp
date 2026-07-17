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
import sys
from pathlib import Path

# Make the harness importable whether run as a script or a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval_harness.chess_scorers import _check_faithfulness, score_forbidden_phrases
from eval_harness.rule_scorers import ScoreResult


def score_file(outputs_path: Path) -> list[dict]:
    """Score every record in the driver's JSON output. Returns one row per case."""
    records = json.loads(outputs_path.read_text())
    if not isinstance(records, list):
        raise SystemExit(f"expected a JSON array in {outputs_path}, got {type(records).__name__}")

    rows: list[dict] = []
    for rec in records:
        output = rec.get("output", "")
        tags = set(rec.get("tags", []))

        # The faithfulness core, run against the pre-supplied tags (the chess
        # app's candidates.json carries tags per case — no FEN derivation needed).
        faithfulness = _check_faithfulness(output, tags)

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
    honest = sum(1 for r in rows if r["honesty_passed"])
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
        f"| Reason-faithfulness pass | {faithful}/{total} ({faithful/total:.0%}) |",
        f"| Honesty-gate pass (no forbidden phrases) | {honest}/{total} ({honest/total:.0%}) |",
        f"| First-token latency (litert only) | "
        + (f"{sum(ft_latencies)//len(ft_latencies)} ms mean ({len(ft_latencies)} samples)" if ft_latencies else "—"),
        f"| Complete latency (litert only) | "
        + (f"{sum(cmpl_latencies)//len(cmpl_latencies)} ms mean ({len(cmpl_latencies)} samples)" if cmpl_latencies else "—"),
        "",
        "## Per-case results",
        "",
        "| Case | Route | Tags | Faithful | Honest | Failed check |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        tags_str = ", ".join(r["tags"]) or "—"
        faithful_str = "✓" if r["faithfulness_passed"] else "✗"
        honest_str = "✓" if r["honesty_passed"] else "✗"
        failed = []
        if not r["faithfulness_passed"]:
            failed.append(f"faithfulness: {r['faithfulness_reason']}")
        if not r["honesty_passed"]:
            failed.append(f"honesty: {r['honesty_reason']}")
        failed_str = "; ".join(failed) or "—"
        lines.append(f"| {r['id']} | {r['route']} | {tags_str} | {faithful_str} | {honest_str} | {failed_str} |")

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
        "Honesty = the forbidden-phrase gate (no 'Stockfish thinks' / 'engine depth' / etc.).",
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
