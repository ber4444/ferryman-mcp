package dev.openclaw.ferryman.providers

import dev.openclaw.ferryman.config.ProviderConfig

/**
 * A request to an LLM. [system] + [user] are the prompt parts built by the
 * orchestrator from the skill body and the caller's input. [tools] is the host's
 * aggregated tool registry serialised as OpenAI-style function schemas so any
 * provider can read them. [conversation] carries the full multi-turn history
 * (assistant tool-call turns + tool results) so the provider can reconstruct
 * the correct message sequence for APIs that require it (OpenAI, Gemini).
 */
data class CompletionRequest(
    val system: String,
    val user: String,
    val tools: List<ToolDescriptor> = emptyList(),
    val conversation: List<ConversationMessage> = emptyList(),
)

/**
 * One turn in the conversation history. The orchestrator builds this as the
 * tool-call loop runs so providers can serialize the full message chain.
 */
sealed class ConversationMessage {
    /** An assistant turn that requested tool calls (no final text answer yet). */
    data class AssistantToolCall(
        val toolCalls: List<ToolCall>,
    ) : ConversationMessage()

    /** A tool result returned to the model after dispatch. */
    data class ToolResult(
        val result: dev.openclaw.ferryman.providers.ToolResult,
    ) : ConversationMessage()
}

/** A tool the model may call, described in provider-neutral terms. */
data class ToolDescriptor(
    val name: String,
    val description: String,
)

/** A previously-dispatched tool call's result, fed back into the next turn. */
data class ToolResult(
    val callId: String,
    val toolName: String,
    val content: String,
    val isError: Boolean = false,
)

/** A tool call the model wants executed. */
data class ToolCall(
    val id: String,
    val name: String,
    val argumentsJson: String,
)

/**
 * The result of one completion turn. Either the model produced a final text
 * answer ([output], loop ends) or it requested tool calls ([toolCalls], loop
 * continues). The two are mutually exclusive in practice.
 */
data class CompletionResult(
    val output: String,
    val toolCalls: List<ToolCall> = emptyList(),
    val finishReason: String = "stop",
)

/**
 * Provider-neutral LLM interface. Implemented once per provider family; the
 * orchestrator selects which instance to call based on skill/config hints.
 *
 * The graduation path if this grows beyond two implementations is
 * `ai.koog:koog-agents` (1.0.0, Apache-2.0) — see plan. For the MVP the thin
 * layer wins on legibility and eval-friendliness.
 */
interface LlmProvider {
    val id: String
    val model: String

    suspend fun complete(request: CompletionRequest): CompletionResult
}

/** Factory entry point — turns a [ProviderConfig] into a live [LlmProvider]. */
object LlmProviderFactory {
    fun create(
        config: ProviderConfig,
        apiKey: String,
    ): LlmProvider =
        when (config.type) {
            dev.openclaw.ferryman.config.ProviderType.ANTHROPIC ->
                AnthropicProvider(config.id, config.model, config.baseUrl, apiKey)
            dev.openclaw.ferryman.config.ProviderType.OPENAI_COMPATIBLE ->
                OpenAiCompatibleProvider(config.id, config.model, config.baseUrl, apiKey)
        }
}
