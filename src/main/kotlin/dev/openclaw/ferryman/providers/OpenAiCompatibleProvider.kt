package dev.openclaw.ferryman.providers

import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.EncodeDefault
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/**
 * OpenAI Chat Completions–shaped provider. Covers OpenAI itself plus every
 * compatible endpoint — z.ai GLM (`https://api.z.ai/api/coding/paas/v4`,
 * model `glm-5.2`), OpenRouter, Together, Fireworks, Ollama, vLLM. Repointing
 * the provider is a config-only change: that is the point of the abstraction.
 *
 * Only the `chat/completions` path is used. Tool calls come back in
 * `choices[0].message.tool_calls`; a non-empty list means the orchestrator
 * dispatches and re-calls.
 */
class OpenAiCompatibleProvider(
    override val id: String,
    override val model: String,
    private val baseUrl: String,
    private val apiKey: String,
    private val client: HttpClient = defaultClient(),
) : LlmProvider {
    override suspend fun complete(request: CompletionRequest): CompletionResult {
        val payload = buildChatRequest(request)
        val httpResponse =
            client.post("${baseUrl.trimEnd('/')}/chat/completions") {
                header("Authorization", "Bearer $apiKey")
                contentType(ContentType.Application.Json)
                setBody(payload)
            }
        if (!httpResponse.status.isSuccess()) {
            val errorBody = httpResponse.bodyAsText()
            throw RuntimeException(
                "Provider '$id' returned ${httpResponse.status}: ${errorBody.take(500)}",
            )
        }
        val response: ChatCompletionResponse = httpResponse.body()
        return response.toResult()
    }

    private fun buildChatRequest(request: CompletionRequest): ChatCompletionRequest {
        val messages =
            buildList {
                add(ChatMessage(role = "system", content = request.system))
                add(ChatMessage(role = "user", content = request.user))
                // Reconstruct the full conversation: assistant tool-call turns
                // interleaved with tool results. This is required by the OpenAI API —
                // orphan tool messages without a preceding assistant tool_call cause
                // the model to loop indefinitely.
                for (msg in request.conversation) {
                    when (msg) {
                        is ConversationMessage.AssistantToolCall -> {
                            add(
                                ChatMessage(
                                    role = "assistant",
                                    content = "",
                                    toolCalls =
                                        msg.toolCalls.map { call ->
                                            ToolCallSpec(
                                                id = call.id,
                                                function =
                                                    FunctionCall(
                                                        name = call.name.replace('.', '_'),
                                                        arguments = call.argumentsJson,
                                                    ),
                                            )
                                        },
                                ),
                            )
                        }
                        is ConversationMessage.ToolResult -> {
                            add(
                                ChatMessage(
                                    role = "tool",
                                    content = msg.result.content,
                                    toolCallId = msg.result.callId,
                                ),
                            )
                        }
                    }
                }
            }
        val tools =
            request.tools.map { descriptor ->
                ToolDef(
                    function =
                        FunctionDef(
                            // OpenAI function names must match ^[a-zA-Z0-9_-]+$.
                            name = descriptor.name.replace('.', '_'),
                            description = descriptor.description,
                            // Use the real input schema from the MCP server so the
                            // model knows what arguments to supply.
                            parameters =
                                Json.parseToJsonElement(descriptor.parametersJson),
                        ),
                )
            }
        return ChatCompletionRequest(
            model = model,
            messages = messages,
            tools = tools.ifEmpty { null },
        )
    }

    companion object {
        fun defaultClient(): HttpClient =
            HttpClient(CIO) {
                install(ContentNegotiation) {
                    json(
                        Json {
                            ignoreUnknownKeys = true
                            encodeDefaults = false
                        },
                    )
                }
            }
    }
}

@Serializable
private data class ChatCompletionRequest(
    val model: String,
    val messages: List<ChatMessage>,
    val tools: List<ToolDef>? = null,
    @SerialName("tool_choice") val toolChoice: String? = null,
)

@Serializable
private data class ChatMessage(
    val role: String,
    val content: String,
    @SerialName("tool_call_id") val toolCallId: String? = null,
    @SerialName("tool_calls") val toolCalls: List<ToolCallSpec>? = null,
)

@OptIn(ExperimentalSerializationApi::class)
@Serializable
private data class ToolDef(
    @EncodeDefault val type: String = "function",
    val function: FunctionDef,
)

@Serializable
private data class FunctionDef(
    val name: String,
    val description: String,
    val parameters: JsonElement,
)

@OptIn(ExperimentalSerializationApi::class)
@Serializable
private data class ToolCallSpec(
    val id: String,
    @EncodeDefault val type: String = "function",
    val function: FunctionCall,
)

@Serializable
private data class FunctionCall(
    val name: String,
    val arguments: String,
)

@Serializable
private data class ChatCompletionResponse(
    val choices: List<Choice> = emptyList(),
) {
    fun toResult(): CompletionResult {
        val choice =
            choices.firstOrNull()
                ?: return CompletionResult(output = "", finishReason = "empty")
        val message = choice.message
        val calls =
            message.toolCalls.orEmpty().map {
                // The model returns the sanitized wire name (filesystem_read_file).
                // Reverse to the namespaced form (filesystem.read_file) so the
                // orchestrator can dispatch to the correct MCP server.
                val wireName = it.function.name
                val namespaced =
                    wireName.replaceFirst('_', '.')
                ToolCall(id = it.id, name = namespaced, argumentsJson = it.function.arguments)
            }
        return CompletionResult(
            output = message.content ?: "",
            toolCalls = calls,
            finishReason = choice.finishReason,
        )
    }
}

@Serializable
private data class Choice(
    val message: ResponseMessage,
    @SerialName("finish_reason") val finishReason: String = "stop",
)

@Serializable
private data class ResponseMessage(
    val role: String = "assistant",
    val content: String? = null,
    @SerialName("tool_calls") val toolCalls: List<ToolCallSpec>? = null,
)
