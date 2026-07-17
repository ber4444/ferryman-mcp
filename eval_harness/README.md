# ferryman eval harness

Scores a ferryman skill against a human-authored golden set. Two scorer layers:

- **`rule_scorers.py`** — deterministic checks per `expectedClaims` key
  (comp figures, fabricated-entity check, citation presence, remote policy,
  tech stack). Returns pass/fail + reason.
- **`judge_scorer.py`** — an LLM-as-judge on the subjective criteria a rule
  can't check (specificity, source-traceability, honesty, tone). Calls a
  *different* provider than the one being evaluated.

## Runner

The primary runner is the hand-written `run_scorecard.py` — it's what CI uses
and what the committed scorecards are produced with. It drives ferry through
the same channels any consumer uses (subprocess or HTTP), runs the rule
scorers, optionally the judge, and writes the per-provider matrix.

The harness **degrades gracefully** if optional deps are absent: `invoke.py`
uses only stdlib (`urllib`), and `judge_scorer.py` records a skip rather than
crashing when its deps or API key are missing.

## Skill under test: company/role research

The default target skill is **company/role research** (`company-role-research`,
shipped in `ferryman/skills/`): given a company and a job title, it drives a
fetch MCP tool to research the company and report on dimensions relevant to a
mobile-engineer candidate. It is a strong eval target because:

- Clear, checkable claims (tech stack, remote policy, AI posture, mobile-first).
- No arithmetic ground truth (no "compute the right number" failure mode).
- Directly relevant to an interview story.

The harness invokes whatever skill name is configured (default
`company-role-research`); the golden set and scorers are skill-agnostic and
re-point with one `SkillSpec` change.

## Multi-skill harness (`--skill`)

The harness scores more than one skill. Each skill has a `SkillSpec` in
`run_scorecard.py` binding its golden set, scorer callable, scorecard output
paths, and judge rubric. `--skill <name>` selects which to run:

```bash
python eval_harness/run_scorecard.py --skill chess-opening-coach --all-providers
```

Adding a skill is: add a `SKILL.md`, a golden set, a scorer module, and one
`SkillSpec` entry — the runner, incremental-save, multi-provider matrix, and
judge layer are all skill-agnostic. The default (no `--skill`) is still
`company-role-research`, so existing CI/scripts are unchanged.

### chess-opening-coach

A chess position-evaluation skill, scored against an **objective exact-match**
golden set — a stricter floor than company-research's positive-presence checks.

- **Golden set:** `golden/chess_golden.json` — a vendored subset of
  [ChessQA](https://github.com/CSSLab/chessqa-benchmark) (CSSLab, MIT; see
  `golden/CHESS_GOLDEN_LICENSE.txt`). 40 stratified cases: 20 Short Tactics
  (UCI best-move, beginner→expert) + 20 Position Judgment (centipawn-band).
- **Scorers (`chess_scorers.py`):** extract the `FINAL ANSWER:` line (ChessQA's
  contract, ported verbatim) and exact-match it — UCI normalization for tactics,
  string match for eval bands. A forbidden-phrase gate (ported from the chess
  app's `MoveCoachResponseValidator`) fails engine-depth/ELO/unsupported-certainty
  claims.
- **Judge:** the existing family-excluded judge against `rubric-chess.md`
  (coaching-explanation quality — distinct from the objective floor).

**Honesty note.** The ChessQA subset is a *bootstrap* set: templated and
objective, runnable today. It is not the engine-grounded canonical set — that
is a specced follow-on in
[`docs/plans/chess-lichess-curation.md`](../docs/plans/chess-lichess-curation.md)
(not yet built). No scorecard numbers are committed until a real run produces
them.

## The golden set and the human-review gate

`golden/golden_set.json` holds 48 cases (47 real companies + 1 deliberate
negative: "Acme Holdings"). Each case's `expectedClaims` is a map of checkable
assertions, not prose answers.

**This is the harness's ground truth and the plan's single most important
quality control.** The golden set was drafted by the agent but must be
**human-reviewed and signed off before M1 consumes it.** Until then, any
scorecard produced is provisional. To sign off, review each case's
`expectedClaims` and `notesForReviewer` and edit `golden_set.json` directly;
record the sign-off by setting `goldenSetApproved: true` in
`golden/approval.json` (see below).

## Running

```bash
# rule scorers only (default — no judge API key needed)
python -m eval_harness.run_scorecard

# add the LLM judge layer (defaults to OpenAI gpt-4o-mini — family `gpt`,
# zero overlap with the evaluated glm/gemini/meta providers, so no skips)
JUDGE_API_KEY=... python -m eval_harness.run_scorecard --judge

# run once per configured provider (the multi-provider matrix)
python -m eval_harness.run_scorecard --all-providers

# tests (rule scorers)
python -m pytest eval_harness/ -q
```

The scorecard is written to `scorecard.md` (human-readable) and
`scorecard.json` (machine-readable) with **real numbers from real invocations**.
No placeholder rows, no fabricated deltas — that is the plan's hard rule.

## Invocation modes

- `--mode http` (default for `auto`): POSTs to a running `ferry serve` instance
  at `FERRY_HTTP_URL` (default `http://localhost:8080`).
- `--mode subprocess`: shells out to the installed `ferry` binary.
- `--mode auto`: tries HTTP, falls back to subprocess.

Neither fabricates output if both are unavailable — `invoke()` raises a clear
error instead.

## Consistency sanity check for the judge

Before trusting judge scores in the scorecard, run the same case through the
judge 3–5 times and confirm the score doesn't swing by more than ~1.0 across
runs. A wildly inconsistent judge is worse than no judge. The
`judge_variance()` helper in `tests/test_judge_consistency.py` does the repeats
and reports per-criterion spread; a spread > 1.0 is the "don't trust" signal.

The judge also enforces **family exclusion** — a judge never grades its own
family. The default judge (`gpt-4o-mini`, family `gpt`) has no overlap with the
configured evaluated providers (`glm` / `gemini` / `meta`), so no rows are
skipped. If you re-point the judge at a GLM/Gemini/Llama model, those
providers' rows skip with a `family conflict (skipped)` reason — by design.
The `model_family()` helper derives the family from the model id; a collision
raises `JudgeFamilyConflict`, which the scorecard runner catches and records
as a skip.

## Latency and cost columns

The `--all-providers` scorecard reports three measured columns per case and a
per-provider aggregate:

- **Latency** — wall-clock of the whole skill invocation, measured in the
  Python adapter (`invoke.py`). Accurate end-to-end.
- **Cost** — computed from `pricing.json`, which records verified per-token
  prices with `dateChecked` and `sourceUrl`. glm-5-turbo: $1.20/M input,
  $4.00/M output (checked 2026-07-16 against `docs.z.ai/guides/overview/pricing`).
- **Pricing date** — when the per-token price was last verified against the
  live docs. Re-verify before trusting cost numbers older than a few weeks.

**How cost is computed:** the Kotlin provider parses the `usage` block and
threads real token counts through `CompletionResult → SkillResult →
InvokeResponse` (HTTP) / a `_meta` JSON line (subprocess) into `estimate_cost`,
which uses them when present. Rows where the provider reported no `usage` (or
runs from before token-propagation landed) fall back to chars ÷ 4. The current
scorecard was generated by the propagation build, so its cost column is exact.
The latency number is always real (wall-clock in the Python adapter).

