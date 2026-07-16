package dev.openclaw.ferryman.host

import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.sse.SSE
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.header
import io.modelcontextprotocol.kotlin.sdk.client.StdioClientTransport
import io.modelcontextprotocol.kotlin.sdk.client.StreamableHttpClientTransport
import io.modelcontextprotocol.kotlin.sdk.shared.Transport
import kotlinx.io.asSink
import kotlinx.io.asSource
import kotlinx.io.buffered
import java.io.IOException

/** Description of one MCP server connection, parsed from `.mcp.json`. */
sealed class ServerSpec {
    abstract val name: String

    /** A stdio server launched as a subprocess. */
    data class Stdio(
        override val name: String,
        val command: String,
        val args: List<String>,
        val env: Map<String, String> = emptyMap(),
    ) : ServerSpec()

    /**
     * A Streamable HTTP MCP server (spec: "Streamable HTTP transport", the
     * successor to HTTP+SSE). The [url] is the MCP endpoint and [headers] are
     * applied to every request (e.g. an `Authorization` bearer token).
     */
    data class Http(
        override val name: String,
        val url: String,
        val headers: Map<String, String> = emptyMap(),
    ) : ServerSpec()
}

/** Result of spawning a stdio server: the transport plus the process to destroy later. */
data class StdioTransport(
    val transport: StdioClientTransport,
    val process: Process,
)

/**
 * Spawns a [ServerSpec.Stdio] subprocess and builds the SDK transport over its streams.
 *
 * kotlinx-io ships `InputStream.asSource()` / `OutputStream.asSink()` (in the JVM
 * variant of `kotlinx-io-core`); the SDK's [StdioClientTransport] takes the
 * buffered `Source`/`Sink` types.
 *
 * Streamable HTTP servers are handled by [spawnHttp]; this stdio helper only
 * owns the subprocess lifecycle.
 */
fun spawnStdio(spec: ServerSpec.Stdio): StdioTransport {
    val process =
        ProcessBuilder(
            buildList {
                add(spec.command)
                addAll(spec.args)
            },
        ).apply {
            spec.env.forEach { (k, v) -> environment()[k] = v }
            redirectErrorStream(false)
        }.start()

    // Surface a clear failure if the binary is missing or cannot start, rather
    // than hanging on an empty pipe during the initialize handshake.
    if (!process.isAlive) {
        throw IOException(
            "MCP server '${spec.name}' failed to start: ${spec.command} ${spec.args.joinToString(" ")}",
        )
    }

    val transport =
        StdioClientTransport(
            input = process.inputStream.asSource().buffered(),
            output = process.outputStream.asSink().buffered(),
            error = process.errorStream.asSource().buffered(),
        )
    return StdioTransport(transport, process)
}

/**
 * Builds an SDK [StreamableHttpClientTransport] for an HTTP MCP server.
 *
 * This owns a dedicated [HttpClient] (CIO engine, separate from the LLM
 * provider's client). The SDK transport speaks the Streamable HTTP transport
 * — POSTing JSON-RPC requests to [spec]'s URL and consuming Server-Sent Events
 * for server-initiated messages, which is why the client needs the [SSE]
 * plugin installed (the plugin and its `ClientSSESession` live in
 * `ktor-client-core`, so no extra dependency is required).
 *
 * [StreamableHttpClientTransport]'s `requestBuilder` lambda is where per-request
 * headers are injected (e.g. `Authorization`); it runs for every outbound call.
 *
 * Returns a plain [Transport] (the broad SDK interface) and no [Process] — an
 * HTTP server has no local subprocess to tear down.
 */
fun spawnHttp(spec: ServerSpec.Http): Transport {
    val client =
        HttpClient(CIO) {
            install(SSE)
        }
    val requestBuilder: (HttpRequestBuilder) -> Unit = { builder ->
        spec.headers.forEach { (key, value) -> builder.header(key, value) }
    }
    return StreamableHttpClientTransport(
        client = client,
        url = spec.url,
        requestBuilder = requestBuilder,
    )
}

/** Dispatch table from a parsed spec to a spawned transport + owning process. */
fun resolveTransport(spec: ServerSpec): Pair<Transport, Process?> =
    when (spec) {
        is ServerSpec.Stdio -> {
            val stdio = spawnStdio(spec)
            stdio.transport to stdio.process
        }
        is ServerSpec.Http -> spawnHttp(spec) to null
    }
