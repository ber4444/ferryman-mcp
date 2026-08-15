#!/usr/bin/env python3
"""
Score an on-device **Move Coach** bench run, model against the deterministic
baseline it has to beat.

  chess app (AndroidBenchRunner)  →  results.jsonl  →  this script  →  comparison

The sibling of ``score_litert_outputs.py`` and the same bypass for the same
reason: ferryman has no on-device provider, the generator lives in the chess
app, and the scorers here are provider-agnostic — they check prose against a
tag set. What is new is the **second column**. Each row of the chess app's JSONL
now carries the ``deterministicExplanation`` that was in the model's own prompt,
so both candidates for the same position can be scored by one scorer in one
pass. That is the actual product question: not "does the model pass a gate" but
"does it beat the free, instant, already-shipping sentence".

Deliberately no API key and no network. The LLM-judge layer answers a different
question (graded coaching quality, including failure modes nobody enumerated);
this answers the falsifiable half — does either candidate assert something the
supplied tags do not support.

**Known blind spot, and it is the one that matters here.** ``_check_faithfulness``
is a bag-of-concepts check: it asks whether a concept appears, never which move
it was attached to. A run of nano-v3 on 2026-08-15 produced *"e4 would have been
a better choice, because it develops a piece"* where ``develops`` described the
move actually played — the concept is supplied, so this scorer passes it, and so
does the chess app's own validator. Do not read a pass here as "no invention".

Usage:
    python3 eval_harness/score_move_coach_run.py <results.jsonl> <candidates.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval_harness.chess_scorers import _check_faithfulness, check_invention, score_forbidden_phrases
from eval_harness.score_litert_outputs import _check_piece_type, _expected_piece_name


def _score_text(text: str, tags: set[str], fen: str, uci: str) -> dict:
    """Faithfulness + honesty + piece-type for one candidate sentence.

    ``_check_faithfulness`` folds two checks into one verdict, and only one of
    them transfers to this surface. **Invention** — a high-stakes concept
    asserted that the tags do not supply — is the truthfulness question. **Coverage**
    — every supplied tag's concept mentioned — is a recall requirement written
    for a reasoning-length answer; the Move Coach panel is two sentences under a
    300-char cap and is handed up to five tags, so it fails coverage by
    construction (measured: 81/100 for the model and 99/100 for the deterministic
    line, which is the shipping product). Reported separately, and only invention
    is counted as a failure.
    """
    faithfulness = _check_faithfulness(text, tags)
    # Independently of coverage — see check_invention, which exists because the
    # folded verdict masked exactly this comparison.
    invented_tags = check_invention(text, tags)
    forbidden = score_forbidden_phrases(text, {})
    expected_piece = _expected_piece_name(fen, uci)
    piece = (
        _check_piece_type(text, expected_piece)
        if expected_piece
        else None
    )
    return {
        "invented": bool(invented_tags),
        "invented_tags": invented_tags,
        "faithful_reason": faithfulness.reason,
        "covers": faithfulness.passed,
        "honest": forbidden.passed,
        "piece_ok": None if piece is None else piece.passed,
        "piece_reason": None if piece is None else piece.reason,
        # Clears the falsifiable checks: no invented high-stakes concept, no
        # engine-provenance claim, right piece. Coverage deliberately excluded.
        "clears": (not invented_tags) and forbidden.passed and (piece is None or piece.passed),
    }


def score_run(rows: list[dict], cases: dict[str, dict], verbose: bool = False) -> dict:
    model_clear = baseline_clear = compared = 0
    coverage = {"model": 0, "baseline": 0}
    model_only: list[str] = []
    baseline_only: list[str] = []
    skipped = {"placeholder": 0, "no answer": 0, "unknown case": 0}
    reasons = {"model": {}, "baseline": {}}

    for row in rows:
        # A placeholder run's rows read exactly like real ones; the chess app
        # marks them precisely so this cannot be scored by accident.
        if not row.get("factsPopulated") or row.get("isFallbackGolden"):
            skipped["placeholder"] += 1
            continue
        text = row.get("rawOutput")
        baseline = row.get("deterministicExplanation")
        if not text or not baseline:
            skipped["no answer"] += 1
            continue
        case = cases.get(row["caseId"])
        if case is None:
            skipped["unknown case"] += 1
            continue

        # Both the golden case's hand-authored tags and the engine-detected
        # motifs the prompt carried. The model was handed the motifs, so it is
        # answerable for them.
        tags = set(row.get("tags", [])) | set(row.get("motifs", []))
        fen, uci = case.get("fen", ""), case.get("bestMoveUci", "")

        m = _score_text(text, tags, fen, uci)
        b = _score_text(baseline, tags, fen, uci)
        compared += 1
        model_clear += m["clears"]
        baseline_clear += b["clears"]
        coverage["model"] += m["covers"]
        coverage["baseline"] += b["covers"]
        if m["clears"] and not b["clears"]:
            model_only.append(row["caseId"])
        if b["clears"] and not m["clears"]:
            baseline_only.append(row["caseId"])
        for who, s in (("model", m), ("baseline", b)):
            if not s["clears"]:
                key = ("asserts unsupported: " + ",".join(s["invented_tags"])) if s["invented"] else (
                    s["piece_reason"] if s["piece_ok"] is False else "forbidden phrase"
                )
                reasons[who][key] = reasons[who].get(key, 0) + 1
                if verbose:
                    text_of = text if who == "model" else baseline
                    print(f"  [{who}] {row['caseId']} tags={sorted(tags)}\n"
                          f"      {key}\n      {text_of[:220]}")

    return {
        "compared": compared,
        "skipped": skipped,
        "model_clear": model_clear,
        "baseline_clear": baseline_clear,
        "model_only": model_only,
        "baseline_only": baseline_only,
        "reasons": reasons,
        "coverage": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="AndroidBenchRunner results.jsonl")
    parser.add_argument("candidates", type=Path, help="evals/golden/candidates.json")
    parser.add_argument("--verbose", action="store_true", help="print every failing candidate")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
    cases = {c["id"]: c for c in json.loads(args.candidates.read_text())}
    r = score_run(rows, cases, verbose=args.verbose)

    print(f"compared:            {r['compared']} rows   skipped: {r['skipped']}")
    print(f"clears (model):      {r['model_clear']}/{r['compared']}  {r['reasons']['model']}")
    print(f"coverage, FYI only:  model {r['coverage']['model']}/{r['compared']}, baseline {r['coverage']['baseline']}/{r['compared']}")
    print(f"clears (baseline):   {r['baseline_clear']}/{r['compared']}  {r['reasons']['baseline']}")
    print(f"model clears where baseline does not: {len(r['model_only'])} {r['model_only'][:8]}")
    print(f"baseline clears where model does not: {len(r['baseline_only'])} {r['baseline_only'][:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
