package dev.openclaw.ferryman.koog

import ai.koog.agents.core.tools.ToolRegistry
import ai.koog.agents.mcp.McpToolRegistryProvider
import ai.koog.agents.mcp.metadata.McpServerInfo
import dev.openclaw.ferryman.host.ServerSpec
import dev.openclaw.ferryman.host.resolveTransport
import io.modelcontextprotocol.kotlin.sdk.ExperimentalMcpApi
import io.modelcontextprotocol.kotlin.sdk.client.Client
import io.modelcontextprotocol.kotlin.sdk.shared.Transport
import io.modelcontextprotocol.kotlin.sdk.types.Implementation

/**
 * The tools a Koog run can call, plus the cleanup for whatever backs them.
 * [serverOf] maps a tool name to the MCP server that provides it, so tool calls
 * are recorded as `<server>.<tool>` — the same shape the hand-rolled engine logs.
 */
class OpenedTools(
    val registry: ToolRegistry,
    private val serverOf: Map<String, String> = emptyMap(),
    private val onClose: suspend () -> Unit = {},
) {
    fun qualifiedName(tool: String): String = serverOf[tool]?.let { "$it.$tool" } ?: tool

    suspend fun close() = onClose()
}

/** Opens a fresh tool set per run (the hand-rolled engine also connects per run). */
fun interface KoogToolSource {
    suspend fun open(): OpenedTools
}

/**
 * Exposes the `.mcp.json` servers to Koog through its own MCP bridge,
 * [McpToolRegistryProvider].
 *
 * The server processes are spawned by the host's [resolveTransport], so both
 * engines talk to byte-identical server processes; from there Koog owns the
 * client, the tool descriptors the model sees and how results are rendered back
 * to the model. Unlike the hand-rolled host, Koog doesn't namespace tool names:
 * the model sees `fetch`, not `fetch_fetch`.
 */
class McpKoogToolSource(
    private val specs: List<ServerSpec>,
    // Seam for tests (an in-process server); production spawns the spec's process.
    private val connect: (ServerSpec) -> Pair<Transport, Process?> = ::resolveTransport,
) : KoogToolSource {
    @OptIn(ExperimentalMcpApi::class)
    override suspend fun open(): OpenedTools {
        val clients = mutableListOf<Pair<Client, Process?>>()
        var registry = ToolRegistry.EMPTY
        val serverOf = mutableMapOf<String, String>()
        val closeAll: suspend () -> Unit = {
            clients.forEach { (client, process) ->
                runCatching { client.close() }
                process?.destroy()
            }
        }
        try {
            for (spec in specs) {
                val (transport, process) = connect(spec)
                val client = Client(clientInfo = Implementation(name = "ferryman-koog", version = "0.1.0"))
                clients.add(client to process)
                client.connect(transport)
                // Mirror the hand-rolled host: a server whose tool listing fails
                // still counts as connected and contributes zero tools.
                val serverTools =
                    try {
                        McpToolRegistryProvider.fromClient(client, McpServerInfo(command = describe(spec)))
                    } catch (e: Exception) {
                        System.err.println("[ferryman-koog] server '${spec.name}' listed no tools: ${e.message}")
                        ToolRegistry.EMPTY
                    }
                serverTools.tools.forEach { serverOf[it.name] = spec.name }
                registry += serverTools
            }
        } catch (e: Throwable) {
            closeAll()
            throw e
        }
        return OpenedTools(registry, serverOf, closeAll)
    }

    private fun describe(spec: ServerSpec): String =
        when (spec) {
            is ServerSpec.Stdio -> (listOf(spec.command) + spec.args).joinToString(" ")
        }
}
