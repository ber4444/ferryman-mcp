# Project Status

Snapshot as of 2026-07-13. This file is the single source of truth for where
the ferryman project stands against its three plans. Update it when a milestone
lands or a gate is crossed.

## At a glance

| Layer | State | Tests |
|---|---|---|
| ferryman (Kotlin host + CLI + HTTP) | MVP built, CI green | 28 Kotlin tests |
| Eval harness (Python) | Scaffolded, rule + judge scorers wired | 35 Python tests |
| Real scorecard with live provider numbers | **Done** — hf-llama 80%, zai-glm 74%; gemini endpoint was 503 during the run | 177 rows |

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

## Scorecard run (2026-07-16) — full matrix

The full 177-row matrix (59 cases × 3 providers) completed. Two providers have
real baselines; gemini's endpoint was unavailable during the run:

- **Golden-set sign-off** — `eval_harness/golden/approval.json` reads
  `goldenSetApproved: true` (approved 2026-07-15 by `repo-owner`). The approved
  set is **59 cases** (58 real companies + the "Acme Holdings" negative).
- **API keys** — `ZAI_API_KEY` and `DEEPINFRA_API_KEY` were available; gemini's
  endpoint returned 503 (Service Unavailable) for the run.

| Provider | Rule pass | Output / errors | Mean latency | Mean cost (est.) |
|---|---|---|---|---|
| hf-llama | **80%** | 59/59 output, 0 errors | 8.4 s | $0.0001 |
| zai-glm | **74%** | 59/59 output, 0 errors | 19.8 s | $0.0014 |
| gemini | — | 0/59 output — 51× HTTP 503 + 8× timeout | 39.8 s | $0.0000 |

**Why this run completed when prior ones didn't.** Robustness fixes landed on
this branch: the Kotlin provider now retries timeouts/IO (not just 429/503) and
the CLI exits cleanly on terminal failure; the Python runner isolates each case
(an exception becomes an error row, not a batch abort), writes the scorecard
incrementally after each provider, and runs providers in order llama→zai→gemini
so the reliable ones land first. Those fixes are why the 118 clean hf-llama +
zai-glm rows survived gemini's 503s — last run a single gemini timeout at case
43 aborted everything and saved nothing.

**zai-glm went from 7 errors to 0** between the 07-15 and 07-16 runs — the
timeout-retry fix converted what used to be fatal timeouts into retried calls.

**Scorer bug, fixed earlier.** `_positive_presence` matched concept words inside
negations (*"No public evidence of Jetpack Compose"* → pass). Made negation-
aware; the bug inflated every provider whose output honestly declines. Tests
added; suite is 35 green.

## What's not done (human gates)

1. **Re-run gemini when its endpoint is healthy.** The 2026-07-16 run hit HTTP
   503 on 51/59 gemini cases and timed out on 8 — zero research output, so
   gemini isn't scored. hf-llama (80%) and zai-glm (74%) are the two locked-in
   baselines; gemini is the missing third. This is an endpoint-availability
   issue, not a model-quality result.
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
