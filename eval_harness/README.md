# ferryman eval harness

Scores a ferryman skill against a human-authored golden set. Two scorer layers:

- **`rule_scorers.py`** — deterministic checks per `expectedClaims` key
  (comp figures, fabricated-entity check, citation presence, remote policy,
  tech stack). Returns pass/fail + reason.
- **`judge_scorer.py`** — an LLM-as-judge on the subjective criteria a rule
  can't check (specificity, source-traceability, honesty, tone). Calls a
  *different* provider than the one being evaluated.

## Tooling choice: promptfoo-default, Braintrust-optional

**Default: promptfoo.** Chosen because it is fully self-hosted, needs no
account, and runs locally against any OpenAI-compatible endpoint — which is
exactly ferryman's provider model. Install it with `pip install -e .[promptfoo]`.

**Alternative: Braintrust.** If you want hosted dataset versioning and
experiment comparison, `pip install -e .[braintrust]` and set `BRAINTRUST_API_KEY`.
The scorer layer is backend-agnostic; only the runner adapts.

The harness **degrades gracefully** if optional deps are absent: `invoke.py`
uses only stdlib (`urllib`), and `judge_scorer.py` records a skip rather than
crashing when its deps or API key are missing.

## Skill under test: company/role research

No production telemetry exists yet for ferryman, so per the eval-harness plan's
default, the target skill is **company/role research** — a strong candidate
because:

- Clear, checkable claims (comp bands, remote policy, tech stack).
- No arithmetic ground truth (no "compute the right number" failure mode).
- Directly relevant to an interview story.

The harness invokes whatever skill name is configured (default
`company-role-research`). Until that skill ships in `ferryman/skills/`, the
harness runs against `hello-repo` to prove the loop end-to-end — the golden set
and scorers are skill-agnostic and re-point with one config change.

## The golden set and the human-review gate

`golden/golden_set.json` holds 25 cases (24 real companies + 1 deliberate
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

# add the LLM judge layer
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
family (per the z.ai/GLM addendum hard rule #5). If the evaluated route is GLM,
the judge must be a non-GLM model, and vice versa. The `model_family()` helper
derives the family from the model id; a collision raises `JudgeFamilyConflict`,
which the scorecard runner catches and records as a skip.

## Latency and cost columns

The `--all-providers` scorecard reports three measured columns per case and a
per-provider aggregate:

- **Latency** — wall-clock of the whole skill invocation, measured in the
  Python adapter (`invoke.py`). Accurate end-to-end.
- **Cost (est.)** — computed from `pricing.json`, which records verified
  per-token prices with `dateChecked` and `sourceUrl`. GLM-5.2: $1.40/M input,
  $4.40/M output (checked 2026-07-13 against `docs.z.ai/guides/overview/pricing`).
- **Pricing date** — when the per-token price was last verified against the
  live docs. Re-verify before trusting cost numbers older than a few weeks.

**Why cost is labeled "est.":** the Kotlin providers don't yet parse `usage`
from the provider response, so token counts are estimated (chars ÷ 4). When real
`usage` data flows through `CompletionResult → SkillResult → InvokeResponse`,
the same `pricing.json` formula computes real cost — only the token source
changes. The latency number is already real.

