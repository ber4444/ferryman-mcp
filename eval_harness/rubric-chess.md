# Judge rubric — chess-opening-coach skill

The judge scores each chess-coach output on five criteria, 1–5 each. A specific
rubric matters: a vague one produces scores that don't reproduce across runs.

This rubric is deliberately **distinct from the objective exact-match scorer**.
The exact-match scorer (rule layer) checks whether the `FINAL ANSWER:` move or
eval band is correct — that is the objective floor. The judge (this rubric)
scores the *coaching explanation quality* — the product-relevant signal a
correct move alone doesn't capture. The scorecard reports both as separate
axes; they are never collapsed into one number.

The skill's contract: given a FEN + question, reason step by step about the
position using the bundled concept corpus, then end with a single
`FINAL ANSWER:` line.

## Criterion 1 — Specificity to this position (1–5)

Is the reasoning genuinely about *this* position, or generic chess boilerplate
that could follow any move?

- **5** — Names concrete features of this exact position: the piece on its
  square, the specific threat it creates or parries, the tactical motif
  (pin/fork/skewer) or positional factor actually present.
- **3** — Mentions a relevant general principle (develop pieces, control the
  center) but doesn't tie it to the specific squares/pieces in the FEN.
- **1** — Could follow any position. Reads like a template ("this is a good
  move that improves development") with nothing position-specific.

## Criterion 2 — Factual move/eval correctness (1–5)

This cross-checks the objective scorer's verdict. The model's `FINAL ANSWER:`
move should be the strongest move, or the eval band should match the engine's
assessment.

- **5** — The final answer is the objectively best move / correct eval band,
  AND the reasoning correctly explains why.
- **3** — The final answer is defensible (a good, if not unique-best, move) but
  the reasoning slightly mischaracterizes why, or vice versa.
- **1** — The final answer is a clear blunder (hangs material, misses a forced
  win, wildly wrong eval), OR the reasoning contradicts the answer given.
  Asserting a losing move is winning is the worst case.

## Criterion 3 — Grounding / no fabrication (1–5)

Does the reasoning stay within what the position actually shows, or does it
invent pieces, squares, lines, or engine claims that aren't there?

- **5** — Every claim about the position is verifiable from the FEN. No mention
  of "engine depth", "Stockfish thinks", ELO, or ratings. No unsupported
  certainty ("forced mate", "guaranteed win") unless it is actually forced.
- **3** — Mostly grounded but includes one unverified claim or a vague engine
  attribution ("the engine prefers this").
- **1** — Fabricates board features (a piece that isn't there, a square the
  piece can't reach) or asserts engine provenance/ratings. This is the failure
  mode the forbidden-phrase gate exists to catch.

## Criterion 4 — Reasoning quality (1–5)

Does the step-by-step reasoning show genuine chess understanding, or is it hand-
waving that happens to land on an answer?

- **5** — Correctly identifies the key idea (the tactic, the threat, the
  positional plan) and traces why the chosen move achieves it. Considers
  the opponent's best reply where it matters.
- **3** — Reaches a reasonable move but the reasoning is shallow or omits the
  opponent's response.
- **1** — No real reasoning — jumps to an answer, or "reasons" in empty
  phrases ("this move is strong and puts pressure on the opponent").

## Criterion 5 — Tone and format (1–5)

Is the output well-structured and appropriately pitched, with the required
`FINAL ANSWER:` line present and correctly formatted?

- **5** — Concise (under ~200 words), step-by-step reasoning then a single
  correctly-formatted `FINAL ANSWER: <answer>` line. UCI move for tactics,
  centipawn band for position judgment.
- **3** — Usable but too long, poorly structured, or the final-answer line is
  present but malformed (e.g. missing the `FINAL ANSWER:` prefix).
- **1** — No `FINAL ANSWER:` line at all (unscorable by the objective layer),
  or wildly off-format / over-long.

## Aggregation

The overall judge score is the mean of the five criteria (1.0–5.0). Two hard
gates regardless of the mean:

- **Criterion 2 below 3.0** (factual correctness) — a wrong move presented as
  correct is a correctness failure.
- **Criterion 3 below 3.0** (grounding) — fabricating board state or engine
  claims is the failure mode this harness exists to catch.
