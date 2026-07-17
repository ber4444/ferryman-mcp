# Project Status

Snapshot as of 2026-07-17. This file is the single source of truth for where
the ferryman project stands against its three plans. Update it when a milestone
lands or a gate is crossed.

## At a glance

| Layer | State | Tests |
|---|---|---|
| ferryman (Kotlin host + CLI + HTTP) | MVP built, CI green | 31 Kotlin tests |
| Eval harness (Python) | Scaffolded, rule + judge scorers wired | 74 Python tests |
| Real scorecard with live provider numbers | **Done** — 144 rows, all 3 providers scored: hf-llama 82%, gemini 76%, zai-glm 68% | 144 rows |

All commits are on `main`. This document is the living snapshot — update it in
place when a milestone lands; it no longer rides on a dedicated status branch.

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
  end-to-end; cost computed from `pricing.json` (glm-5-turbo $1.20/M in, $4.00/M out,
  verified 2026-07-16); per-provider aggregate row in the scorecard
- M3 — judge family-exclusion (GLM never grades GLM), `judge_variance()` consistency
  helper, 7 new tests
- M4 — `.github/workflows/eval-harness.yml` runs rule scorers on skill/harness
  changes

## Scorecard — full three-provider run (2026-07-16)

The full matrix completed: 144 rows, the 48-case golden set × 3 providers.
**All three providers produced real output with zero errors** — the first clean
three-provider run. Gemini scored at last, after settling on `gemini-3.1-flash-lite`
(`gemini-3.5-flash-lite` doesn't exist; `2.5-flash-lite` is deprecated; the prior
`3.5-flash` run hit 503s on 51/59 cases).

- **Golden-set sign-off** — `eval_harness/golden/approval.json` reads
  `goldenSetApproved: true` (approved 2026-07-15 by `repo-owner`).
- **Models** — zai-glm `glm-5-turbo`, gemini `gemini-3.1-flash-lite`, hf-llama
  `Meta-Llama-3.1-70B-Instruct-Turbo`. All three API keys available.

| Provider | Rule pass | Output / errors | Mean latency | Mean cost |
|---|---|---|---|---|
| hf-llama | **82%** | 48/48 output, 0 errors | 10.4 s | $0.0002 |
| gemini | **76%** | 48/48 output, 0 errors | 6.0 s | $0.0004 |
| zai-glm | **68%** | 48/48 output, 0 errors | 18.2 s | $0.0011 |

Three model families within ~14 points — the multi-provider routing claim is now
measured, not a config option. zai-glm dropped from 74% (under `glm-5.2`, prior
run) to 68% under the cheaper `glm-5-turbo`; hf-llama leads on quality at the
lowest cost. Cost is exact — this run used the token-propagation build (commit
`e507414`, the provider parses `usage` and threads real counts to
`estimate_cost`), so cost reflects actual usage rather than the chars÷4 fallback.

**How earlier runs failed and this one didn't.** Prior runs died on a single
gemini timeout and saved nothing; robustness fixes (per-case isolation,
incremental writes, provider ordering llama→zai→gemini, timeout retry) let this
run complete all 144 rows. The negation-aware scorer fix is applied.

## What's not done (human gates)

1. **M1 of the z.ai/GLM addendum (chess-server `LlmComposer`).** Blocked: the
   prerequisite plan `opening-explainer-cloud-route.md` has not been run — none
   of the five local chess repos has the `:server` module, `LlmComposer`, or
   `TemplateComposer`. Recorded in `docs/plans/zai-glm-provider-addendum.md`.

## What's explicitly estimated, not measured

- **Latency** is real — wall-clock of the whole skill invocation, measured in
  the Python adapter. **Cost is now exact too** (re-ran 2026-07-16 against the
  token-propagation build): the Kotlin provider parses `usage`, and counts flow
  through `CompletionResult → SkillResult → InvokeResponse` (HTTP) / a `_meta`
  JSON line (subprocess) into `estimate_cost`, which uses real tokens. Rows
  where a provider reports no `usage` still fall back to chars ÷ 4, but all
  three providers in this matrix report usage.

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
python -m pytest eval_harness/ -q      # 74 passed
./gradlew build                         # BUILD SUCCESSFUL (31 tests, ktlint, detekt)
ferry providers list                    # zai-glm, gemini, hf-llama as JSON
ferry skills list                       # hello-repo, company-role-research, chess-opening-coach
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
| z.ai GLM model | glm-5-turbo | docs.z.ai (was glm-5.2) |
| koog (reference only) | 1.0.0 | GitHub releases (not depended on) |

## Naming

Public name: **ferryman** (binary `ferry`). "ferryman" is the internal working
title only — it collides with a ~381k-star AI agent (`openclaw.ai`) and a Captain
Claw game reimplementation. See `AGENTS.md` → Naming.
