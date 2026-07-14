# Project Status

Snapshot as of 2026-07-13. This file is the single source of truth for where
the ferryman project stands against its three plans. Update it when a milestone
lands or a gate is crossed.

## At a glance

| Layer | State | Tests |
|---|---|---|
| ferryman (Kotlin host + CLI + HTTP) | MVP built, CI green | 28 Kotlin tests |
| Eval harness (Python) | Scaffolded, rule + judge scorers wired | 22 Python tests |
| Real scorecard with live provider numbers | **Not yet** — blocked on human gates | — |

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

## What's not done (human gates)

These are blocking and not mine to resolve. No scorecard with real numbers can
exist until they are crossed:

1. **Golden-set sign-off.** `eval_harness/golden/approval.json` has
   `goldenSetApproved: false`. Until a human reviews the 25 cases and flips it,
   every scorecard is provisional. This is the plan's "single most important
   quality control."
2. **API keys.** `ANTHROPIC_API_KEY` and `ZAI_API_KEY` are not set in this
   environment. Without them, `ferry run` / `ferry serve` cannot call a real
   provider, and `run_scorecard.py` cannot produce real numbers. `JUDGE_API_KEY`
   is likewise unset, blocking the judge layer.
3. **M1 of the z.ai/GLM addendum (chess-server `LlmComposer`).** Blocked: the
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
python -m pytest eval_harness/ -q      # 22 passed
./gradlew build                         # BUILD SUCCESSFUL (28 tests, ktlint, detekt)
ferry providers list                    # anthropic + zai-glm as JSON
ferry skills list                       # hello-repo
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

Public name: **ferryman** (binary `ferry`). "OpenClaw" is the internal working
title only — it collides with a ~381k-star AI agent (`openclaw.ai`) and a Captain
Claw game reimplementation. See `AGENTS.md` → Naming.
