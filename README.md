# ferryman

> A local-first MCP host with pluggable skills, multi-provider routing, and
> multi-channel I/O. The gateway, not the IDE.

[![CI](https://img.shields.io/badge/CI-todo-orange)](.github/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Kotlin](https://img.shields.io/badge/Kotlin-2.3.21-purple.svg)](https://kotlinlang.org)

ferryman sits one level above Claude Code: instead of being the IDE, it is the
gateway that connects MCP servers, routes requests across LLM providers, and
exposes skills (the [Agent Skills](https://agentskills.io) open standard) over
multiple channels (CLI and HTTP). It is a small, honest, well-architected
gateway — a portfolio piece, not a product.

## Feature status

Every row maps to a runnable command. Nothing is marked `done` until that
command passes on `main`.

| Capability | Status | Proof command |
|---|---|---|
| Build, lint, test | done | `./gradlew build` (28 Kotlin tests, ktlint, detekt) |
| CLI launcher | done | `./gradlew installDist` → `build/install/ferry/bin/ferry` |
| Provider routing (4 providers) | done | `ferry providers list` — anthropic, zai-glm, gemini, perplexity |
| Skills enumerable | done | `ferry skills list` — company-role-research, hello-repo |
| MCP host aggregates tools | done | `ferry tools list` — filesystem + fetch MCP servers |
| Skill runs end to end | building | `ferry run company-role-research --input '{"company":"...","role":"..."}'` (needs an API key) |
| HTTP channel | building | `ferry serve --port 8080` (needs an API key) |
| Routing logged | done | unit-tested; `logs/routing.jsonl` written by every `runSkill` call |
| Python eval harness | building | `python -m pytest eval_harness/ -q` (29 tests green; scorecard needs a live provider) |
| Multi-provider scorecard | building | `python eval_harness/run_scorecard.py --all-providers` (needs API keys + ferry binary) |

## Quickstart

```bash
git clone https://github.com/ber4444/ferryman-mcp && cd ferryman-mcp

# Build the host and CLI
./gradlew build
./gradlew installDist

# Set at least one provider key (all four are optional, but you need one to run a skill)
export ZAI_API_KEY=...        # z.ai GLM (default provider)
export GEMINI_API_KEY=...     # Google Gemini
export PERPLEXITY_API_KEY=... # Perplexity Sonar
export ANTHROPIC_API_KEY=...  # Anthropic Claude

# List configured providers and skills
./build/install/ferry/bin/ferry providers list
./build/install/ferry/bin/ferry skills list

# Run a skill (input is a JSON string)
./build/install/ferry/bin/ferry run company-role-research \
  --input '{"company":"EarnIn","role":"Senior Mobile Engineer (Android)"}'
```

## Running the eval harness

The harness scores the `company-role-research` skill against a 59-case golden set
(58 real companies + 1 fabricated "Acme Holdings" negative case). It supports two
invocation modes:

**Via subprocess (finds the Gradle-installed binary automatically):**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
python eval_harness/run_scorecard.py --all-providers
```

**Via HTTP (start the server first, then point the harness at it):**

```bash
# Terminal 1 — start the ferry HTTP channel
./build/install/ferry/bin/ferry serve --port 8080 &

# Terminal 2 — run the scorecard (auto-detects HTTP, falls back to subprocess)
python eval_harness/run_scorecard.py --all-providers
```

The scorecard writes `eval_harness/scorecard.md` (human-readable) and
`eval_harness/scorecard.json` (machine-readable) with real numbers from real
invocations — no fabricated results.

To run the judge layer (requires a separate `JUDGE_API_KEY`):

```bash
JUDGE_API_KEY=... python eval_harness/run_scorecard.py --all-providers --judge
```

### Troubleshooting

- **`McpException: Connection closed`** — an MCP server failed to start. The
  `fetch` server requires `mcp-server-fetch` installed in the `.venv`
  (`pip install -e .` pulls it in as a dependency). Run `ferry tools list` to
  verify both servers start cleanly.
- **`No provider available for skill`** — no API key is set for any provider.
  Export at least one of `ZAI_API_KEY`, `GEMINI_API_KEY`, `PERPLEXITY_API_KEY`,
  or `ANTHROPIC_API_KEY`, then check with `ferry providers list` (look for
  `"apiKeySet": true`).
- **`Neither HTTP channel nor ferry binary available`** — the harness couldn't
  find ferry. Either run `./gradlew installDist` first (the harness checks
  `build/install/ferry/bin/ferry` automatically), or set `FERRY_BINARY` to the
  full path.

## Architecture

```mermaid
flowchart LR
    CLI[CLI channel] --> Orchestrator
    HTTP[HTTP channel] --> Orchestrator
    Orchestrator -->|selects| Providers[4 providers]
    Orchestrator -->|dispatches tool calls| McpHost[MCP host]
    McpHost -->|stdio| FS[filesystem server]
    McpHost -->|stdio| Fetch[fetch server]
    Orchestrator -->|loads| Skills[skills/*/SKILL.md]
    Orchestrator -->|writes| Logs[routing.jsonl]
```

- **Channels** (`channels/`) — CLI and HTTP both call the same `Orchestrator`.
- **Orchestrator** (`orchestrator/`) — `runSkill(name, input)`: loads the skill,
  selects a provider, runs the model↔tool loop, writes a routing log line.
- **Providers** (`providers/`) — `LlmProvider` with `AnthropicProvider` and
  `OpenAiCompatibleProvider` (covers z.ai GLM, Gemini, Perplexity, OpenRouter,
  Ollama, vLLM, …). All four configured providers route through the same
  abstraction.
- **MCP host** (`host/`) — connects stdio servers, aggregates tools into a
  namespaced registry (`<server>.<tool>`). Two servers configured: filesystem
  (`@modelcontextprotocol/server-filesystem`) and fetch (`mcp-server-fetch`).
- **Skills** (`skills/`) — scans `skills/*/SKILL.md` (Agent Skills open
  standard). Two skills: `company-role-research` (eval harness target) and
  `hello-repo` (repo summarizer).
- **Config** (`config/`) — a single TOML file; the Python eval harness reads it
  with stdlib `tomllib` to enumerate providers for the `--all-providers` matrix.
- **Eval harness** (`eval_harness/`) — Python package with rule-based scorers
  (8 deterministic checks), an LLM-as-judge scorer (5-criterion rubric with
  family-exclusion), and a multi-provider scorecard runner. See
  `eval_harness/README.md` for details.

See `AGENTS.md` for the package map and contribution rules.

## Providers

| Provider | Model | Type | Pricing (per 1M tokens) |
|---|---|---|---|
| zai-glm (default) | glm-5.2 | openai-compatible | $1.40 in / $4.40 out |
| anthropic | claude-sonnet-4-5 | anthropic | $3.00 in / $15.00 out |
| gemini | gemini-2.5-flash | openai-compatible | $0.30 in / $2.50 out |
| perplexity | sonar | openai-compatible | $1.00 in / $1.00 out |

Adding an OpenAI-compatible provider is a config-only change — edit
`ferryman/config.toml`, no code needed.

## Roadmap (not yet built)

- First real multi-provider scorecard run (needs API keys exported — the harness
  and skill are ready, the scoring just hasn't been run against live providers).
- Streamable HTTP transport for the MCP host (stdio only for now).
- More channels: Telegram, Slack (HTTP is the MVP second channel).
- Real token-count propagation through providers for exact (non-estimated) cost.

## License

Apache-2.0. See [LICENSE](LICENSE).
