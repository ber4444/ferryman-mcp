package dev.openclaw.ferryman.host

import io.modelcontextprotocol.kotlin.sdk.client.StdioClientTransport
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
 * Streamable HTTP is intentionally not implemented — the function shape leaves
 * room to add it without touching aggregation logic.
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

/** Dispatch table from a parsed spec to a spawned transport + owning process. */
fun resolveTransport(spec: ServerSpec): Pair<Transport, Process?> =
    when (spec) {
        is ServerSpec.Stdio -> {
            val stdio = spawnStdio(spec)
            stdio.transport to stdio.process
        }
    }
