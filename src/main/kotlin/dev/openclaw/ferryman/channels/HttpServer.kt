package dev.openclaw.ferryman.channels

import dev.openclaw.ferryman.orchestrator.Engine
import dev.openclaw.ferryman.orchestrator.SkillRunner
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
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
import kotlinx.serialization.EncodeDefault
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * The HTTP channel — a minimal Ktor server exposing `POST /invoke` that calls the
 * *same* [SkillRunner.runSkill] the CLI uses. Demonstrating the multi-channel
 * claim honestly means both paths hit identical code.
 *
 * [runners] holds one runner per [Engine]; a request picks one with its optional
 * `engine` field (default: the hand-rolled orchestrator).
 */
class HttpServer(
    private val runners: Map<Engine, SkillRunner>,
    private val port: Int,
) {
    suspend fun start() {
        embeddedServer(CIO, port = port, module = { module(runners) }).start(wait = true)
    }
}

@Serializable
private data class InvokeRequest(
    val skill: String,
    val input: String,
    val provider: String? = null,
    // "ferryman" (default) or "koog". Echoed back in the response so a caller can
    // detect an older server that ignores the field.
    val engine: String? = null,
)

@OptIn(ExperimentalSerializationApi::class)
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
    // Always on the wire (the server's Json skips defaults): the harness checks it
    // to catch a server that ran a different engine than it asked for.
    @EncodeDefault val engine: String = Engine.FERRYMAN.id,
)

internal fun Application.module(runners: Map<Engine, SkillRunner>) {
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
            val runner = Engine.fromId(request.engine)?.let { runners[it] }
            if (runner == null) {
                call.respondText(
                    "Unknown engine '${request.engine}'. Expected one of: ${runners.keys.joinToString { it.id }}",
                    status = HttpStatusCode.BadRequest,
                )
                return@post
            }
            val result = runner.runSkill(request.skill, request.input, request.provider)
            call.respond(
                InvokeResponse(
                    output = result.output,
                    provider = result.provider,
                    model = result.model,
                    toolCalls = result.toolCalls,
                    inputTokens = result.inputTokens,
                    outputTokens = result.outputTokens,
                    engine = result.engine,
                ),
            )
        }
        // Health check for container/CI probes.
        post("/health") {
            call.respondText("ok", contentType = ContentType.Text.Plain)
        }
    }
}
