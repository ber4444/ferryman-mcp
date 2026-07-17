# Plan: Canonical Chess Golden Set — Lichess → Stockfish Curation Pipeline

Suggested location: `docs/plans/chess-lichess-curation.md` inside the ferryman
repo. Status: **not started** — this is the documented follow-on to the
ChessQA bootstrap set (see `eval_harness/README.md` → "chess-opening-coach").

## Why this exists

The chess-opening-coach skill currently scores against a **bootstrap** golden
set: a vendored subset of [ChessQA](https://github.com/CSSLab/chessqa-benchmark)
(CSSLab, MIT). That set is objective and exact-match scorable, but it is
templated and tests chess *understanding* in a synthetic framing. It is not the
authoritative ground truth for a *move-coaching* product.

The canonical set should be **engine-grounded**: positions sampled from the
Lichess Stockfish evaluation database, re-analyzed at a fixed engine config so
the ground truth is self-consistent, with move-quality labels derived from eval
deltas. This is the dataset design the chess-app article's "deeper methodology"
section ultimately rests on. Until this lands, the chess scorecard cites
ChessQA, not engine-derived labels.

This plan does **not** replace the ChessQA set — it adds a second golden set
(`golden/chess_curated.json`) alongside it, both wired as `--golden` options
under the same `--skill chess-opening-coach` entry, clearly labeled by
provenance. Nothing about the bootstrap path is removed.

## Hard rules

(Inherited from `docs/plans/eval-harness.md`, restated because they bind here too.)

- **The golden set is human-authored and human-reviewed.** The pipeline may
  sample and label automatically, but a person signs off on the frozen set
  before any scorecard citing it is published. This is the owner-must-verify
  convention the chess repo's own `evals/golden/README.md` applies.
- **Never fabricate numbers.** If the pipeline hasn't run, the README and
  scorecard say so. No placeholder rows in `chess_curated.json`.
- **Don't loosen a scorer to make a case pass.** If the curated set exposes a
  real skill bug, fix the skill prompt, not the grader.
- **Use the chess repo's own engine for reference evals** when possible. The
  chess-app `:chess-core` module already integrates Stockfish
  (`ChessEngine.evaluate(fen)`, `BaseStockfishEngine` → `go depth $EVAL_DEPTH`)
  and supports fixed-depth evaluation (`UciProtocolClient.evaluate(fen, depth=...)`).
  Self-consistent ground truth — same engine the product uses — is stronger
  than borrowing an external eval snapshot mixed across Stockfish versions.
- **Stratify, don't convenience-sample.** A golden set that over-represents
  one niche (e.g. only tactical puzzles, or only one rating band) is a biased
  gate. Stratification is a first-class step, not an afterthought.

## Data sources

- **Lichess Stockfish evaluation database** — ~395M positions evaluated by
  Stockfish, the canonical open eval source. Mirrored on Hugging Face
  ([Lichess/chess-position-evaluations](https://huggingface.co/datasets/Lichess/chess-position-evaluations))
  and Kaggle ([lichess/chess-evaluations](https://www.kaggle.com/datasets/lichess/chess-evaluations)).
  License: ODbL for the data (attribution required; derived sets must remain
  open). **Verify the license terms are compatible with vendoring a derived
  subset before M0 ends** — ODbL's share-alike clause may differ from ChessQA's
  MIT. If incompatible, the pipeline runs but ships only the *labels it
  computes* (not the Lichess rows) plus enough FEN strings to reproduce.
- **chess-core's `ChessEngine`** — the fixed-depth reference analyzer. Already
  in the chess repo, already Stockfish-backed, already Kotlin/Multiplatform.
  Re-using it keeps ground truth consistent with the product.

## Label types the pipeline produces

Per position, the pipeline computes:

- **Reference evaluation** — centipawn score (White's perspective) at a fixed
  Stockfish depth, via `ChessEngine.evaluate`. The depth is pinned at freeze
  time and recorded in the set's metadata; mixed depths are a correctness bug.
- **Best move** — `ChessEngine.getBestMove(fen)`, the engine's top choice.
- **Move-quality labels** — for a *candidate* move (e.g. the move the LLM
  proposed, or the move a Lichess game actually played), classify by eval delta
  against the best move: `best` / `excellent` / `good` / `inaccuracy` /
  `mistake` / `blunder`. The band thresholds are fixed in the pipeline and
  documented (Lichess's own analysis thresholds are the reference defaults).
- **Stratification tags** — phase (opening / middlegame / endgame), rating band
  (derived from the source game where available), position type (tactical /
  quiet / attacking / defensive). Computed from the FEN + move number, not
  hand-applied.

## Milestones

### M0 — feasibility + license check

- Fetch a small slice of the Lichess eval database (a few thousand rows) and
  confirm the FEN + eval format is as documented.
- **Resolve the ODbL licensing question** before sampling at scale. Decide:
  vendor a derived subset (if ODbL-compatible), or ship only computed labels +
  reproducible FEN seeds. Record the decision in this file's "Decisions" log.
- Confirm `ChessEngine.evaluate` / `getBestMove` run headless against a sample
  FEN at the target fixed depth and return deterministic results.

### M1 — the curation script

- New `pipeline/curate_chess.py` (Python, matching the harness language):
  1. Sample N positions from the Lichess eval source per a stratification plan
     (rating bands × phases × position types), not a random `head`.
  2. For each: call the chess-core `ChessEngine` (via a thin bridge — see
     "Engine bridge" below) at the pinned depth to get the reference eval +
     best move. Re-analyze, don't trust the Lichess snapshot's depth, so the
     set is internally consistent.
  3. Derive move-quality labels from eval deltas (candidate move vs best move).
  4. Attach stratification tags.
  5. Write `golden/chess_curated.json` in the same normalized shape as
     `chess_golden.json` (id, input, correctAnswer, answerFormat, taskCategory,
     provenance) so the existing scorer dispatch works unchanged.
- The script is deterministic given a seed — re-running reproduces the set.

### M2 — human review + freeze

- A person inspects the sampled positions and labels, prunes duplicates /
  ambiguous cases / positions where even the engine is uncertain, and freezes
  the set. The freeze is recorded as a version + date in the set's metadata.
- Until freeze, `chess_curated.json` does not exist on `main` — no half-curated
  set ships.

### M3 — wire into the harness

- Register `chess_curated.json` as a `--golden` option under the existing
  `chess-opening-coach` skill spec. The scorer (`chess_scorers.py`) already
  handles `uci` and `centipawn-band` answer formats; add `move-quality` if M1
  ships that label type (classification scorer, exact-match on the band).
- Scorecard labels provenance: a run against `chess_curated.json` cites
  "Lichess→Stockfish (engine-derived, frozen YYYY-MM-DD)", distinct from a
  ChessQA-bootstrap run.

### M4 — scorecard + article gate

- Run `--skill chess-opening-coach --golden golden/chess_curated.json
  --all-providers` and record real numbers. Only after this does the chess-app
  article's "canonical engine-grounded eval" claim become publishable.

## Engine bridge

The curation script is Python; `ChessEngine` is Kotlin/Multiplatform. Two
honest options (decide in M0):

1. **Reuse the perft-mcp path** — the chess repo's `:perft-mcp` module already
   wraps a Stockfish-backed MCP server. But it exposes only `stockfish_divide`
   (perft node counts), **not** evaluation. So it can't produce reference
   evals as-is. Extending it with an `evaluate` tool is the cleanest bridge:
   one new MCP tool, ferryman's host already connects to the server.
2. **Shell out to a local `stockfish` binary** directly from the Python script
   (UCI over stdin/stdout). Simpler, no chess-repo change, but the reference
   engine is then "whatever stockfish is on the machine", not guaranteed to
  match the product's vendored `sf_17`. Less self-consistent; acceptable as a
   bootstrap within M1 if documented.

Option 1 is preferred for the canonical set (self-consistency with the
product); option 2 is the M1 fallback.

## Verification matrix

| Check | Command |
|---|---|
| License compatibility resolved | decision recorded in this file's Decisions log |
| Curation script reproduces the set from a seed | `python pipeline/curate_chess.py --seed N` produces byte-identical output (modulo timestamp) |
| Reference evals are deterministic | same FEN → same eval across runs at the pinned depth |
| Owner reviewed + froze the set | version + freeze date recorded in `chess_curated.json` metadata |
| Harness scores the curated set | `python eval_harness/run_scorecard.py --skill chess-opening-coach --golden eval_harness/golden/chess_curated.json --all-providers` |
| Bootstrap set still works | `python eval_harness/run_scorecard.py --skill chess-opening-coach` (unchanged) |

## Decisions log

(filled in as milestones land)

- *YYYY-MM-DD* — ODbL licensing: [resolved how]
- *YYYY-MM-DD* — engine bridge: [option 1 or 2, with rationale]
- *YYYY-MM-DD* — fixed depth pinned at: [depth N, Stockfish version X]

## Sizing

Per the eval-set design literature: 200–500 positions for strict numeric tasks
(best-move, eval agreement) is the practical CI-scorable range; 50–150 for
explanation-heavy tasks. The curated set targets the former (objective
move-quality labels), so **~200–300 stratified positions** is the working
target — large enough to be statistically meaningful across strata, small
enough that a full `--all-providers` run completes in a CI-reasonable time and
the set remains human-inspectable at freeze time.
