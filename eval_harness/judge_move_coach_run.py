#!/usr/bin/env python3
"""
Judge an on-device **Move Coach** run head-to-head against the deterministic
baseline that ships today.

    results.jsonl (model + deterministic, same row)  →  judge  →  preference + veto

Why pairwise rather than the 5-criterion rubric in ``rubric-chess.md``: that
rubric grades the *opening-coach skill*, whose contract is step-by-step
reasoning ending in a ``FINAL ANSWER:`` line. A Move Coach panel is two
sentences under a 300-char cap with no final-answer line, so criteria 2 and 5
would score it on a contract it never had. And the product question is not "what
grade does this earn" — it is "does it beat the free, instant sentence already in
the same row". That is a comparison, so the judge is asked for one.

Two design choices the result depends on:

* **Blinded and order-randomised.** Candidate order is shuffled per row from a
  fixed seed, so a judge's position bias cannot become the model's win rate. The
  mapping is kept and un-blinded only when aggregating.
* **The veto is asked before the preference, and asked neutrally.** The judge
  lists claims the supplied facts do not support, without being told which
  failure modes were expected. Naming them ("watch for a reason attached to the
  engine's move") would have the judge confirm the hypothesis it was handed —
  the rule-based layer already covers what is enumerable, and the judge is here
  precisely for what is not.

Requires ``JUDGE_API_KEY`` (see .env.example). ~1 call per row.

Usage:
    python3 eval_harness/judge_move_coach_run.py <results.jsonl> [--limit N] [--workers N]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval_harness.judge_scorer import (
    JUDGE_API_KEY_ENV,
    JUDGE_MODEL,
    JudgeFamilyConflict,
    _call_judge,
    model_family,
)

# The identifier the *model* is judged under. Deliberately not the row's
# `mlkit-aicore-full`, which family-exclusion would read as family "mlkit" and
# happily let a Gemini judge grade Gemini Nano's output.
EVALUATED_MODEL = "gemini-nano-v3"

# Two calls per row, because the two questions need different information and mixing them
# corrupted both. The veto needs the *whole* ground truth the writer had, which includes the
# deterministic sentence — it carries the piece-and-square detail ("your bishop on g4 pins the
# pawn on e2") that the fact list summarises as the slug `pin`. Judging against the slugs alone
# flagged 13 of 98 deterministic lines as inventions, when that text is generated from those very
# facts by code and cannot invent. But the deterministic sentence cannot appear in the *preference*
# call: it is one of the two candidates, and a judge that can see which candidate is quoted
# verbatim in its own ground truth is no longer blind.
VETO_PROMPT = """\
A chess coaching assistant wrote one or two sentences for a club-level player about a move they
just played. It was given exactly this and nothing else:

{facts}
- Reference explanation it was given as ground truth: "{baseline}"

What it wrote:
{candidate}

List every claim it makes that the information above does not support.

Paraphrase is NOT invention. Restating the facts in ordinary chess language is supported: calling
an assessment of "mistake" a weakening of the position, describing a "pawn-push" as gaining space,
or repeating anything in the reference explanation. Judging severity from the percentage is
supported.

A claim is unsupported only when it asserts something the information neither states nor directly
implies — naming a piece, square, file, diagonal or line that appears nowhere above; asserting what
was captured when nothing says; or attaching a stated fact to a different move than the one it
describes. In particular, the listed features describe the move the player made: saying the engine
liked *that* move because of them is supported, while giving a reason why the engine's preferred
alternative would have been better is not — the alternative is named above with no reason at all.

If it only restates what it was given, return an empty list. Say so rather than reaching.

Return ONLY a JSON object: {{"unsupported": ["..."], "reason": "<one sentence>"}}
"""

PREFERENCE_PROMPT = """\
Two coaching assistants each wrote one or two sentences for a club-level player about the same move
they just played, from the same facts:

{facts}

Candidate A:
{a}

Candidate B:
{b}

Which is the better explanation to show the player? Prefer the one that better tells them why the
move was good or bad. A confident-sounding sentence that asserts something these facts do not
establish is worse than a plain one that stays within them. Answer "tie" if neither is better.

Return ONLY a JSON object: {{"better": "A" | "B" | "tie", "reason": "<one sentence>"}}
"""


def _facts_block(row: dict) -> str:
    """The prompt's fact list — the same facts MoveCoachPromptBuilder gave the model."""
    lines = [f"- The player played: {row.get('moveDisplay')}"]
    if row.get("moveClassName"):
        lines.append(f"- Engine assessment of that move: {row['moveClassName'].lower()}")
    if row.get("motifs"):
        lines.append(f"- Tactical features detected: {', '.join(row['motifs'])}")
    lost = row.get("winPercentLost")
    if lost is not None:
        # Positive means lost — "changed by" read as a gain to the judge, which had it calling a
        # correct "drops your winning chances by 3%" a contradiction of the facts.
        lines.append(
            f"- The move cost the player {lost:.1f}% of their winning chances" if lost >= 0
            else f"- The move gained the player {abs(lost):.1f}% of winning chances",
        )
    if row.get("betterMoveDisplay"):
        lines.append(
            f"- The move the engine preferred instead: {row['betterMoveDisplay']} "
            "(no reason for this preference is given)",
        )
    return "\n".join(lines)


def judge_row(row: dict, api_key: str, rng_seed: int) -> dict | None:
    model_text = (row.get("rawOutput") or "").strip()
    baseline = (row.get("deterministicExplanation") or "").strip()
    if not model_text or not baseline:
        return None
    if not row.get("factsPopulated") or row.get("isFallbackGolden"):
        return None

    facts = _facts_block(row)

    def ask(prompt: str) -> dict | None:
        text = _call_judge(prompt, api_key).strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # 1. Veto, model only, against the full ground truth including the reference sentence.
    veto = ask(VETO_PROMPT.format(facts=facts, baseline=baseline, candidate=model_text))
    # 2. The same veto on the deterministic line. It is derived from these facts by code, so any
    #    flag here is the judge's error and the rate is this run's false-positive floor.
    control = ask(VETO_PROMPT.format(facts=facts, baseline=baseline, candidate=baseline))
    # 3. Preference, blinded and order-randomised, facts only.
    rng = random.Random(rng_seed)
    model_is_a = rng.random() < 0.5
    a, b = (model_text, baseline) if model_is_a else (baseline, model_text)
    pref = ask(PREFERENCE_PROMPT.format(facts=facts, a=a, b=b))

    if veto is None or pref is None:
        return {"caseId": row["caseId"], "error": "unparseable judge reply"}

    better = str(pref.get("better", "")).strip().lower()
    model_key, baseline_key = ("a", "b") if model_is_a else ("b", "a")
    winner = (
        "tie" if better == "tie"
        else "model" if better == model_key
        else "baseline" if better == baseline_key
        else "unparsed"
    )
    return {
        "caseId": row["caseId"],
        "winner": winner,
        "model_unsupported": veto.get("unsupported") or [],
        "baseline_unsupported": (control or {}).get("unsupported") or [],
        "reason": pref.get("reason", ""),
        "model_text": model_text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--limit", type=int, default=0, help="judge only the first N rows")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", type=Path, help="write the per-row verdicts as JSON")
    args = parser.parse_args()

    api_key = os.environ.get(JUDGE_API_KEY_ENV)
    if not api_key:
        raise SystemExit(f"{JUDGE_API_KEY_ENV} not set")
    if model_family(JUDGE_MODEL) == model_family(EVALUATED_MODEL):
        raise JudgeFamilyConflict(
            f"judge {JUDGE_MODEL} shares a family with {EVALUATED_MODEL} — a judge never "
            "grades its own family",
        )

    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
    if args.limit:
        rows = rows[: args.limit]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda t: judge_row(t[1], api_key, t[0]), enumerate(rows)))
    verdicts = [r for r in results if r]

    errors = [v for v in verdicts if "error" in v]
    scored = [v for v in verdicts if "error" not in v]
    wins = {k: sum(1 for v in scored if v["winner"] == k) for k in ("model", "baseline", "tie", "unparsed")}
    model_bad = [v for v in scored if v["model_unsupported"]]
    base_bad = [v for v in scored if v["baseline_unsupported"]]

    print(f"judge:               {JUDGE_MODEL}  vs  {EVALUATED_MODEL}")
    print(f"judged:              {len(scored)} rows ({len(errors)} unparseable)")
    print(f"preferred:           model {wins['model']}, deterministic {wins['baseline']}, "
          f"tie {wins['tie']}, unparsed {wins['unparsed']}")
    print(f"unsupported claims:  model {len(model_bad)}/{len(scored)}, "
          f"deterministic {len(base_bad)}/{len(scored)}")
    print()
    for v in model_bad[:10]:
        print(f"  [{v['caseId']}] {v['model_unsupported']}")
        print(f"      {v['model_text'][:150]}")
    if args.out:
        args.out.write_text(json.dumps(scored, indent=2))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
