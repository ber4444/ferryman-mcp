package dev.openclaw.ferryman.channels

import dev.openclaw.ferryman.orchestrator.Orchestrator
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.serialization.kotlinx.json.json
import io.ktor.server.application.Application
import io.ktor.server.application.install
import io.ktor.server.cio.CIO
import io.ktor.server.engine.embeddedServer
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.request.receive
import io.ktor.server.response.respond
import io.ktor.server.response.respondText
import io.ktor.server.routing.get
import io.ktor.server.routing.post
import io.ktor.server.routing.routing
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * The HTTP channel — a minimal Ktor server exposing `POST /invoke` that calls the
 * *same* [Orchestrator.runSkill] the CLI uses. Demonstrating the multi-channel
 * claim honestly means both paths hit identical code.
 */
class HttpServer(
    private val orchestrator: Orchestrator,
    private val port: Int,
) {
    suspend fun start() {
        embeddedServer(CIO, port = port, module = { module(orchestrator) }).start(wait = true)
    }
}

@Serializable
private data class InvokeRequest(
    val skill: String,
    val input: String,
    val provider: String? = null,
)

@Serializable
private data class InvokeResponse(
    val output: String,
    val provider: String,
    val model: String,
    val toolCalls: List<String>,
    // Real token counts from the provider's usage block, summed across turns.
    // Optional (default null) so older callers and error rows still deserialize.
    @SerialName("inputTokens") val inputTokens: Int? = null,
    @SerialName("outputTokens") val outputTokens: Int? = null,
)

private fun Application.module(orchestrator: Orchestrator) {
    install(ContentNegotiation) {
        json(
            kotlinx.serialization.json.Json {
                ignoreUnknownKeys = true
            },
        )
    }
    routing {
        post("/invoke") {
            val request = call.receive<InvokeRequest>()
            val result = orchestrator.runSkill(request.skill, request.input, request.provider)
            call.respond(
                InvokeResponse(
                    output = result.output,
                    provider = result.provider,
                    model = result.model,
                    toolCalls = result.toolCalls,
                    inputTokens = result.inputTokens,
                    outputTokens = result.outputTokens,
                ),
            )
        }
        // Health check for container/CI probes.
        post("/health") {
            call.respondText("ok", contentType = ContentType.Text.Plain)
        }
    }
}
