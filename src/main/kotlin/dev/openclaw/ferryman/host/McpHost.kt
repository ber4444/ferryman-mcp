package dev.openclaw.ferryman.host

import io.modelcontextprotocol.kotlin.sdk.ExperimentalMcpApi
import io.modelcontextprotocol.kotlin.sdk.client.Client
import io.modelcontextprotocol.kotlin.sdk.client.ClientOptions
import io.modelcontextprotocol.kotlin.sdk.types.Implementation
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.readText

/**
 * A namespaced tool aggregated from one MCP server.
 *
 * `server.tool` is the wire name used when dispatching calls; `originalName` is the
 * raw tool name the server reported (what the model sees in its tool list).
 */
data class AggregatedTool(
    val server: String,
    val originalName: String,
    val namespacedName: String,
    val description: String?,
    val inputSchemaJson: String = "{}",
)

/**
 * Connection to a single MCP server over stdio.
 *
 * Holds the spawned subprocess and the connected [Client] so they can be closed
 * together. [Client] handles the initialize handshake inside [connect].
 */
class McpServerConnection(
    val name: String,
    val client: Client,
    private val process: Process? = null,
) {
    /**
     * Closes the connection. [Client.close] is suspend, so this is too — callers
     * inside a coroutine scope should prefer it; [closeBlocking] is provided for
     * non-suspend cleanup paths.
     */
    @OptIn(ExperimentalMcpApi::class)
    suspend fun close() {
        client.close()
        process?.destroy()
    }

    /** Best-effort synchronous close for use in finally blocks without a scope. */
    fun closeBlocking() {
        process?.destroy()
        kotlinx.coroutines.runBlocking { client.close() }
    }
}

/**
 * The MCP host. Reads `.mcp.json`, connects to each configured stdio server,
 * aggregates their tools into a namespaced registry, and dispatches tool calls.
 *
 * The transport is hidden behind [McpTransport] so Streamable HTTP can be added
 * later without touching the aggregation logic (scope discipline — stdio only for MVP).
 */
class McpHost(
    private val serverSpecs: List<ServerSpec>,
) {
    /**
     * Connects to every configured server, performs the initialize handshake, and
     * returns the live connections plus their aggregated tool registry.
     */
    @OptIn(ExperimentalMcpApi::class)
    suspend fun connect(): ConnectedHost {
        val connections = mutableListOf<McpServerConnection>()
        val tools = mutableListOf<AggregatedTool>()
        for (spec in serverSpecs) {
            val (transport, process) = resolveTransport(spec)
            val client =
                Client(
                    clientInfo = Implementation(name = "ferryman", version = "0.1.0"),
                    options = ClientOptions(),
                )
            client.connect(transport)
            val connection = McpServerConnection(spec.name, client, process)
            connections.add(connection)
            try {
                val result = client.listTools()
                for (tool in result.tools) {
                    // Capture the input schema the server advertises so the
                    // provider can pass it to the model — without it the model
                    // doesn't know what arguments to supply.
                    val schemaJson =
                        try {
                            kotlinx.serialization.json.Json.encodeToString(
                                io.modelcontextprotocol.kotlin.sdk.types.ToolSchema
                                    .serializer(),
                                tool.inputSchema,
                            )
                        } catch (e: Exception) {
                            "{}"
                        }
                    tools.add(
                        AggregatedTool(
                            server = spec.name,
                            originalName = tool.name,
                            namespacedName = "${spec.name}.${tool.name}",
                            description = tool.description,
                            inputSchemaJson = schemaJson,
                        ),
                    )
                }
            } catch (e: Exception) {
                // A server without tools support still counts as connected; we
                // just contribute zero tools to the registry.
                System.err.println("[ferryman] server '${spec.name}' listed no tools: ${e.message}")
            }
        }
        return ConnectedHost(connections, tools)
    }

    /** JSON description of the aggregated tool registry — feeds `ferry tools list`. */
    suspend fun describeTools(): String =
        withContext(Dispatchers.IO) {
            ConnectedHost.use(connect()) { connected ->
                val arr =
                    JsonArray(
                        connected.tools.map {
                            JsonObject(
                                mapOf(
                                    "server" to JsonPrimitive(it.server),
                                    "name" to JsonPrimitive(it.namespacedName),
                                    "description" to JsonPrimitive(it.description ?: ""),
                                ),
                            )
                        },
                    )
                arr.toString()
            }
        }

    companion object {
        /** Reads `.mcp.json` (Claude Code's own format) and builds a host from it. */
        fun fromConfig(path: Path): McpHost {
            if (!Files.exists(path)) {
                return McpHost(emptyList())
            }
            val specs = McpConfigParser.parse(path.readText())
            return McpHost(specs)
        }
    }
}

/** Live connections + aggregated registry returned by [McpHost.connect]. */
class ConnectedHost(
    val connections: List<McpServerConnection>,
    val tools: List<AggregatedTool>,
) {
    suspend fun close() = connections.forEach { it.close() }

    fun find(
        server: String,
        tool: String,
    ): AggregatedTool? = tools.firstOrNull { it.server == server && it.originalName == tool }

    companion object {
        /** Runs [block] against a connected host and always closes it afterwards. */
        suspend fun <T> use(
            host: ConnectedHost,
            block: suspend (ConnectedHost) -> T,
        ): T =
            try {
                block(host)
            } finally {
                host.close()
            }
    }
}
