package dev.openclaw.ferryman.host

import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.withCharset
import io.ktor.serialization.kotlinx.json.json
import io.ktor.server.application.Application
import io.ktor.server.application.call
import io.ktor.server.application.install
import io.ktor.server.cio.CIO
import io.ktor.server.engine.embeddedServer
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.request.receiveText
import io.ktor.server.response.respond
import io.ktor.server.response.respondTextWriter
import io.ktor.server.routing.post
import io.ktor.server.routing.routing
import io.modelcontextprotocol.kotlin.sdk.client.Client
import io.modelcontextprotocol.kotlin.sdk.client.ClientOptions
import io.modelcontextprotocol.kotlin.sdk.shared.Transport
import io.modelcontextprotocol.kotlin.sdk.types.ClientCapabilities
import io.modelcontextprotocol.kotlin.sdk.types.Implementation
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.text.Charsets

/**
 * Integration test for the Streamable HTTP transport: stands up a *real*
 * listening MCP server (a Ktor embeddedServer on an OS-assigned port) speaking
 * the 2025-03-26 Streamable HTTP transport, then drives the real SDK [Client]
 * through [spawnHttp] → connect → listTools to prove the transport wiring works
 * over an actual socket.
 *
 * This closes the gap noted on PR #6 ("no live Streamable HTTP MCP server to
 * connect to"). Unlike a mock that stubs the SDK classes, this exercises the
 * real `StreamableHttpClientTransport` + `Client.connect` + `listTools` against
 * a real HTTP endpoint returning real SSE events. Not covered: auth-header
 * enforcement, SSE reconnection, server-initiated requests.
 */
class StreamableHttpTransportTest {
    /**
     * Minimal Streamable HTTP MCP server: POST /mcp, dispatch on the JSON-RPC
     * method, write each result as one SSE `data:` event. Returns an
     * `Mcp-Session-Id` on initialize that the client echoes on later calls.
     */
    private fun mockMcpApp(): Application.() -> Unit =
        {
            install(ContentNegotiation) { json() }
            routing {
                post("/mcp") {
                    val body = call.receiveText()
                    val req = Json.decodeFromString(JsonObject.serializer(), body)
                    val method = req["method"]?.jsonPrimitive?.content
                    val id = req["id"]
                    when (method) {
                        "initialize" -> {
                            call.response.headers.append("Mcp-Session-Id", "test-session-123")
                            val result =
                                buildJsonObject {
                                    put("protocolVersion", "2025-03-26")
                                    put("capabilities", buildJsonObject {})
                                    put(
                                        "serverInfo",
                                        buildJsonObject {
                                            put("name", "mock")
                                            put("version", "0.1")
                                        },
                                    )
                                }
                            call.respondSse(id, result)
                        }
                        "notifications/initialized" -> call.respond(HttpStatusCode.Accepted)
                        "tools/list" -> {
                            val result =
                                buildJsonObject {
                                    put(
                                        "tools",
                                        buildJsonArray {
                                            add(
                                                buildJsonObject {
                                                    put("name", "echo")
                                                    put("description", "Echoes input")
                                                    put(
                                                        "inputSchema",
                                                        buildJsonObject {
                                                            put("type", "object")
                                                            put(
                                                                "properties",
                                                                buildJsonObject {
                                                                    put("msg", buildJsonObject { put("type", "string") })
                                                                },
                                                            )
                                                        },
                                                    )
                                                },
                                            )
                                        },
                                    )
                                }
                            call.respondSse(id, result)
                        }
                        "ping" -> call.respondSse(id, buildJsonObject {})
                        else -> call.respond(HttpStatusCode.OK)
                    }
                }
            }
        }

    /** Write a JSON-RPC response as a single SSE `event: message` / `data:` frame. */
    private suspend fun io.ktor.server.application.ApplicationCall.respondSse(
        id: kotlinx.serialization.json.JsonElement?,
        result: JsonObject,
    ) {
        val response =
            buildJsonObject {
                put("jsonrpc", "2.0")
                if (id != null) put("id", id)
                put("result", result)
            }
        val payload = Json.encodeToString(JsonObject.serializer(), response)
        respondTextWriter(ContentType.Text.EventStream.withCharset(Charsets.UTF_8)) {
            write("event: message\n")
            write("data: $payload\n\n")
            flush()
        }
    }

    @Test
    fun `spawnHttp transport connects and lists tools over Streamable HTTP`() =
        runBlocking {
            // Bind a free port up front so the transport has a concrete URL to dial,
            // then start a real listening server on it. (Ktor's resolvedConnectors()
            // is a suspend member that's awkward to reach here; a known port avoids it.)
            val freePort = java.net.ServerSocket(0).use { it.localPort }
            val server = embeddedServer(CIO, port = freePort, module = mockMcpApp()).start()

            @Suppress("HttpUrlsUsage")
            val url = "http://localhost:$freePort/mcp"

            try {
                val spec = ServerSpec.Http(name = "mock", url = url, headers = mapOf("X-Test" to "yes"))
                val transport: Transport = spawnHttp(spec)

                val client =
                    Client(
                        clientInfo = Implementation(name = "ferryman-test", version = "0.1"),
                        options =
                            ClientOptions(
                                capabilities = ClientCapabilities(),
                                enforceStrictCapabilities = false,
                            ),
                    )
                client.connect(transport)

                val tools = client.listTools()
                assertEquals(1, tools.tools.size)
                assertEquals("echo", tools.tools.first().name)
                assertEquals("Echoes input", tools.tools.first().description)

                client.close()
            } finally {
                server.stop()
            }
        }

    @Test
    fun `resolveTransport returns an HTTP transport with no owning process`() {
        val spec = ServerSpec.Http(name = "mock", url = "http://localhost:1/mcp")
        val (_, process) = resolveTransport(spec)
        // An HTTP transport has no local subprocess — the second pair element is
        // null so McpServerConnection.close() skips process.destroy().
        assertEquals(null, process, "an HTTP transport has no subprocess to destroy")
    }
}
