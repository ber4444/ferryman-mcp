package dev.openclaw.ferryman.host

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Parses Claude Code's `.mcp.json` format. Both stdio and Streamable HTTP
 * servers are supported: stdio entries map onto [ServerSpec.Stdio], and
 * `http` / `streamable-http` entries map onto [ServerSpec.Http].
 *
 * Recognised shapes (a subset of Claude Code's format; entries with an
 * unknown type are skipped with a warning so a mixed config file does not
 * crash the host):
 *
 * ```json
 * {
 *   "mcpServers": {
 *     "filesystem": {
 *       "command": "npx",
 *       "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
 *       "env": { "FOO": "bar" }
 *     },
 *     "remote": {
 *       "type": "http",
 *       "url": "https://example.com/mcp",
 *       "headers": { "Authorization": "Bearer TOKEN" }
 *     }
 *   }
 * }
 * ```
 */
object McpConfigParser {
    private val json = Json { ignoreUnknownKeys = true }

    fun parse(text: String): List<ServerSpec> {
        if (text.isBlank()) return emptyList()
        val root = json.parseToJsonElement(text).jsonObject
        val servers = root["mcpServers"]?.jsonObject ?: return emptyList()
        return servers.entries.mapNotNull { (name, spec) -> parseServer(name, spec) }
    }

    private fun parseServer(
        name: String,
        element: JsonElement,
    ): ServerSpec? {
        val obj = element as? JsonObject ?: return null
        val type = obj["type"]?.jsonPrimitive?.contentOrNull ?: "stdio"
        return when (type) {
            "stdio" -> parseStdio(name, obj)
            "http", "streamable-http" -> parseHttp(name, obj)
            else -> {
                System.err.println("[ferryman] skipping server '$name': type '$type' not supported (stdio and http only)")
                null
            }
        }
    }

    private fun parseStdio(
        name: String,
        obj: JsonObject,
    ): ServerSpec? {
        val command =
            obj["command"]?.jsonPrimitive?.contentOrNull
                ?: run {
                    System.err.println("[ferryman] skipping server '$name': missing 'command'")
                    return null
                }
        val args = obj["args"]?.asListOfStrings().orEmpty()
        val env = obj["env"]?.asEnvMap().orEmpty()
        return ServerSpec.Stdio(name, command, args, env)
    }

    private fun parseHttp(
        name: String,
        obj: JsonObject,
    ): ServerSpec? {
        val url =
            obj["url"]?.jsonPrimitive?.contentOrNull
                ?: run {
                    System.err.println("[ferryman] skipping server '$name': missing 'url'")
                    return null
                }
        val headers = obj["headers"]?.asHeadersMap().orEmpty()
        return ServerSpec.Http(name, url, headers)
    }
}

private fun JsonElement.asListOfStrings(): List<String>? = (this as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }

private fun JsonElement.asEnvMap(): Map<String, String>? = asStringMap()

private fun JsonElement.asHeadersMap(): Map<String, String>? = asStringMap()

private fun JsonElement.asStringMap(): Map<String, String>? =
    (this as? JsonObject)?.entries?.associate { (k, v) -> k to (v.jsonPrimitive.contentOrNull ?: "") }
