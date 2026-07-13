# Research findings + Plan: OpenClaw repo creation

## Research findings (evidence summary)

**Kotlin vs TypeScript for the MCP host — build in Kotlin.** The official MCP Kotlin SDK (`io.modelcontextprotocol:kotlin-sdk`, maintained with JetBrains) supports building MCP *clients/hosts*, not just servers: the client module is `kotlin-sdk-client` and ships `StdioClientTransport` and `StreamableHttpClientTransport` (plus SSE and WebSocket). The SDK is still pre-1.0 and its schema packages have been reorganised repeatedly, and its published version is genuinely ambiguous across sources (tags as low as `0.7.7`/`0.8.0` are visible on some mirrors, while release trackers show a `0.13.0`-era line in mid-2026) — so the plan treats the exact version as a **must-verify-at-execution** item and instructs the agent to read Maven Central first. New contributions are Apache-2.0 licensed (confirmed by the SDK changelog, PR #481, "Update licensing to Apache 2.0 for new contributions"). Most prominent open-source MCP hosts/gateways are *not* Kotlin — they are TypeScript (MetaMCP, mcp-use, LibreChat), Go (Bifrost, Docker MCP Gateway, mcpjungle), Rust (agentgateway), or Python (IBM ContextForge). There is no well-known Kotlin MCP host to reference. For a portfolio piece marketing a "staff mobile engineer who ships AI products," that absence is a feature: Kotlin plays to Gabor's expertise and differentiates the repo. Minimal host requirements from the current released spec (2025-11-25) are modest: initialize handshake, `tools/list` discovery, cross-server tool aggregation, `tools/call`. Sampling, roots, and logging are being deprecated in the 2026-07-28 release candidate (SEP-2577), so the MVP must not build on them.

**koog vs a thin in-repo provider abstraction — build a thin abstraction, cite koog as the graduation path.** JetBrains' koog (`ai.koog:koog-agents`) shipped **1.0.0 at KotlinConf 2026 (May 2026)** with a one-year no-breaking-changes guarantee for stable modules, is Apache-2.0, and offers multi-provider clients (OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Ollama, AWS Bedrock) plus built-in MCP integration. But the portfolio's core value proposition *is* "I architected multi-provider routing," and the sibling eval-harness plan needs provider config that is trivially readable and enumerable. Wrapping koog would obscure the story and add a heavy dependency to a 1–2 week artifact. A thin abstraction — one interface, two implementations (`AnthropicProvider` + `OpenAiCompatibleProvider`) — is more legible, keeps routing config flat and enumerable, and cleanly covers z.ai GLM now and Ollama/OpenRouter/Together/Fireworks later (all OpenAI-compatible). z.ai's GLM Coding endpoint is OpenAI-compatible at `https://api.z.ai/api/coding/paas/v4`; after Gabor's GLM credits expire July 19, 2026, the same `OpenAiCompatibleProvider` repoints at a local Ollama/vLLM URL or a hosted endpoint with a config-only change. koog's arbitrary OpenAI-compatible base-URL override could not be confirmed from primary docs, a second reason not to depend on it yet.

**Skills format — adopt the Agent Skills / `SKILL.md` open standard, kept distinct from `AGENTS.md`.** Anthropic published Agent Skills as an open standard on **December 18, 2025** (spec at agentskills.io): a skill is a directory containing a `SKILL.md` whose YAML frontmatter requires `name` and `description`, followed by a Markdown body. It is agent-agnostic (Gabor's stated preference), Claude-Code-compatible, and machine-enumerable — scan `skills/*/SKILL.md`, parse frontmatter. This is a different concern from `AGENTS.md`, which guides agents working *on* the repo (contributed to the Linux Foundation's Agentic AI Foundation, announced December 9, 2025, alongside MCP and Goose). The plan uses both.

**Naming collision — severe. Rename before the first public commit.** "OpenClaw" is Peter Steinberger's local-first personal AI agent (openclaw.ai). Its scale is decisive: Steinberger's own GitHub profile lists it as "🦞 OpenClaw (381k+ stars) — the AI that actually does things," and its homepage cites Y Combinator calling it "the most-starred software repo on GitHub in under 5 months, with 346k+ stars." It occupies almost exactly Gabor's stated problem space: a local-first gateway, model-agnostic provider routing, `SKILL.md` skills, multi-channel I/O (Telegram/Slack/WhatsApp), and MCP integration. A separate well-known project, `pjasicek/OpenClaw`, is a Captain Claw game reimplementation. Shipping a portfolio piece called "OpenClaw" that is an MCP host with skills and multi-channel I/O would read as derivative, be un-Googleable, and invite direct comparison with a ~381k-star project. Keep "OpenClaw" only as the internal working title; pick a distinct public name before the repo goes public.

**Public vs private — public from day one, scaffolding-first, but don't link it from the résumé until a skill runs end to end.** Hiring managers reward visible momentum, green CI from the first commit, and an honest history. The real risk is a résumé linking to an empty or broken repo. The fix is sequencing, not secrecy: land green CI and an honest README in M0, and gate the résumé link on M3 (a skill executing end to end).

**Version/coordinate summary the plan cites (all verify-at-execution):** MCP Kotlin SDK `io.modelcontextprotocol:kotlin-sdk` / `:kotlin-sdk-client` — version ambiguous across sources (`0.7.7`/`0.8.0` visible on some mirrors, `0.13.0`-era on trackers), Apache-2.0 for new contributions; koog `ai.koog:koog-agents` = `1.0.0` (May 2026), Apache-2.0; detekt stable `1.23.x` line (2.0.0 is alpha, built on Kotlin 2.4); ktlint via `org.jlleitschuh.gradle.ktlint`; GitHub Actions `actions/checkout@v4`, `actions/setup-java@v4` (Temurin JDK 21), `gradle/actions/setup-gradle@v4` (pin by SHA); config format TOML (Python `tomllib` reads it); MCP spec current released `2025-11-25`, release candidate `2026-07-28`.

---

# Plan: OpenClaw (working title — rename before publish)

**Target:** a new GitHub repository, `github.com/<owner>/<renamed-openclaw>` — does not exist yet. No repo, no code.

## Context for the agent

This plan creates the repository for a local-first orchestration layer above Claude Code: an **MCP host** with pluggable **skills**, **multi-provider routing**, and **multi-channel I/O**. It sits one level above Claude Code by being the gateway, not the IDE. It is used for AI-assisted coding workflows — not on-device Android AI.

Two sibling plans depend on what this plan builds:

- `openclaw-eval-harness-plan.md` runs **after** this repo exists. It assumes skills are locatable, provider-routing config is readable and enumerable, there is a programmatic entry point to invoke a skill, and a Python `eval_harness/` package will be layered on top. This plan satisfies that contract and names exactly where each assumption lands (see **Hard rules** and **M2/M3**).
- A z.ai/GLM provider addendum plan depends on the provider abstraction from **M2** already existing. GLM is not special-cased in code; it is an `OpenAiCompatibleProvider` entry in config.

The MVP is ruthlessly small — a Kotlin CLI application, 1–2 weeks of work, not a product. Do not rebuild OpenClaw-the-famous-agent. Build a small, honest, well-architected gateway that demonstrates the four capabilities end to end.

**Language decision (already made, do not relitigate):** Kotlin, because the official MCP Kotlin SDK supports building clients/hosts and Kotlin is the portfolio's whole point. If the SDK proves unusable at execution time, stop and escalate to the human rather than silently switching to TypeScript.

## Hard rules

- **Never claim a feature that is not built.** The README and any article describe only what has landed on `main` with green CI. Every capability in the README maps to a runnable command in the **Success command** section. A "roadmap" section may list intent, clearly marked as not-yet-built.
- **Never keep the name "OpenClaw" in anything public.** It collides with a ~381k-star AI agent (openclaw.ai) and a well-known game reimplementation. "OpenClaw" survives only as the internal working title in planning docs. The public name is chosen in M0 before the first push. This is a blocking gate.
- **Never put secrets in the repo.** API keys live in environment variables only. Config files reference env var *names*, never values. `.gitignore` excludes `.env`, and a committed `.env.example` documents required variables with placeholders.
- **Never let a milestone land red.** Every milestone ends with green CI on `main`. If CI cannot be made green, the milestone is not done.
- **Never invent an agent-specific skill format.** Skills use the Agent Skills open standard: a directory `skills/<name>/` containing a `SKILL.md` with YAML frontmatter (`name`, `description` required). Agent-agnostic, Claude-Code-compatible, machine-enumerable. This is the eval-harness contract for skill discovery.
- **Never break the eval-harness compatibility contract.** Three things must stay true and stable: (1) skills are enumerable by scanning `skills/*/SKILL.md`; (2) provider-routing config is a readable TOML file plus a `providers list` CLI subcommand that prints providers as JSON; (3) there is a programmatic entry point — `Orchestrator.runSkill(name, input): SkillResult` — reachable from the CLI. Structured routing logs are written as JSON Lines to a known path for the harness to read.
- **Never trust this plan's version numbers over reality.** Before adding any dependency, inspect the current published version on Maven Central / GitHub releases. The MCP Kotlin SDK is pre-1.0, churns, and its version is reported inconsistently across sources — re-verify it, do not paste a number from this plan.
- **Never scope-creep past the gateway.** No GUI, no auth/OAuth broker, no multi-user, no on-device AI, no second LLM feature the eval harness does not need. Gateway, not IDE.

## Success command

By the end of M4, this sequence runs clean from a fresh clone (with `ANTHROPIC_API_KEY` and `ZAI_API_KEY` exported):

```bash
git clone <repo> && cd <repo>
./gradlew build                      # compiles, lints, tests — all green
./gradlew installDist                # produces the CLI launcher
./build/install/<app>/bin/<app> providers list        # prints ≥2 providers as JSON (anthropic + zai-glm)
./build/install/<app>/bin/<app> skills list           # prints skills discovered under skills/
./build/install/<app>/bin/<app> tools list            # prints aggregated tools from configured MCP servers
./build/install/<app>/bin/<app> run hello-repo --input "summarize this repo"   # runs a skill end to end
tail -n 1 logs/routing.jsonl         # shows a structured routing decision (skill, provider, model, tool calls)
# second channel:
./build/install/<app>/bin/<app> serve --port 8080 &
curl -s -X POST localhost:8080/invoke -d '{"skill":"hello-repo","input":"summarize this repo"}'   # same orchestrator, HTTP channel
```

## M0 — Naming, publish decision, and scaffolding

**Human steps (agents must not do these):**
1. **Pick the public name.** "OpenClaw" is out. Run coordinate checks — GitHub org/repo availability, Maven `groupId`, npm, PyPI, and a plain web search — before committing. Candidate working names to check (pick or replace): **`skald`** (a Norse court poet who orchestrates and recites — evokes orchestration), **`quartermaster`** / short `qm` (dispenses tools and provisions to a crew — maps to tool aggregation + provider routing), **`ferryman`** / `ferry` (the gateway/crossing metaphor — "the gateway, not the IDE"). Default: keep the working title only in planning docs; the repo, package namespace, and README use the chosen name.
2. **Create the empty GitHub repository** under the chosen name. Public. Add a description and topics (`mcp`, `mcp-host`, `kotlin`, `ai-agents`, `claude-code`). Enable branch protection requiring green CI on `main`.
3. **Provision nothing else yet** — no API keys needed until M2.

**Agent steps:**
- Scaffold a single-module Gradle project with Kotlin DSL (`build.gradle.kts`, `settings.gradle.kts` with an explicit `rootProject.name`), Gradle wrapper committed, JDK 21 (Temurin) toolchain. Use the Kotlin `application` plugin so `installDist` produces a launcher.
- Package layout (single module, clear packages — do not over-modularise): `host/` (MCP host), `providers/` (abstraction + implementations), `skills/` (SKILL.md loader), `orchestrator/` (`runSkill` entry point), `channels/` (CLI + HTTP), `config/` (TOML loader), `logging/` (structured routing logger).
- Add a CLI entry point (use `clikt` or `kotlinx-cli`) with stub subcommands: `providers`, `skills`, `tools`, `run`, `serve`. Unimplemented paths print "not implemented yet" and exit non-zero — honest stubs, not fake success.
- Add `LICENSE` — **Apache-2.0** (matches the Kotlin/JetBrains ecosystem: koog is Apache-2.0 and the MCP Kotlin SDK licenses new contributions under Apache-2.0 per its own changelog; Apache-2.0 adds a patent grant over MIT, signalling professionalism).
- Add `README.md`: title, one-line positioning, badges (CI status, license, Kotlin version), a **feature status table** (each row: capability → status `planned`/`building`/`done` → the command that proves it), a Quickstart, and an **Architecture** section with a placeholder Mermaid diagram (host → providers → skills → channels). No feature is marked `done` until its command works.
- Add `AGENTS.md` at repo root (build/test commands, package map, code-style rules, "never touch" boundaries, secrets-in-env rule), kept under ~150 lines. Add a thin `CLAUDE.md` whose first line is `@AGENTS.md` so Claude Code inherits it.
- Add `.gitignore` (Gradle, IntelliJ, `.env`, `build/`, `logs/`), `.env.example`, and a checked-in `.mcp.json` describing the MCP servers the host will connect to (start with one stdio server, e.g. a filesystem server) — this doubles as Claude Code's own MCP config.
- Add linting: `org.jlleitschuh.gradle.ktlint` for formatting and `detekt` (stable `1.23.x` line — **verify current; 2.0.0 is alpha**) with `detekt-formatting`. Wire both into `./gradlew build`.
- Adopt Conventional Commits (recommended — it signals discipline and feeds a future changelog); document the convention in `AGENTS.md`.
- Add GitHub Actions CI (`.github/workflows/ci.yml`): `actions/checkout@v4`, `actions/setup-java@v4` (Temurin 21), `gradle/actions/setup-gradle@v4` (pin by SHA; **verify latest v4.x at execution — a v6 line may exist**), running `./gradlew build` on push and PR to `main`. Wrapper validation is automatic in setup-gradle v4+.

**Milestone exit:** repo is public, CI is green on `main`, README is honest with everything marked `planned`, `./gradlew build` passes locally and in CI. Do not link this repo from the résumé yet.

## M1 — MCP host: connect, aggregate tools

- Add `io.modelcontextprotocol:kotlin-sdk-client` (**verify version — inspect Maven Central first; sources disagree, do not hard-code a number from this plan**). Add a Ktor client engine only if Streamable HTTP is used; stdio needs no engine.
- Implement `host/McpHost`: read `.mcp.json`, and for each configured server open a `Client` over `StdioClientTransport` (launch the server as a subprocess), perform the initialize handshake, call `tools/list`, and aggregate tools across all servers into a namespaced registry (`<server>.<tool>`).
- Implement `tools list` — prints the aggregated tool registry (server, tool name, description) as JSON.
- MVP transport is **stdio at minimum**. Structure the transport behind a small interface so Streamable HTTP can be added later without touching the aggregation logic — but do not implement it now (scope discipline).
- Tests: a fake in-memory MCP server (the SDK's `ChannelTransport` / linked-pair test util) exercised by `McpHost` so `tools list` is unit-tested without spawning processes.

**Milestone exit:** `tools list` prints real aggregated tools from at least one stdio MCP server; CI green.

## M2 — Provider abstraction + routing config + structured logs

**Human step:** obtain and export API keys — `ANTHROPIC_API_KEY` and `ZAI_API_KEY`. Agents must never hard-code or commit these.

- Define `providers/LlmProvider`: a suspend interface with `suspend fun complete(request: CompletionRequest): CompletionResult`, exposing tool-calling (the host's aggregated tools go in, tool-call requests come back out).
- Implement `AnthropicProvider` (Claude via the Anthropic Messages API) and `OpenAiCompatibleProvider` (OpenAI Chat Completions shape, configurable `baseUrl` + `model`). Use the Ktor client plus `kotlinx.serialization`.
- Config: a single readable **TOML** file (`config.toml` or the app-named equivalent) with a `[providers.<id>]` table per provider — `type` (`anthropic` | `openai-compatible`), `baseUrl`, `model`, and `apiKeyEnv` (the env var *name*, never the key). Ship at least two entries: `anthropic` and `zai-glm` (`baseUrl = "https://api.z.ai/api/coding/paas/v4"`, `model = "<glm-coding-model-id>"` — **verify the current GLM model id against z.ai docs at execution; it could not be confirmed from primary docs during planning**, `apiKeyEnv = "ZAI_API_KEY"`). TOML is chosen so the Python eval harness reads it with standard-library `tomllib`.
- Note for the human: z.ai GLM credits expire July 19, 2026. After that, edit config only — repoint `zai-glm` (or add `local-ollama`) to an Ollama/vLLM URL or a hosted OpenAI-compatible endpoint (OpenRouter/Together/Fireworks). No code change required; that is the point of the abstraction.
- Implement `providers list` — enumerates configured providers as JSON (id, type, model, whether the referenced env var is set). This is the eval-harness enumeration contract.
- Implement `logging/RoutingLogger`: append one JSON object per routing decision to `logs/routing.jsonl` (timestamp, skill, provider id, model, tool calls made, token counts if available, latency ms, outcome). Use `kotlinx.serialization`; the eval harness reads this file.
- Reference note (do not implement): if the provider layer ever needs to grow beyond two implementations, `ai.koog:koog-agents` (1.0.0, Apache-2.0, one-year API stability guarantee) is the graduation path — it already abstracts OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Ollama, and Bedrock. For the MVP the thin layer wins on legibility and eval-friendliness.

**Milestone exit:** `providers list` prints ≥2 providers; a smoke test calls one provider and writes a routing log line; CI green (provider calls in CI are mocked — no live keys in CI).

## M3 — Skills: load, list, and run end to end

- Implement `skills/SkillLoader`: scan `skills/*/SKILL.md`, parse YAML frontmatter (`name`, `description`, optional `provider` hint), keep the Markdown body as the skill's instructions. Ship one real skill, `skills/hello-repo/SKILL.md`, that summarises the current repository using a filesystem tool.
- Implement `skills list` — prints discovered skills (name, description, path) as JSON.
- Implement `orchestrator/Orchestrator.runSkill(name, input): SkillResult` — the programmatic entry point the eval harness will call. It loads the skill, selects a provider (skill hint → config default), builds the prompt from the skill body + input, passes the host's aggregated tools, runs the completion loop (model → tool calls dispatched through `McpHost` → results fed back → final answer), and writes a routing log line.
- Wire `run <skill> --input "..."` to `Orchestrator.runSkill`.
- Tests: `runSkill` against a mocked provider and the in-memory MCP server, asserting the tool-call loop executes and a routing log line is produced.

**Milestone exit:** `run hello-repo --input "summarize this repo"` executes prompt + tool calls + result from the CLI; a routing log line lands; CI green. **This is the résumé-link gate — link the repo now.**

## M4 — Second channel: HTTP endpoint (makes "multi-channel" honest)

- Implement `channels/HttpChannel`: a minimal Ktor server (`serve --port <n>`) exposing `POST /invoke` that accepts `{"skill": "...", "input": "..."}` and calls the *same* `Orchestrator.runSkill`. Return the result as JSON.
- **Recommendation: HTTP, not Telegram.** An HTTP endpoint is a single Ktor route, needs no bot account or token (no human account step), and is testable with `curl` in the Success command. Telegram needs a BotFather token and polling/webhook infra — more effort, an external account, and untestable in CI. Note Telegram/Slack as future channels in the roadmap; do not build them.
- The CLI and HTTP channel share one orchestrator — demonstrate the "multi-channel" claim honestly by having both paths hit identical code.
- Tests: an integration test that boots the HTTP server, posts to `/invoke` with a mocked provider, and asserts a valid result.

**Milestone exit:** the full **Success command** sequence passes end to end; CI green.

## M5 — Publication gate and v0.1.0

- Fill in the README architecture diagram (real Mermaid, not placeholder). Flip feature-status rows to `done` only for capabilities with a passing command.
- Write the launch article/README narrative **only** about what shipped: MCP host aggregating tools, two-provider routing with structured logs, agent-agnostic skills, two channels. The roadmap section (eval harness, more channels, more providers, Streamable HTTP) is clearly marked not-yet-built.
- Tag `v0.1.0`. Reserve a top-level `eval_harness/` path in the repo layout for the sibling plan — do not create it here.

**Milestone exit:** README claims are all backed by commands; `v0.1.0` tagged; CI green.

## Verification matrix

| Check | Command |
|---|---|
| Compiles, lints, tests | `./gradlew build` |
| CI green on main | GitHub Actions status on `main` |
| No secrets committed | `git grep -nE "sk-|api[_-]?key" -- . ':!*.example'` returns nothing sensitive |
| Providers enumerable | `<app> providers list` prints ≥2 providers as JSON |
| Skills enumerable | `<app> skills list` scans `skills/*/SKILL.md` |
| Tools aggregated | `<app> tools list` prints tools from configured MCP servers |
| Skill runs end to end | `<app> run hello-repo --input "..."` |
| Routing logged | `tail -n1 logs/routing.jsonl` shows a decision object |
| Second channel works | `curl -X POST localhost:8080/invoke -d '{"skill":"hello-repo","input":"..."}'` |
| Name is clean | GitHub/Maven/npm/PyPI/web checks show no active collision |

## Out of scope

- On-device / mobile Android AI. This is a desktop AI-assisted-coding gateway.
- GUI, web dashboard, authentication/OAuth broker, multi-user, RBAC. Gateway, not platform.
- The Python `eval_harness/` package — sibling plan, layered on the contract this repo guarantees.
- Telegram/Slack/Discord channels — roadmap only.
- MCP sampling, roots, and elicitation — optional server-side features being deprecated in the 2026-07-28 spec release candidate (SEP-2577); the host MVP needs only tool discovery and tool calls.
- Streamable HTTP transport for the host — structure for it, but stdio is the MVP.

## Publication / article gate

No README section, badge, blog post, or résumé bullet may describe a capability before its verification-matrix command passes on `main`. The feature-status table is the single source of truth. When in doubt, mark it `building` and move on — an honest "not yet" is worth more to a hiring manager than a claim that fails on `git clone`.