package dev.openclaw.ferryman.orchestrator

import dev.openclaw.ferryman.host.ConnectedHost
import dev.openclaw.ferryman.host.McpHost
import dev.openclaw.ferryman.logging.RoutingDecision
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.memory.MemoryStore
import dev.openclaw.ferryman.providers.CompletionRequest
import dev.openclaw.ferryman.providers.ConversationMessage
import dev.openclaw.ferryman.providers.LlmProvider
import dev.openclaw.ferryman.providers.ProviderRegistry
import dev.openclaw.ferryman.providers.ToolCall
import dev.openclaw.ferryman.providers.ToolDescriptor
import dev.openclaw.ferryman.providers.ToolResult
import dev.openclaw.ferryman.skills.SkillLoader
import java.time.Instant

/**
 * The end result of running a skill — what the eval harness and both channels
 * consume. [output] is the final answer text; [provider] and [model] record
 * where it came from for the routing log and scorecard.
 */
data class SkillResult(
    val output: String,
    val provider: String,
    val model: String,
    val toolCalls: List<String>,
    // Real token counts summed across all provider turns (the tool-call loop
    // can call the provider several times). Null when the provider returns no
    // usage data — the scorecard then falls back to a chars/4 estimate.
    val inputTokens: Int? = null,
    val outputTokens: Int? = null,
)

/**
 * Internal result of the model↔tool loop: the final answer text plus the real
 * token counts summed across every provider turn. Rolled into [SkillResult] by
 * [Orchestrator.runSkill].
 */
private data class LoopOutcome(
    val output: String,
    val inputTokens: Int?,
    val outputTokens: Int?,
)

/**
 * The programmatic entry point the eval harness and both channels call.
 *
 * Loads the skill, selects a provider (skill hint → config default), builds the
 * prompt from the skill body + input, passes the host's aggregated tools, runs
 * the completion loop (model → tool calls dispatched through the MCP host →
 * results fed back → final answer), and writes a routing log line.
 *
 * Bounded at [MAX_ITERATIONS] turns to guarantee termination even if a model
 * loops on tool calls.
 */
class Orchestrator(
    private val skills: SkillLoader,
    private val host: McpHost,
    private val providers: ProviderRegistry,
    private val logger: RoutingLogger,
    private val memory: MemoryStore? = null,
) {
    suspend fun runSkill(
        name: String,
        input: String,
        providerOverride: String? = null,
    ): SkillResult {
        val skill =
            skills.find(name)
                ?: return SkillResult(
                    output = "Skill '$name' not found. Run `ferry skills list` to see available skills.",
                    provider = "none",
                    model = "none",
                    toolCalls = emptyList(),
                )

        val providerId = providerOverride ?: skill.providerHint ?: providers.default()?.id
        val provider =
            providerId?.let { providers.get(it) }
                ?: return SkillResult(
                    output =
                        "No provider available for skill '$name'. " +
                            "Set \${apiKeyEnv} for provider '$providerId' (see `ferry providers list`).",
                    provider = providerId ?: "none",
                    model = "none",
                    toolCalls = emptyList(),
                )

        // Seed user preferences on first use so a fresh checkout immediately
        // carries the fit criteria without a manual step. Cheap + idempotent.
        memory?.ensureSeeded()

        // Prepend any remembered context to the skill body so the model sees
        // prior runs and the user's standing preferences. Always-on user
        // preferences + prior research for the company in the input, if any.
        val system = withMemoryContext(skill.body, input)

        val started = System.currentTimeMillis()
        val toolCallsMade = mutableListOf<String>()
        var outcome = "ok"
        var error: String? = null
        try {
            return ConnectedHost.use(host.connect()) { connected ->
                val outcome = runLoop(system, input, provider, connected, toolCallsMade)
                // Persist the result so the next run on this company can
                // cross-reference it. Keyed on the company name when one is
                // parseable from the input; falls back to the skill name.
                memory?.rememberResult(name, input, outcome.output)
                SkillResult(
                    output = outcome.output,
                    provider = provider.id,
                    model = provider.model,
                    toolCalls = toolCallsMade.toList(),
                    inputTokens = outcome.inputTokens,
                    outputTokens = outcome.outputTokens,
                )
            }
        } catch (e: Throwable) {
            outcome = "error"
            error = e.message
            throw e
        } finally {
            logger.log(
                RoutingDecision(
                    timestamp = Instant.now().toString(),
                    skill = name,
                    provider = provider.id,
                    model = provider.model,
                    toolCalls = toolCallsMade.toList(),
                    latencyMs = System.currentTimeMillis() - started,
                    outcome = outcome,
                    error = error,
                ),
            )
        }
    }

    /**
     * The model↔tool loop. Terminates when the model returns a final answer or
     * when [MAX_ITERATIONS] is reached. Returns the final answer text plus the
     * real token counts summed across every provider turn.
     */
    private suspend fun runLoop(
        system: String,
        input: String,
        provider: LlmProvider,
        connected: ConnectedHost,
        toolCallsMade: MutableList<String>,
    ): LoopOutcome {
        val tools =
            connected.tools.map {
                ToolDescriptor(
                    name = it.namespacedName,
                    description = it.description ?: "",
                    parametersJson = it.inputSchemaJson,
                )
            }
        val conversation = mutableListOf<ConversationMessage>()
        // Sum real token counts across turns. Stays null if the provider never
        // reports usage (e.g. Anthropic) so the scorecard can fall back.
        var inputTokens: Int? = null
        var outputTokens: Int? = null
        repeat(MAX_ITERATIONS) { iteration ->
            val response =
                provider.complete(
                    CompletionRequest(
                        system = system,
                        user = input,
                        tools = tools,
                        conversation = conversation.toList(),
                    ),
                )
            inputTokens = sumTokens(inputTokens, response.inputTokens)
            outputTokens = sumTokens(outputTokens, response.outputTokens)
            // Debug: log each turn to stderr so we can see what the model is doing.
            System.err.println(
                "[ferryman] iteration $iteration: ${response.toolCalls.size} tool calls, output=${response.output.take(100)}",
            )
            for (call in response.toolCalls) {
                System.err.println("[ferryman]   tool: ${call.name} args=${call.argumentsJson.take(200)}")
            }
            if (response.toolCalls.isEmpty()) {
                return LoopOutcome(response.output, inputTokens, outputTokens)
            }
            // Record the assistant's tool-call turn in the conversation history.
            conversation.add(
                ConversationMessage.AssistantToolCall(
                    toolCalls = response.toolCalls,
                ),
            )
            // Dispatch each requested tool call through the host and collect results.
            for (call in response.toolCalls) {
                val toolResult = dispatchToolCall(call, connected, toolCallsMade)
                System.err.println("[ferryman]   result: ${toolResult.content.length} chars, error=${toolResult.isError}")
                conversation.add(ConversationMessage.ToolResult(toolResult))
            }
        }
        return LoopOutcome(
            "Reached tool-call limit ($MAX_ITERATIONS) without a final answer.",
            inputTokens,
            outputTokens,
        )
    }

    /**
     * Accumulate per-turn token counts. Once any turn reports a count, the
     * running total becomes non-null; null turns contribute nothing.
     */
    private fun sumTokens(
        running: Int?,
        turn: Int?,
    ): Int? {
        if (running == null && turn == null) return null
        return (running ?: 0) + (turn ?: 0)
    }

    /**
     * Resolves one model-requested tool call against the aggregated registry and
     * returns the [ToolResult] to feed back to the model. Unknown tools and
     * missing connections become error results rather than skipping silently.
     */
    private suspend fun dispatchToolCall(
        call: ToolCall,
        connected: ConnectedHost,
        toolCallsMade: MutableList<String>,
    ): ToolResult {
        val (server, toolName) = parseNamespaced(call.name)
        val aggregateTool = connected.find(server, toolName)
        if (aggregateTool == null) {
            return ToolResult(
                callId = call.id,
                toolName = call.name,
                content = "Tool '${call.name}' not found in the registry.",
                isError = true,
            )
        }
        val connection =
            connected.connections.firstOrNull { it.name == server }
                ?: return ToolResult(
                    callId = call.id,
                    toolName = call.name,
                    content = "Server '$server' not connected.",
                    isError = true,
                )
        toolCallsMade.add(call.name)
        val result =
            connection.client.callTool(
                name = toolName,
                arguments = parseArguments(call.argumentsJson),
            )
        val content =
            result.content.joinToString("") {
                (it as? io.modelcontextprotocol.kotlin.sdk.types.TextContent)?.text ?: ""
            }
        return ToolResult(
            callId = call.id,
            toolName = call.name,
            content = content,
            isError = result.isError == true,
        )
    }

    private fun parseNamespaced(name: String): Pair<String, String> {
        val dot = name.indexOf('.')
        return if (dot < 0) "" to name else name.substring(0, dot) to name.substring(dot + 1)
    }

    private fun parseArguments(json: String): Map<String, Any?> {
        if (json.isBlank()) return emptyMap()
        val parsed =
            kotlinx.serialization.json.Json
                .parseToJsonElement(json)
        val obj = parsed as? kotlinx.serialization.json.JsonObject ?: return emptyMap()
        // callTool accepts Map<String, Any?>; JsonElement values are re-serialised
        // by the SDK on the wire, so passing them through as-is is fine.
        return obj.toMap()
    }

    /**
     * Builds the system prompt by prepending remembered context to the skill
     * body. Two layers:
     * 1. Standing user preferences (always, if any are stored).
     * 2. Prior research for the company named in the input, if any — so the
     *    skill can cross-reference and build on earlier runs rather than start
     *    from scratch.
     *
     * The skill body always comes last, unchanged. Returns [body] verbatim when
     * no memory store is configured or nothing is remembered.
     */
    private fun withMemoryContext(
        body: String,
        input: String,
    ): String {
        val store = memory ?: return body
        val sections = mutableListOf<String>()

        store
            .loadAll(MemoryStore.USER_PREFERENCES)
            .takeIf { it.isNotEmpty() }
            ?.let {
                sections += "## User preferences (remembered)"
                sections += it.joinToString("\n") { mem -> "- ${mem.key}: ${mem.content}" }
            }

        val company = parseCompany(input)
        if (company != null) {
            val prior =
                store
                    .loadAll(MemoryStore.COMPANY_RESEARCH)
                    .filter { it.key.equals(company, ignoreCase = true) }
            if (prior.isNotEmpty()) {
                sections += "## Prior research on $company (remembered — reference and build on this)"
                sections += prior.joinToString("\n\n") { mem -> mem.content }
            }
        }

        if (sections.isEmpty()) return body
        return sections.joinToString("\n\n") + "\n\n---\n\n" + body
    }

    /**
     * Parses a `{"company": "..."}` field from a JSON-ish input so the result
     * can be keyed on the company name. Returns null for unparseable input or a
     * missing company field — then we just don't persist under a company key.
     */
    private fun parseCompany(input: String): String? =
        try {
            val obj =
                kotlinx.serialization.json.Json
                    .parseToJsonElement(input) as? kotlinx.serialization.json.JsonObject
                    ?: return null
            (obj["company"] as? kotlinx.serialization.json.JsonPrimitive)?.content
        } catch (e: kotlinx.serialization.SerializationException) {
            null
        } catch (e: IllegalArgumentException) {
            null
        }

    /**
     * Remembers a skill result. Company-research skills are keyed on the company
     * name; anything else falls back to the skill name so the run is still
     * recoverable via `ferry memory list` / `search`.
     */
    private fun MemoryStore.rememberResult(
        skillName: String,
        input: String,
        output: String,
    ) {
        val key = parseCompany(input) ?: skillName
        save(MemoryStore.COMPANY_RESEARCH, key, output)
    }

    companion object {
        const val MAX_ITERATIONS = 4
    }
}
