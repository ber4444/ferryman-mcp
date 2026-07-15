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
        val result = runBlocking { AppContext().orchestrator().runSkill(skill, input, provider) }
        echo(result.output)
    }
}

/** `ferry serve --port 8080` — HTTP channel sharing the same orchestrator. */
class ServeCommand : CoreCliktCommand(name = "serve") {
    override fun help(context: Context): String = "Run the HTTP channel"

    private val port by option("--port").int().default(8080)

    override fun run() {
        runBlocking { HttpServer(AppContext().orchestrator(), port).start() }
    }
}
