# Plan: Eval Harness for One ferryman Skill

Target: the ferryman repository (not the chess-app repo). Suggested location for this file:
`docs/plans/eval-harness.md` inside that repo.

## Context for the agent

ferryman is a local-first MCP orchestration layer above Claude Code — pluggable skills,
multi-provider routing, multi-channel I/O. This plan does two things at once: it builds a real
eval harness for one skill (the specific, highest-leverage ask), and in doing so it's also the
work that makes ferryman itself presentable enough to publish — a repo with an eval harness and
a scorecard reads very differently from a repo with neither.

**This plan assumes a repo layout it hasn't seen.** Before M0, locate: where skill definitions
live (likely something like `skills/<name>/` or a manifest file), where provider/model routing
config lives, and whether there's an existing logging/telemetry path that records which skill
ran how often — that log is the fastest way to pick the skill in M0. Adjust every path below to
match what's actually there; treat the structure in this plan as illustrative, not literal.

## Hard rules

- **The golden set's reference claims are human-authored and human-reviewed.** The agent may
  propose candidate inputs and draft claims, but a model does not get to grade its own homework —
  get explicit sign-off on the golden set before M1 starts consuming it.
- **Never fabricate "what it found" results.** If the scorecard doesn't exist yet, the article
  and this plan's own status notes say so plainly. Numbers only get written down after a real run.
- **Don't loosen a scorer to make a case pass.** If a golden case reveals a real skill bug, fix
  the skill's prompt or tool logic, not the grader.
- **Python for the harness**, deliberately — even if ferryman's core is TypeScript/Kotlin/other.
  The goal includes building genuine fluency with the Python eval-tooling ecosystem, not just
  shipping a working harness in whatever language is most convenient.
- **Don't touch ferryman's production routing or skill code** beyond what's needed to invoke a
  skill programmatically for the harness. This is an evaluation layer, not a refactor.

## Success command

```bash
python -m pytest eval_harness/ -q && python eval_harness/run_scorecard.py --all-providers
```

Plus: `eval_harness/scorecard.md` (or the Braintrust/promptfoo dashboard equivalent) regenerated
with real numbers, and a CI job wired to run on skill-prompt changes.

## M0 — pick the skill and author the golden set

- Identify the most-invoked skill from ferryman's own logs, or default to the company/role
  research skill if no telemetry exists (it's a strong candidate: clear checkable claims, no
  arithmetic ground truth, directly relevant to an interview story).
- Draft 20–30 golden inputs (e.g., company + role pairs) as JSON: `id`, the input the skill
  receives, and `expectedClaims` — a list of checkable assertions, not prose answers. Example
  shape:

  ```json
  {
    "id": "case-014",
    "input": { "company": "...", "role": "..." },
    "expectedClaims": {
      "mustMentionCompBand": true,
      "mustFlagRemotePolicy": true,
      "mustNotFabricateFigures": true,
      "sourceUrlsMustBeCited": true
    }
  }
  ```

- **Human review gate:** present the drafted golden set to the repo owner before M1. Do not
  proceed until it's approved — this is the harness's ground truth and the plan's single most
  important quality control.

## M1 — harness scaffolding + rule-based scorers (Python)

- New `eval_harness/` package. Pick Braintrust's Python SDK (`pip install braintrust`) as the
  default — it gives dataset versioning and experiment comparison for free — or `promptfoo`
  (Python assertions via its `python` assertion type) if a fully self-hosted, no-account option
  is preferred. Record the choice and why in `eval_harness/README.md`.
- `eval_harness/invoke.py` — a thin adapter that calls the target ferryman skill programmatically
  (through whatever the skill's own entry point is) and returns raw output plus any tool-call
  trace.
- `eval_harness/rule_scorers.py` — deterministic checks per `expectedClaims` key: regex/keyword
  presence for comp figures, a fabricated-entity check against a disallow-list built from the
  golden case's known-real names, citation-presence check. Each scorer returns pass/fail plus a
  reason string, not just a boolean.
- `eval_harness/run_scorecard.py` — runs the full golden set through `invoke.py`, applies the
  rule scorers, and writes a first-pass scorecard (rule-based only; M2 adds the judge layer).

## M2 — LLM-as-judge scorer

- `eval_harness/rubric.md` — a short, specific written rubric for the subjective criteria a rule
  can't check: is the recommendation actually specific to this company, does every claim trace
  to a cited source, is the tone appropriate. Specificity matters — a vague rubric produces a
  judge score that doesn't reproduce across runs.
- `eval_harness/judge_scorer.py` — calls a separate model (a different provider than the one
  being evaluated, to avoid a model favoring its own outputs) with the rubric and the skill's
  output, parses a structured score (e.g. 1–5 per rubric criterion) plus a short justification.
- Sanity-check judge consistency: run the same case through the judge 3–5 times and confirm the
  score doesn't swing wildly before trusting it in the scorecard.

## M3 — multi-provider run matrix

- Read ferryman's provider-routing config to enumerate the providers/models it can route the
  target skill to.
- Extend `run_scorecard.py` with a `--all-providers` mode: run the full golden set once per
  provider, and add provider, score, latency, and (where known) cost columns to the scorecard.
- This table is the concrete evidence for "multi-provider routing" as a real, measured
  capability rather than a config option nobody has verified end-to-end.

## M4 — CI wiring

- A CI job (GitHub Actions or equivalent) triggered on changes to skill prompts/definitions or
  `eval_harness/`, running the rule-based scorers at minimum (the judge-model pass can be a
  separate, less-frequent nightly job if per-PR judge-model cost is a concern — state that
  tradeoff explicitly in the workflow file's comments).
- A rule-scorer regression or a judge-score drop past a stated threshold fails the job.

## Verification matrix

| Check | Command |
|---|---|
| Golden set approved by owner | manual sign-off, recorded in `eval_harness/README.md` |
| Rule scorers pass on a known-good case | `pytest eval_harness/test_rule_scorers.py` |
| Judge scorer is reasonably stable | manual 3–5x repeat run, documented in the PR |
| Multi-provider matrix runs | `python eval_harness/run_scorecard.py --all-providers` |
| CI wired | a deliberately broken golden case fails the CI job in a test PR |

## Article + publication gate

Per the outline's own honesty rule: the "what it actually found" section of the article is
written only after M3 produces a real scorecard — no placeholder numbers, no illustrative
fabricated deltas. Publishing ferryman itself (making the repo public, if it isn't already)
should happen alongside this work landing, not before — a repo with an eval harness and a
scorecard is a materially stronger first impression than the orchestration layer alone.
