# The same skill on two agent hosts: hand-rolled vs Koog

`company-role-research` runs on two engines in one binary:

```bash
ferry run --skill company-role-research --engine ferryman --input '…'   # hand-rolled (default)
ferry run --skill company-role-research --engine koog     --input '…'   # JetBrains Koog 1.3.0
```

and the eval harness scores both on the same golden set:

```bash
python eval_harness/head_to_head.py --all-providers     # live run, needs provider keys
python eval_harness/wire_diff.py                        # no keys, no internet, ~8 s
```

**Status (2026-10-18):** the Koog engine is built and tested, and the harness runs
it end to end. **The live head-to-head on the three providers has not run yet.**
It needs `ZAI_API_KEY`, `GEMINI_API_KEY` and `DEEPINFRA_API_KEY` plus open internet
for the fetch tool. No score below comes from a model. The wire-level and overhead
numbers are measured; the model-quality comparison is pending.

## What is held equal

A scorecard difference should come from the framework, not from a different
experiment. Both engines get:

| | Both engines |
|---|---|
| Prompt | The `SKILL.md` body as the system message and the caller's JSON as the user message. Byte-identical on the wire (checked by `wire_diff.py`). |
| Model and endpoint | The same `config.toml` entry and provider selection (override → skill hint → default). Koog talks to it through `OpenAILLMClient` with the base URL overridden. |
| Tools | The same `.mcp.json` server processes, spawned by the host's `resolveTransport`. |
| Budget | At most 4 model calls per run, 2048 max output tokens, provider-default temperature. |
| Retries | 3 attempts, 5 s then 10 s backoff, on 429/503 and timeouts. Koog gets this via `RetryingLLMClient`. |
| Scoring | The same rule scorers and judge. Each case runs on both engines back to back, alternating which goes first. |

What Koog owns: the agent loop (a strategy graph on Koog's runtime), the wire
format, the MCP client and how tool results reach the model, and the event hooks
that report tokens and tool calls.

```
start → callLLM ─tool calls→ executeTools ─budget left→ sendToolResults ─tool calls→ executeTools
           │                      └─budget spent→ finish("Reached tool-call limit (4) …")
           └─text→ finish                                 sendToolResults ─text→ finish
```

## What the wire diff found (measured, no model involved)

`wire_diff.py` runs one case per engine against a local scripted model and a
local company page, through the real fetch and filesystem MCP servers.

| What the model receives | ferryman (hand-rolled) | koog |
|---|---|---|
| System prompt / user message | identical | identical |
| Output cap | `max_tokens: 2048` | `max_completion_tokens: 2048`, `stream: false` |
| Request size, first call | 12,848 bytes | 11,924 bytes (−7%) |
| Fetch tool name | `fetch_fetch` (namespaced `server_tool`) | `fetch` (no namespacing) |
| Fetch tool schema | 20 JSON-schema keywords | 8: drops `default`, `minimum`/`maximum`, `format: uri`, `minLength`, `title` |
| Assistant tool-call turn echoed back | the model's raw tool call, verbatim | rebuilt from Koog's message model; `content` key omitted, arguments re-serialized |
| Gemini `thought_signature` on the echoed call | **kept** | **dropped** |
| Tool result | the page as plain markdown (416 chars) | `{"content":[{"text":"…"}]…}`, JSON-escaped (488 chars, +17%) |
| Tool error | the server's text | the same text prefixed `Error: ` |

What each line could do to a live score:

- **`thought_signature` (Gemini).** The hand-rolled provider hit this in July:
  Gemini 3.x needs the signature echoed back on tool calls (commit `5cec7b5`).
  Koog's OpenAI-compatible client rebuilds the tool call and loses the field. The
  prediction is that the second model call fails on most Gemini rows under Koog.
  That's not verified, because no Gemini key was available. If it holds, the
  Koog-native fix is `GoogleLLMClient`, which handles signatures but means a
  different endpoint than the hand-rolled engine uses.
- **Schema loss.** The model no longer learns that `max_length` defaults to 5000
  or that `url` must be a URI. Probably harmless here, since the skill fetches
  once with just a URL, but it is information the MCP server published and Koog
  discarded.
- **JSON-wrapped tool results.** The same facts, escaped and wrapped. That costs
  more tokens per fetched page, and plausibly a little readability for smaller
  models.
- **`max_completion_tokens`.** OpenAI's newer field. Whether z.ai, DeepInfra and
  Gemini's compatibility layer honour it is up to each provider; one that ignores
  it runs uncapped.

## Overhead (measured)

With an instant local model, so the wall-clock is all framework, JVM start and
MCP server spawn: over 15 alternating pairs, the median run was 3.65 s
hand-rolled vs 3.77 s Koog (+0.13 s, Koog slower in 12/15 pairs). Against real
provider latency (6–18 s per run in the July scorecard) that is noise.

| | hand-rolled | Koog |
|---|---|---|
| Code for loop + wire client + MCP glue (non-blank, non-comment lines) | ~800 (orchestrator, OpenAI-compatible provider, provider types, MCP host) | ~400 (`koog/`, reusing the host's config, skills and process spawning) |
| Distribution (`build/install/ferry/lib`) | 50 jars, 19.1 MB | 87 jars, 28.4 MB (+37 jars, +9.3 MB) |

## Koog 1.3.0 notes from the rebuild

- **A retry bug.** `RetryConfig(jitterFactor = 0.0)` passes Koog's own
  validation, then the first retry throws `Random range is empty`
  (`RetryingLLMClient` calls `Random.nextDouble(0.0, delay * jitter)`). A unit
  test caught it; the engine uses 1% jitter.
- **MCP is beta.** `agents-mcp` (and the Agent Skills module) ship only as
  `1.3.0-beta`, outside Koog's 1.x stability guarantee. `agents-mcp` is built
  against MCP Kotlin SDK 0.11.1; this project resolves it to 0.14.0, and
  `KoogMcpBridgeTest` drives an in-process MCP server through it to show it
  still links. It also pulls in every Koog LLM client, Bedrock and the AWS SDK
  included; the build excludes that.
- **Base URL override works.** The repo-creation plan couldn't confirm Koog
  allowed an arbitrary OpenAI-compatible base URL. It does:
  `OpenAIClientSettings(baseUrl, chatCompletionsPath = "chat/completions")`
  reaches `<baseUrl>/chat/completions`. The model descriptor needs
  `LLMCapability.OpenAIEndpoint.Completions`, or Koog can't decide between Chat
  Completions and the Responses API.
- **No tool namespacing.** Tools from several MCP servers share one flat
  namespace. A custom descriptor parser can't add a prefix, because `McpTool`
  calls the server with the descriptor's name.

## Running the live head-to-head

```bash
./gradlew installDist
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,judge]'
export ZAI_API_KEY=… GEMINI_API_KEY=… DEEPINFRA_API_KEY=…
.venv/bin/python eval_harness/head_to_head.py --provider gemini --limit 3   # smoke: does Gemini survive on Koog?
.venv/bin/python eval_harness/head_to_head.py --all-providers               # 48 cases × 3 providers × 2 engines
```

It writes `eval_harness/head_to_head/{ferryman,koog}.json` and `report.md`:
per-provider rule pass rate, errors, latency, cost, tool discipline (did the
agent fetch exactly once, as the skill asks), per-claim pass rates, and a paired
per-case comparison with an exact sign test. It never overwrites the July
`scorecard.*`. `run_scorecard.py --engine koog` writes `scorecard-koog.*`.
