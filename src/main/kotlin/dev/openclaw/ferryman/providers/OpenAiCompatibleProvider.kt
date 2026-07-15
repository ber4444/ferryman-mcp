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
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

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
        val maxRetries = 3
        var lastError: String? = null
        repeat(maxRetries) { attempt ->
            val httpResponse =
                client.post("${baseUrl.trimEnd('/')}/chat/completions") {
                    header("Authorization", "Bearer $apiKey")
                    contentType(ContentType.Application.Json)
                    setBody(payload)
                }
            if (httpResponse.status.isSuccess()) {
                val response: ChatCompletionResponse = httpResponse.body()
                return response.toResult()
            }
            val errorBody = httpResponse.bodyAsText()
            lastError = "Provider '$id' returned ${httpResponse.status}: ${errorBody.take(500)}"
            // Retry on 429 (rate limit) and 503 (overloaded) with backoff.
            val retryable = httpResponse.status.value == 429 || httpResponse.status.value == 503
            if (!retryable || attempt == maxRetries - 1) {
                throw RuntimeException(lastError)
            }
            kotlinx.coroutines.delay((attempt + 1) * 5000L)
        }
        throw RuntimeException(lastError ?: "exhausted retries")
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
                            // Embed the raw JSON from the model's original response
                            // so provider-specific fields like Gemini's
                            // thought_signature survive the round-trip verbatim.
                            val rawElements =
                                msg.toolCalls.map { call ->
                                    if (call.rawJson != null) {
                                        Json.parseToJsonElement(call.rawJson)
                                    } else {
                                        JsonObject(
                                            mapOf(
                                                "id" to JsonPrimitive(call.id),
                                                "type" to JsonPrimitive("function"),
                                                "function" to
                                                    JsonObject(
                                                        mapOf(
                                                            "name" to JsonPrimitive(call.name.replace('.', '_')),
                                                            "arguments" to JsonPrimitive(call.argumentsJson),
                                                        ),
                                                    ),
                                            ),
                                        )
                                    }
                                }
                            add(
                                ChatMessage(
                                    role = "assistant",
                                    content = "",
                                    toolCalls = rawElements,
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
                // LLM completions with tool calls can take 30-60s per turn.
                // The CIO default is too short for that.
                engine {
                    requestTimeout = 120_000
                }
            }
    }
}

@OptIn(ExperimentalSerializationApi::class)
@Serializable
private data class ChatCompletionRequest(
    val model: String,
    val messages: List<ChatMessage>,
    val tools: List<ToolDef>? = null,
    @SerialName("tool_choice") val toolChoice: String? = null,
    @EncodeDefault @SerialName("max_tokens") val maxTokens: Int = 2048,
)

@Serializable
private data class ChatMessage(
    val role: String,
    val content: String,
    @SerialName("tool_call_id") val toolCallId: String? = null,
    // Raw JsonElements so provider-specific fields (Gemini's thought_signature)
    // survive serialization round-trips.
    @SerialName("tool_calls") val toolCalls: List<
        @Serializable(with = RawJsonElementSerializer::class)
        JsonElement,
    >? = null,
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
            message.toolCalls.orEmpty().map { rawElement ->
                // Parse the known fields from the raw JSON element, but preserve
                // the full object as rawJson so provider-specific fields (e.g.
                // Gemini's thought_signature) survive the round-trip.
                val obj = rawElement as? JsonObject
                val id = obj?.get("id")?.let { (it as? kotlinx.serialization.json.JsonPrimitive)?.content } ?: ""
                val func = obj?.get("function") as? JsonObject
                val wireName = func?.get("name")?.let { (it as? kotlinx.serialization.json.JsonPrimitive)?.content } ?: ""
                val namespaced = wireName.replaceFirst('_', '.')
                val args = func?.get("arguments")?.let { (it as? kotlinx.serialization.json.JsonPrimitive)?.content } ?: "{}"
                ToolCall(
                    id = id,
                    name = namespaced,
                    argumentsJson = args,
                    rawJson = rawElement.toString(),
                )
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
    // Store tool_calls as raw JsonElements so provider-specific fields like
    // Gemini's thought_signature survive deserialization and can be echoed
    // back in the next request.
    @SerialName("tool_calls") val toolCalls: List<
        @Serializable(with = RawJsonElementSerializer::class)
        JsonElement,
    >? = null,
)

/**
 * Pass-through serializer for [JsonElement] — reads and writes the raw JSON
 * tree without loss, so provider-specific fields (e.g. Gemini's
 * thought_signature inside tool call objects) survive serialization.
 */
private object RawJsonElementSerializer :
    kotlinx.serialization.KSerializer<JsonElement> {
    override val descriptor =
        kotlinx.serialization.json.JsonElement
            .serializer()
            .descriptor

    override fun serialize(
        encoder: kotlinx.serialization.encoding.Encoder,
        value: JsonElement,
    ) {
        kotlinx.serialization.json.JsonElement
            .serializer()
            .serialize(encoder, value)
    }

    override fun deserialize(decoder: kotlinx.serialization.encoding.Decoder): JsonElement =
        kotlinx.serialization.json.JsonElement
            .serializer()
            .deserialize(decoder)
}
