package dev.openclaw.ferryman.providers

import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.contentType
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Anthropic Messages API provider (Claude). Kept alongside [OpenAiCompatibleProvider]
 * so multi-provider routing is a real, measured capability rather than a config
 * option nobody exercises. The shape mirrors Anthropic's documented request format.
 *
 * The MVP uses the OpenAI-compatible path for z.ai GLM; this provider is what
 * makes `anthropic` a second routable entry in config.toml.
 */
class AnthropicProvider(
    override val id: String,
    override val model: String,
    private val baseUrl: String = "https://api.anthropic.com/v1",
    private val apiKey: String,
    private val client: HttpClient = OpenAiCompatibleProvider.defaultClient(),
) : LlmProvider {
    override suspend fun complete(request: CompletionRequest): CompletionResult {
        val payload =
            AnthropicRequest(
                model = model,
                maxTokens = 1024,
                system = request.system,
                messages = listOf(AnthropicMessage(role = "user", content = request.user)),
            )
        val response: AnthropicResponse =
            client
                .post("${baseUrl.trimEnd('/')}/messages") {
                    header("x-api-key", apiKey)
                    header("anthropic-version", "2023-06-01")
                    contentType(ContentType.Application.Json)
                    setBody(payload)
                }.body()
        return response.toResult()
    }
}

@Serializable
private data class AnthropicRequest(
    val model: String,
    @SerialName("max_tokens") val maxTokens: Int,
    val system: String,
    val messages: List<AnthropicMessage>,
)

@Serializable
private data class AnthropicMessage(
    val role: String,
    val content: String,
)

@Serializable
private data class AnthropicResponse(
    val content: List<Block> = emptyList(),
    @SerialName("stop_reason") val stopReason: String? = null,
) {
    fun toResult(): CompletionResult {
        val text = content.filterIsInstance<Block.Text>().joinToString("") { it.text }
        return CompletionResult(output = text, finishReason = stopReason ?: "stop")
    }
}

@Serializable
private sealed class Block {
    @SerialName("type")
    abstract val type: String

    @Serializable
    @SerialName("text")
    data class Text(
        val text: String,
    ) : Block() {
        @SerialName("type")
        override val type: String = "text"
    }
}
