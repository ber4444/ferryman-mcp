package dev.openclaw.ferryman.orchestrator

import dev.openclaw.ferryman.host.ConnectedHost
import dev.openclaw.ferryman.host.McpHost
import dev.openclaw.ferryman.logging.RoutingDecision
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.providers.CompletionRequest
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

        val started = System.currentTimeMillis()
        val toolCallsMade = mutableListOf<String>()
        var outcome = "ok"
        var error: String? = null
        try {
            return ConnectedHost.use(host.connect()) { connected ->
                val output = runLoop(skill.body, input, provider, connected, toolCallsMade)
                SkillResult(
                    output = output,
                    provider = provider.id,
                    model = provider.model,
                    toolCalls = toolCallsMade.toList(),
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
     * when [MAX_ITERATIONS] is reached.
     */
    private suspend fun runLoop(
        system: String,
        input: String,
        provider: LlmProvider,
        connected: ConnectedHost,
        toolCallsMade: MutableList<String>,
    ): String {
        val tools = connected.tools.map { ToolDescriptor(it.namespacedName, it.description ?: "") }
        val priorResults = mutableListOf<ToolResult>()
        repeat(MAX_ITERATIONS) { iteration ->
            val response =
                provider.complete(
                    CompletionRequest(
                        system = system,
                        user = input,
                        tools = tools,
                        toolResults = priorResults.toList(),
                    ),
                )
            if (response.toolCalls.isEmpty()) {
                return response.output
            }
            // Dispatch each requested tool call through the host and collect results.
            for (call in response.toolCalls) {
                val toolResult = dispatchToolCall(call, connected, toolCallsMade)
                priorResults.add(toolResult)
            }
        }
        return "Reached tool-call limit ($MAX_ITERATIONS) without a final answer."
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

    companion object {
        const val MAX_ITERATIONS = 8
    }
}
