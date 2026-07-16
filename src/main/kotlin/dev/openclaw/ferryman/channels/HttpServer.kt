package dev.openclaw.ferryman.channels

import dev.openclaw.ferryman.orchestrator.Orchestrator
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
import io.ktor.server.routing.post
import io.ktor.server.routing.routing
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.security.MessageDigest

/**
 * The HTTP channel — a Ktor server exposing `POST /invoke` that calls the
 * *same* [Orchestrator.runSkill] the CLI uses. Demonstrating the multi-channel
 * claim honestly means both paths hit identical code.
 *
 * Inbound auth is mandatory: callers must present [apiToken] as a bearer token
 * (`Authorization: Bearer <token>`). The server is constructed only after the
 * caller has supplied a token, so an unauthenticated `POST /invoke` is rejected
 * with 401 before the orchestrator runs.
 */
class HttpServer(
    private val orchestrator: Orchestrator,
    private val port: Int,
    private val apiToken: String,
) {
    suspend fun start() {
        embeddedServer(CIO, port = port, module = { module(orchestrator, apiToken) }).start(wait = true)
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

@Serializable
private data class ErrorResponse(
    val error: String,
)

/**
 * The server module. Internal so [HttpServerTest] can install it via Ktor's
 * `testApplication` without standing up a real listening socket.
 */
internal fun Application.module(
    orchestrator: Orchestrator,
    apiToken: String,
) {
    install(ContentNegotiation) {
        json(
            kotlinx.serialization.json.Json {
                ignoreUnknownKeys = true
            },
        )
    }
    routing {
        post("/invoke") {
            // Reject requests without a valid bearer token *before* any
            // orchestrator work — so an unauthenticated caller can't burn
            // provider credits or reach skill logic. MessageDigest.isEqual is
            // constant-time, avoiding a timing side-channel on the comparison.
            if (!checkBearer(call.request.headers["Authorization"], apiToken)) {
                call.respond(HttpStatusCode.Unauthorized, ErrorResponse("unauthorized"))
                return@post
            }
            val request = call.receive<InvokeRequest>()
            // A provider failure (timeout, exhausted 429 retries, transport
            // error) throws from runSkill. Map it to a clean 502 with a JSON
            // error body — mirroring RunCommand's stderr one-liner — instead
            // of letting Ktor surface a raw 500 + stack trace.
            val result =
                try {
                    orchestrator.runSkill(request.skill, request.input, request.provider)
                } catch (e: RuntimeException) {
                    call.respond(
                        HttpStatusCode.BadGateway,
                        ErrorResponse("upstream provider failure: ${e.message}"),
                    )
                    return@post
                }
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
        // Health check for container/CI probes. Intentionally unauthenticated:
        // it carries no data and exists only for liveness checks.
        post("/health") {
            call.respondText("ok", contentType = ContentType.Text.Plain)
        }
    }
}

/**
 * True iff [authHeader] is `Bearer <token>` and `<token>` matches [expected]
 * using a constant-time comparison.
 */
private fun checkBearer(
    authHeader: String?,
    expected: String,
): Boolean {
    if (authHeader == null) return false
    val presented = authHeader.removePrefix("Bearer ").trim()
    // removePrefix leaves the string untouched when the prefix is absent, so a
    // header that isn't a bearer token fails both the prefix check and the
    // (now meaningless) comparison.
    if (!authHeader.startsWith("Bearer ")) return false
    return MessageDigest.isEqual(
        presented.toByteArray(Charsets.UTF_8),
        expected.toByteArray(Charsets.UTF_8),
    )
}
