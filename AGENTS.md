# AGENTS.md

Guidance for agents (human or AI) working **on** this repository. Distinct from
the skills under `ferryman/skills/`, which are instructions the running agent
executes — those are data, this file is build/contribution rules.

## Quick facts

- **Name:** ferryman (working title: ferryman — see Naming below). Binary: `ferry`.
- **What it is:** a local-first MCP host with pluggable skills, multi-provider
  routing, and multi-channel I/O. Sits one level above Claude Code — the gateway,
  not the IDE.
- **Stack:** Kotlin (JVM 21), Gradle 9.x, Ktor, kotlinx.serialization, the
  official MCP Kotlin SDK. Python for the eval harness (see `eval_harness/`).

## Build, test, run

```bash
./gradlew build                 # compile + ktlint + detekt + tests
./gradlew installDist           # produce the CLI launcher at build/install/ferry/bin/ferry
ferry providers list            # JSON enumeration of configured providers
ferry skills list               # JSON enumeration of discovered skills
ferry tools list                # aggregated MCP tools (stdio servers must be runnable)
ferry run hello-repo --input "summarize this repo"
ferry serve --port 8080         # HTTP channel sharing the same orchestrator
```

CI: `.github/workflows/ci.yml` runs `./gradlew build` on push/PR to `main`.

## Package map (`src/main/kotlin/dev/openclaw/ferryman/`)

| Package | Responsibility |
|---|---|
| `host/` | MCP host: read `.mcp.json`, connect stdio servers, aggregate tools |
| `providers/` | `LlmProvider` interface + `AnthropicProvider`, `OpenAiCompatibleProvider` |
| `skills/` | `SkillLoader` scanning `skills/*/SKILL.md` |
| `orchestrator/` | `Orchestrator.runSkill(name, input)` — the programmatic entry point |
| `channels/` | CLI (in `Main.kt`) + `HttpServer` — both call the same orchestrator |
| `config/` | TOML loader (`ferryman/config.toml`) |
| `logging/` | `RoutingLogger` → `logs/routing.jsonl` |

## Eval-harness compatibility contract (do not break)

Three things must stay stable so `eval_harness/` keeps working:

1. **Skills are enumerable** by scanning `ferryman/skills/*/SKILL.md`.
2. **Provider config is readable TOML** plus `ferry providers list` prints JSON.
3. **There is a programmatic entry point** — `Orchestrator.runSkill(name, input): SkillResult`.

Structured routing logs are JSON Lines at `logs/routing.jsonl`.

## Code style

- ktlint (Kotlin official style) and detekt run on `./gradlew build`. Detekt is
  relaxed from defaults — see `detekt.yml`. Don't loosen a rule to make a warning
  go away; fix the code.
- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).
- Match surrounding code's naming, density, and idiom.

## Never touch

- `ferryman/skills/*/SKILL.md` body edits without checking the eval-harness
  golden set still passes — these are the prompts being graded.
- The eval-harness contract above.
- Production routing/provider code beyond what an evaluation needs.

## Secrets

API keys live in **environment variables only** (`ANTHROPIC_API_KEY`,
`ZAI_API_KEY`). Config files reference env-var *names*, never values. `.env` is
gitignored. `git grep -nE "sk-|api[_-]?key" -- . ':!*.example'` should return
nothing sensitive.

## Cloud sandbox limits

Observed 2026-10-18 in a Claude Code cloud session (the one that built the
`nl2sql` skill). Keys and network access are settings of the cloud
environment, so they can change. Check before assuming either way.

- **There are no provider API keys in this sandbox, and Hugging Face and
  Ollama are blocked, so no local model either.** `ZAI_API_KEY`,
  `GEMINI_API_KEY`, `DEEPINFRA_API_KEY` and `JUDGE_API_KEY` are unset, so no
  skill can be scored against a real provider: the skill's output is just
  "No provider available". `huggingface.co` (and its mirrors and CDNs),
  `registry.ollama.ai` and `ollama.com` are denied, so there are no weights to
  serve locally. Don't commit scorecard numbers from this sandbox, and never
  substitute mocked numbers for real ones.
- **Benchmark data hosts are blocked too:** Google Drive (the original Spider
  `.sqlite` files and test suites), BIRD's site and download bucket, Kaggle,
  Zenodo. That is why the nl2sql databases come from InterCode's
  GitHub-hosted dump.
- **What works:** `git clone` from GitHub, `raw.githubusercontent.com`, GitHub
  release downloads, PyPI, npm, Maven Central. Maven Central sometimes answers
  429. Re-run `./gradlew build`; each attempt caches more dependencies.
- **A fresh container has no `.venv`**, so the `fetch` MCP server in
  `.mcp.json` (which runs `.venv/bin/python`) fails to start. Fix:
  `python3 -m venv .venv && .venv/bin/pip install -e '.[dev,chess]'`.
- **Checking plumbing without keys:** run `ferry` from a scratch directory
  whose `ferryman/config.toml` has a `[providers.mock]` entry pointing at a
  local mock of `POST /v1/chat/completions`. That tests wiring (skill loading,
  input, output parsing, scoring), not model quality, and its scores are not
  results.
- **To lift the limits** (repo owner): add the keys as environment variables
  or network secrets in the cloud environment's settings, and widen Network
  access or allow the needed domains
  (https://code.claude.com/docs/en/cloud-environments#network-access). A new
  session picks the change up.

## Naming

The public name is **ferryman** (binary `ferry`). "ferryman" survives only as
the internal working title in these planning docs — it collides with a ~381k-star
AI agent (openclaw.ai) and a game reimplementation. Never use "ferryman" in
anything user-facing: README, package namespace, CLI help, git remote name.

## Adding a provider

1. Add a `[providers.<id>]` table to `ferryman/config.toml` (`type`, `baseUrl`,
   `model`, `apiKeyEnv`).
2. If the type is new, implement it under `providers/` and add the branch to
   `LlmProviderFactory`. For OpenAI-compatible endpoints (Ollama, vLLM,
   OpenRouter, Together, Fireworks) step 1 alone is enough — no code change.
3. `ferry providers list` must show the new entry.
