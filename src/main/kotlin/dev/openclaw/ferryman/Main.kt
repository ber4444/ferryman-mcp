package dev.openclaw.ferryman

import com.github.ajalt.clikt.core.Context
import com.github.ajalt.clikt.core.CoreCliktCommand
import com.github.ajalt.clikt.core.main
import com.github.ajalt.clikt.core.subcommands
import com.github.ajalt.clikt.parameters.options.default
import com.github.ajalt.clikt.parameters.options.option
import com.github.ajalt.clikt.parameters.options.required
import com.github.ajalt.clikt.parameters.types.int
import dev.openclaw.ferryman.channels.HttpServer
import dev.openclaw.ferryman.config.ConfigLoader
import dev.openclaw.ferryman.host.McpHost
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.orchestrator.Orchestrator
import dev.openclaw.ferryman.providers.ProviderRegistry
import dev.openclaw.ferryman.skills.SkillLoader
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import java.nio.file.Path
import java.nio.file.Paths

fun main(args: Array<String>) =
    Ferry()
        .subcommands(
            ProvidersCommand().subcommands(ProvidersListCommand()),
            SkillsCommand().subcommands(SkillsListCommand()),
            ToolsCommand().subcommands(ToolsListCommand()),
            RunCommand(),
            ServeCommand(),
        ).main(args)

/**
 * `ferry` — ferryman's CLI.
 *
 * A local-first MCP host with pluggable skills, multi-provider routing, and
 * multi-channel I/O. Every subcommand maps to one capability row in the README
 * feature-status table.
 *
 * clikt 5.x: help text is an override of [CoreCliktCommand.help], not a constructor
 * parameter. `name` is the only constructor param.
 */
class Ferry : CoreCliktCommand(name = "ferry") {
    override val invokeWithoutSubcommand: Boolean = true

    override fun help(context: Context): String =
        "ferryman — a local-first MCP host with pluggable skills, multi-provider routing, and multi-channel I/O."

    override fun run() = Unit
}

/** Shared application wiring — built once per process and reused by every subcommand. */
class AppContext(
    val configPath: Path = Paths.get("ferryman", "config.toml"),
    val skillsPath: Path = Paths.get("ferryman", "skills"),
    val mcpConfigPath: Path = Paths.get(".mcp.json"),
    val logPath: Path = Paths.get("logs", "routing.jsonl"),
) {
    val config by lazy { ConfigLoader.load(configPath) }
    val providers by lazy { ProviderRegistry.fromConfig(config) }
    val logger by lazy { RoutingLogger(logPath) }

    fun mcpHost(): McpHost = McpHost.fromConfig(mcpConfigPath)

    fun orchestrator(): Orchestrator =
        Orchestrator(
            skills = SkillLoader(skillsPath),
            host = mcpHost(),
            providers = providers,
            logger = logger,
        )
}

/** Base for `ferry <group>` commands that only route to subcommands. */
abstract class GroupCommand(
    private val groupName: String,
    private val helpText: String,
) : CoreCliktCommand(groupName) {
    // false: calling the group alone (e.g. `ferry providers`) prints help and exits
    // non-zero; the group's run() is only reached when no subcommand was given.
    override val invokeWithoutSubcommand: Boolean = false

    override fun help(context: Context): String = helpText

    // No-op: when a subcommand is present, clikt runs the parent's run() first,
    // then the subcommand's. We want zero output on the parent path — the help
    // text is shown only via `--help` or when no subcommand matches (clikt errors).
    override fun run() = Unit
}

/** `ferry providers list` — enumerate configured providers as JSON (eval-harness contract). */
class ProvidersCommand : GroupCommand("providers", "List configured LLM providers")

class ProvidersListCommand : CoreCliktCommand(name = "list") {
    override fun help(context: Context): String = "Print configured providers as JSON"

    override fun run() {
        echo(AppContext().providers.describe())
    }
}

/** `ferry skills list` — enumerate skills discovered under skills/ as JSON. */
class SkillsCommand : GroupCommand("skills", "List discovered skills")

class SkillsListCommand : CoreCliktCommand(name = "list") {
    override fun help(context: Context): String = "Print discovered skills as JSON"

    override fun run() {
        echo(SkillLoader(AppContext().skillsPath).describe())
    }
}

/** `ferry tools list` — print aggregated tools from configured MCP servers. */
class ToolsCommand : GroupCommand("tools", "List tools aggregated from MCP servers")

class ToolsListCommand : CoreCliktCommand(name = "list") {
    override fun help(context: Context): String = "Print aggregated tools as JSON"

    override fun run() {
        val json = runBlocking { AppContext().mcpHost().describeTools() }
        echo(json)
    }
}

/** `ferry run --skill <name> --input "..." [--provider <id>]` — run a skill end to end. */
class RunCommand : CoreCliktCommand(name = "run") {
    override fun help(context: Context): String = "Run a skill end to end"

    private val skill by option("--skill").required()
    private val input by option("--input").required()
    private val provider by option("--provider")

    override fun run() {
        // A provider failure (timeout, exhausted 429 retries) throws from
        // runSkill. Without this catch ferry dumped a full stack trace and the
        // eval harness captured the whole trace as the case's error string.
        // Print a clean one-liner to stderr and exit non-zero instead — the
        // harness maps a non-zero exit to a per-case error and keeps going.
        val result =
            try {
                runBlocking { AppContext().orchestrator().runSkill(skill, input, provider) }
            } catch (e: RuntimeException) {
                System.err.println("ferry: ${e.message}")
                kotlin.system.exitProcess(1)
            }
        // Print a JSON metadata line to stdout FIRST, then the output text. This
        // lets invoke.py's subprocess path read the first line as structured
        // metadata (provider/model/tokens) and treat the rest as the answer.
        echo(metaLine(result))
        echo(result.output)
    }

    /**
     * Serialise routing metadata as a single `{"_meta":{...}}` JSON line for the
     * subprocess channel. Token counts are emitted only when the provider
     * reported real usage; absent keys stay null so the harness can fall back.
     */
    private fun metaLine(result: dev.openclaw.ferryman.orchestrator.SkillResult): String {
        val json = Json { encodeDefaults = true }
        val meta =
            buildJsonObject {
                put("provider", JsonPrimitive(result.provider))
                put("model", JsonPrimitive(result.model))
                result.inputTokens?.let { put("inputTokens", JsonPrimitive(it)) }
                result.outputTokens?.let { put("outputTokens", JsonPrimitive(it)) }
            }
        val wrapper = buildJsonObject { put("_meta", meta) }
        return json.encodeToString(JsonObject.serializer(), wrapper)
    }
}

/**
 * `ferry serve --port 8080` — HTTP channel sharing the same orchestrator.
 *
 * Requires a bearer-token secret for inbound auth: pass `--api-key`, or export
 * `FERRY_HTTP_TOKEN`. Without either the command errors out rather than starting
 * an unauthenticated server. `--api-key` takes precedence over the env var when
 * both are set.
 */
class ServeCommand : CoreCliktCommand(name = "serve") {
    override fun help(context: Context): String = "Run the HTTP channel"

    private val port by option("--port").int().default(8080)
    private val apiKeyOption by option("--api-key")

    override fun run() {
        // Read the token explicitly via System.getenv — the same path
        // ProviderRegistry uses — rather than Clikt's envvar= option param, so
        // the resolution is identical to how outbound provider keys are read.
        val apiToken =
            apiKeyOption ?: System.getenv("FERRY_HTTP_TOKEN")
                ?: run {
                    System.err.println(
                        "ferry serve requires an inbound auth token: pass --api-key or export FERRY_HTTP_TOKEN.",
                    )
                    kotlin.system.exitProcess(1)
                }
        runBlocking { HttpServer(AppContext().orchestrator(), port, apiToken).start() }
    }
}
