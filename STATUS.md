# Project Status

Snapshot as of 2026-07-13. This file is the single source of truth for where
the ferryman project stands against its three plans. Update it when a milestone
lands or a gate is crossed.

## At a glance

| Layer | State | Tests |
|---|---|---|
| ferryman (Kotlin host + CLI + HTTP) | MVP built, CI green | 28 Kotlin tests |
| Eval harness (Python) | Scaffolded, rule + judge scorers wired | 31 Python tests |
| Real scorecard with live provider numbers | **Partial** — zai-glm 66% + hf-llama 81% (fixed scorer); gemini re-run pending | 177 rows |

All commits are on `main`. This branch (`status/project-status`) exists only to
carry this status document and open a draft PR for review.

## What's done

**ferryman host (commits 338a7d7 → 3c75b70):**

- M0 — Gradle/Kotlin 2.3.21 project, JDK 21 toolchain, `ferry` CLI (clikt 5.x),
  Apache-2.0, README with honest feature-status table, AGENTS.md / CLAUDE.md,
  ktlint + detekt, GitHub Actions CI
- M1 — MCP host: reads `.mcp.json`, spawns stdio servers via
  `StdioClientTransport`, initialize handshake, `tools/list`, namespaced
  `<server>.<tool>` registry
- M2 — `LlmProvider` interface + `AnthropicProvider` + `OpenAiCompatibleProvider`
  (z.ai GLM `glm-5.2`), TOML config, `providers list`, `RoutingLogger`
- M3 — `SkillLoader` scanning `skills/*/SKILL.md`, `Orchestrator.runSkill(name, input)`
  with model↔tool loop, `hello-repo` skill
- M4 — `HttpServer` (Ktor), `serve` CLI, `POST /invoke` sharing one orchestrator

**Eval harness (commits 37ca001, 3c75b70):**

- M0 — 25 golden cases (24 real companies + 1 negative "Acme Holdings"), drafted
  for human review, gated by `approval.json`
- M1 — `invoke.py` (http / subprocess / auto), `rule_scorers.py` (5 deterministic
  checks), `run_scorecard.py`
- M2 — `--all-providers` reads `config.toml` via stdlib `tomllib`; latency measured
  end-to-end; cost computed from `pricing.json` (GLM-5.2 $1.40/M in, $4.40/M out,
  verified 2026-07-13); per-provider aggregate row in the scorecard
- M3 — judge family-exclusion (GLM never grades GLM), `judge_variance()` consistency
  helper, 7 new tests
- M4 — `.github/workflows/eval-harness.yml` runs rule scorers on skill/harness
  changes

## First real scorecard run (2026-07-15)

The human gates are crossed, and the harness has produced runs against live
providers. A scorer bug was found during verification and **fixed**; two of three
providers now have real baselines:

- **Golden-set sign-off** — `eval_harness/golden/approval.json` reads
  `goldenSetApproved: true` (approved 2026-07-15 by `repo-owner`). The approved
  set is **59 cases** (58 real companies + the "Acme Holdings" negative).
- **API keys** — `ZAI_API_KEY` and `DEEPINFRA_API_KEY` were available; the
  gemini key hit rate limits during its run.

| Provider | Rule pass (fixed scorer) | Mean latency | Mean cost (est.) | Status |
|---|---|---|---|---|
| zai-glm | **66%** | 29 s | $0.0014 | real baseline (6 timeouts + 1 `NoClassDefFoundError`) |
| hf-llama | **81%** | 8.8 s | $0.0002 | real baseline (59/59 produced output) |
| gemini | — | — | — | all 59 returned HTTP **429** — never measured, re-run pending |

**Scorer bug, fixed.** `_positive_presence` matched concept words inside
negations (*"No public evidence of Jetpack Compose"* → pass). Made negation-
aware: a match preceded by "no evidence of…" no longer counts. Re-scored from
raw JSON: hf-llama 94%→81% (38 false passes removed), zai-glm 69%→66% (9
removed). The bug punished the more-honest model hardest — llama's frequent
"no public evidence of…" declines were counted as affirmations. Tests added
(`test_positive_presence_fails_when_mentioned_only_in_a_negation`,
`test_positive_presence_passes_when_mentioned_both_affirmed_and_negated`);
suite is 31 green.

The `scorecard.md` gemini "23%" is a separate artifact — rule scorers matching
incidental tokens in 429 error text against a provider that produced no output.

## What's not done (human gates)

1. **Complete the matrix.** Scorer is fixed and zai-glm (66%) + hf-llama (81%)
   are re-scored. Remaining: re-run gemini under a non-rate-limited key (its
   first run hit HTTP 429 on all 59 cases), then merge all three providers into
   one multi-provider `scorecard.json` — the runner currently overwrites the
   file on each invocation, so a merge mode (or a per-provider split + combine
   script) is needed for a single file of record.
2. **M1 of the z.ai/GLM addendum (chess-server `LlmComposer`).** Blocked: the
   prerequisite plan `opening-explainer-cloud-route.md` has not been run — none
   of the five local chess repos has the `:server` module, `LlmComposer`, or
   `TemplateComposer`. Recorded in `docs/plans/zai-glm-provider-addendum.md`.

## What's explicitly estimated, not measured

- **Cost** in the scorecard is labeled `est.` The Kotlin providers don't yet
  parse `usage` from the provider response, so token counts are estimated
  (chars ÷ 4) from the recorded per-token pricing in `pricing.json`. When
  `usage` flows through `CompletionResult → SkillResult → InvokeResponse`, the
  same formula computes real cost. **Latency is already real** (wall-clock in
  the Python adapter).

## Honest-failure behavior (verified)

- `run_scorecard.py --all-providers` with no ferry running and no API key raises
  a clear `RuntimeError` naming both missing channels. It does **not** fabricate
  results.
- `ferry run hello-repo` with no `ZAI_API_KEY` returns "No provider available
  for skill 'hello-repo'" — correct, not a crash.
- A GLM judge evaluating a GLM-produced output raises `JudgeFamilyConflict` and
  is recorded as a skip in the scorecard.

## Verification commands

```bash
python -m pytest eval_harness/ -q      # 31 passed
./gradlew build                         # BUILD SUCCESSFUL (28 tests, ktlint, detekt)
ferry providers list                    # zai-glm, gemini, hf-llama as JSON
ferry skills list                       # hello-repo, company-role-research
```

## Dependency versions (all verified against primary sources)

| Dependency | Version | Source |
|---|---|---|
| Kotlin | 2.3.21 | Maven Central (matches MCP SDK 0.14.0 build target) |
| Gradle | 9.6.1 | services.gradle.org |
| MCP Kotlin SDK | 0.14.0 | GitHub releases (2026-06-30) |
| Ktor | 3.4.3 | Maven Central (matches SDK's bundled version) |
| kotlinx-io | 0.9.1 | Maven Central |
| clikt | 5.0.3 | Maven Central |
| detekt | 1.23.8 | Maven Central |
| ktlint-gradle | 14.2.0 | GitHub releases |
| z.ai GLM model | glm-5.2 | docs.z.ai |
| koog (reference only) | 1.0.0 | GitHub releases (not depended on) |

## Naming

Public name: **ferryman** (binary `ferry`). "ferryman" is the internal working
title only — it collides with a ~381k-star AI agent (`openclaw.ai`) and a Captain
Claw game reimplementation. See `AGENTS.md` → Naming.
