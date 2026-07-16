package dev.openclaw.ferryman

import com.github.ajalt.clikt.core.Context
import com.github.ajalt.clikt.core.CoreCliktCommand
import com.github.ajalt.clikt.core.main
import com.github.ajalt.clikt.core.subcommands
import com.github.ajalt.clikt.parameters.arguments.argument
import com.github.ajalt.clikt.parameters.arguments.multiple
import com.github.ajalt.clikt.parameters.options.default
import com.github.ajalt.clikt.parameters.options.option
import com.github.ajalt.clikt.parameters.options.required
import com.github.ajalt.clikt.parameters.types.int
import dev.openclaw.ferryman.channels.HttpServer
import dev.openclaw.ferryman.config.ConfigLoader
import dev.openclaw.ferryman.host.McpHost
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.memory.MemoryStore
import dev.openclaw.ferryman.memory.memoriesToJson
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
            MemoryCommand().subcommands(
                MemoryListCommand(),
                MemorySaveCommand(),
                MemoryForgetCommand(),
                MemorySearchCommand(),
            ),
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
    val memoryPath: Path = Paths.get("memory"),
) {
    val config by lazy { ConfigLoader.load(configPath) }
    val providers by lazy { ProviderRegistry.fromConfig(config) }
    val logger by lazy { RoutingLogger(logPath) }
    val memoryStore by lazy { MemoryStore(memoryPath) }

    fun mcpHost(): McpHost = McpHost.fromConfig(mcpConfigPath)

    fun orchestrator(): Orchestrator =
        Orchestrator(
            skills = SkillLoader(skillsPath),
            host = mcpHost(),
            providers = providers,
            logger = logger,
            memory = memoryStore,
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

/** `ferry memory ...` — inspect and manage persistent agent memory. */
class MemoryCommand : GroupCommand("memory", "Persistent agent memory across skill runs")

/** `ferry memory list` — print every stored memory as a JSON array. */
class MemoryListCommand : CoreCliktCommand(name = "list") {
    override fun help(context: Context): String = "List all stored memories as JSON"

    override fun run() {
        val store = AppContext().memoryStore
        echo(memoriesToJson(store.all()))
    }
}

/** `ferry memory save --category <cat> --key <key> --content "..."` — store a memory. */
class MemorySaveCommand : CoreCliktCommand(name = "save") {
    override fun help(context: Context): String = "Save a memory (category, key, content)"

    private val category by option("--category").required()
    private val key by option("--key").required()
    private val content by option("--content").required()

    override fun run() {
        val store = AppContext().memoryStore
        store.save(category, key, content)
        val saved = store.load(category, key)
        echo(memoriesToJson(listOfNotNull(saved)))
    }
}

/** `ferry memory forget --category <cat> --key <key>` — delete a memory. */
class MemoryForgetCommand : CoreCliktCommand(name = "forget") {
    override fun help(context: Context): String = "Delete a memory (category, key)"

    private val category by option("--category").required()
    private val key by option("--key").required()

    override fun run() {
        val removed = AppContext().memoryStore.forget(category, key)
        echo("""{"category":"$category","key":"$key","forgotten":$removed}""")
    }
}

/** `ferry memory search "query"` — keyword search across all memories. */
class MemorySearchCommand : CoreCliktCommand(name = "search") {
    override fun help(context: Context): String = "Search memories by keyword (case-insensitive)"

    // Positional so `ferry memory search "EarnIn"` works as documented.
    private val query by argument().multiple()

    override fun run() {
        val store = AppContext().memoryStore
        echo(memoriesToJson(store.search(query.joinToString(" "))))
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
