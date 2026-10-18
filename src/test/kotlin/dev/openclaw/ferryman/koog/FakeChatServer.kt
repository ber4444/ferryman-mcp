package dev.openclaw.ferryman.koog

import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.server.cio.CIO
import io.ktor.server.engine.EmbeddedServer
import io.ktor.server.engine.embeddedServer
import io.ktor.server.request.path
import io.ktor.server.request.receiveText
import io.ktor.server.response.respondText
import io.ktor.server.routing.post
import io.ktor.server.routing.routing
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A scripted OpenAI-compatible `chat/completions` endpoint on a random local port.
 * Each request pops the next scripted response and is recorded, so a test can
 * assert exactly what an engine put on the wire. Mounted under a z.ai-style path
 * prefix to catch base-URL joining bugs.
 */
class FakeChatServer(
    private val pathPrefix: String = "/api/coding/paas/v4",
) : AutoCloseable {
    data class Captured(
        val path: String,
        val authorization: String?,
        val body: JsonObject,
    )

    val requests = CopyOnWriteArrayList<Captured>()
    private val script = ConcurrentLinkedQueue<Pair<Int, String>>()
    private var server: EmbeddedServer<*, *>? = null

    fun enqueue(
        body: String,
        status: Int = 200,
    ) {
        script.add(status to body)
    }

    /** Starts the server and returns the base URL a provider entry would hold. */
    fun start(): String {
        val started =
            embeddedServer(CIO, port = 0) {
                routing {
                    post("$pathPrefix/chat/completions") {
                        val text = call.receiveText()
                        requests.add(
                            Captured(
                                path = call.request.path(),
                                authorization = call.request.headers["Authorization"],
                                body = Json.parseToJsonElement(text).jsonObject,
                            ),
                        )
                        val (status, body) = script.poll() ?: (500 to """{"error":{"message":"script exhausted"}}""")
                        call.respondText(body, ContentType.Application.Json, HttpStatusCode.fromValue(status))
                    }
                }
            }.start(wait = false)
        server = started
        val port =
            runBlocking {
                started.engine
                    .resolvedConnectors()
                    .first()
                    .port
            }
        return "http://127.0.0.1:$port$pathPrefix"
    }

    override fun close() {
        server?.stop(gracePeriodMillis = 0, timeoutMillis = 500)
    }

    companion object {
        fun toolCall(
            name: String,
            argumentsJson: String,
            id: String = "call_1",
            text: String? = null,
            promptTokens: Int = 100,
            completionTokens: Int = 20,
        ): String =
            completion(
                message =
                    buildJsonObject {
                        put("role", "assistant")
                        put("content", text?.let { JsonPrimitive(it) } ?: JsonPrimitive(null as String?))
                        put(
                            "tool_calls",
                            buildJsonArray {
                                add(
                                    buildJsonObject {
                                        put("id", id)
                                        put("type", "function")
                                        put(
                                            "function",
                                            buildJsonObject {
                                                put("name", name)
                                                put("arguments", argumentsJson)
                                            },
                                        )
                                    },
                                )
                            },
                        )
                    },
                finishReason = "tool_calls",
                promptTokens = promptTokens,
                completionTokens = completionTokens,
            )

        fun answer(
            text: String,
            promptTokens: Int = 150,
            completionTokens: Int = 30,
        ): String =
            completion(
                message =
                    buildJsonObject {
                        put("role", "assistant")
                        put("content", text)
                    },
                finishReason = "stop",
                promptTokens = promptTokens,
                completionTokens = completionTokens,
            )

        private fun completion(
            message: JsonObject,
            finishReason: String,
            promptTokens: Int,
            completionTokens: Int,
        ): String =
            buildJsonObject {
                put("id", "chatcmpl-test")
                put("object", "chat.completion")
                put("created", 1_760_000_000)
                put("model", "fake-model")
                put(
                    "choices",
                    buildJsonArray {
                        add(
                            buildJsonObject {
                                put("index", 0)
                                put("message", message)
                                put("finish_reason", finishReason)
                            },
                        )
                    },
                )
                put(
                    "usage",
                    buildJsonObject {
                        put("prompt_tokens", promptTokens)
                        put("completion_tokens", completionTokens)
                        put("total_tokens", promptTokens + completionTokens)
                    },
                )
            }.toString()

        /** Message text whether the engine sent `content` as a string or as an array of parts. */
        fun contentText(message: JsonElement): String =
            when (val content = message.jsonObject["content"]) {
                is JsonPrimitive -> content.contentOrNull.orEmpty()
                is JsonArray ->
                    content.joinToString("") {
                        it.jsonObject["text"]
                            ?.jsonPrimitive
                            ?.contentOrNull
                            .orEmpty()
                    }
                else -> ""
            }
    }
}
