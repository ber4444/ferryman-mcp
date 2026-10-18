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
ferry serve --port 8080         # HTTP channel sharing the same orchestrator
```

Each skill under `ferryman/skills/` takes its own input shape — see its
`SKILL.md`:

```bash
ferry run hello-repo --input "summarize this repo"
ferry run company-role-research \
  --input '{"company":"EarnIn","role":"Senior Mobile Engineer (Android)"}'
ferry run chess-opening-coach \
  --input '{"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","question":"Find the best move for the side to move."}'
```

The Python eval harness has its own test suite, which `./gradlew build` does
**not** run. Run it after touching `eval_harness/` or any `SKILL.md`:

```bash
pip install -e '.[dev]'                     # once; add ,chess for python-chess scorers
python -m pytest eval_harness/ -q           # rule scorers — no API key needed
python -m eval_harness.run_scorecard        # regenerate scorecard (needs a running ferry)
```

CI: `.github/workflows/ci.yml` runs `./gradlew build` on push/PR to `main`;
`.github/workflows/eval-harness.yml` runs the pytest suite when
`ferryman/skills/**` or `eval_harness/**` change.

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
