package dev.openclaw.ferryman.koog

import dev.openclaw.ferryman.config.FerryConfig
import dev.openclaw.ferryman.config.ProviderConfig
import dev.openclaw.ferryman.config.ProviderType
import dev.openclaw.ferryman.host.ServerSpec
import dev.openclaw.ferryman.koog.FakeChatServer.Companion.answer
import dev.openclaw.ferryman.koog.FakeChatServer.Companion.contentText
import dev.openclaw.ferryman.koog.FakeChatServer.Companion.toolCall
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.skills.SkillLoader
import io.modelcontextprotocol.kotlin.sdk.client.StdioClientTransport
import io.modelcontextprotocol.kotlin.sdk.server.Server
import io.modelcontextprotocol.kotlin.sdk.server.ServerOptions
import io.modelcontextprotocol.kotlin.sdk.server.StdioServerTransport
import io.modelcontextprotocol.kotlin.sdk.types.CallToolResult
import io.modelcontextprotocol.kotlin.sdk.types.Implementation
import io.modelcontextprotocol.kotlin.sdk.types.ServerCapabilities
import io.modelcontextprotocol.kotlin.sdk.types.TextContent
import io.modelcontextprotocol.kotlin.sdk.types.ToolSchema
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.io.asSink
import kotlinx.io.asSource
import kotlinx.io.buffered
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.nio.channels.Channels
import java.nio.channels.Pipe
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.time.Duration.Companion.seconds

/**
 * Koog's MCP bridge (agents-mcp 1.3.0-beta) is built against MCP Kotlin SDK
 * 0.11.1; this project resolves 0.14.0. This test drives a real MCP server
 * (in-process, over NIO pipes) through [McpKoogToolSource] and a full
 * [KoogSkillRunner] run, so a binary incompatibility between the two SDK lines
 * fails here instead of in the eval.
 */
class KoogMcpBridgeTest {
    private lateinit var dir: Path
    private lateinit var chat: FakeChatServer
    private lateinit var baseUrl: String
    private val serverScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val calledWith = CopyOnWriteArrayList<String>()

    @BeforeTest
    fun setUp() {
        dir = Files.createTempDirectory("koog-mcp")
        val skillDir = Files.createDirectories(dir.resolve("skills/company-role-research"))
        Files.writeString(skillDir.resolve("SKILL.md"), "---\nname: company-role-research\n---\n\nResearch the company.\n")
        chat = FakeChatServer()
        baseUrl = chat.start()
    }

    @AfterTest
    fun tearDown() {
        serverScope.cancel()
        chat.close()
        dir.toFile().deleteRecursively()
    }

    /** A fetch-shaped MCP server wired to the client through two in-memory pipes. */
    private fun inProcessFetchServer(
        @Suppress("UNUSED_PARAMETER") spec: ServerSpec,
    ): Pair<StdioClientTransport, Process?> {
        val clientToServer = Pipe.open()
        val serverToClient = Pipe.open()
        val server =
            Server(
                Implementation(name = "fake-fetch", version = "0.0.1"),
                ServerOptions(capabilities = ServerCapabilities(tools = ServerCapabilities.Tools(listChanged = false))),
            )
        server.addTool(
            name = "fetch",
            description = "Fetches a URL from the internet and extracts its contents as markdown.",
            inputSchema =
                ToolSchema(
                    properties = buildJsonObject { put("url", buildJsonObject { put("type", "string") }) },
                    required = listOf("url"),
                ),
        ) { request ->
            calledWith.add(
                request.params.arguments
                    ?.get("url")
                    ?.jsonPrimitive
                    ?.content
                    .orEmpty(),
            )
            CallToolResult(content = listOf(TextContent(text = "Contents of the page:\nRemote-first. Jetpack Compose.")))
        }
        val serverTransport =
            StdioServerTransport(
                input = Channels.newInputStream(clientToServer.source()).asSource().buffered(),
                output = Channels.newOutputStream(serverToClient.sink()).asSink().buffered(),
            ) {}
        serverScope.launch { server.createSession(serverTransport) }
        val clientTransport =
            StdioClientTransport(
                input = Channels.newInputStream(serverToClient.source()).asSource().buffered(),
                output = Channels.newOutputStream(clientToServer.sink()).asSink().buffered(),
            )
        return clientTransport to null
    }

    private fun runner() =
        KoogSkillRunner(
            skills = SkillLoader(dir.resolve("skills")),
            config =
                FerryConfig(
                    providers = listOf(ProviderConfig("fake", ProviderType.OPENAI_COMPATIBLE, baseUrl, "fake-model", "FAKE_KEY")),
                    defaultProviderId = "fake",
                ),
            tools = McpKoogToolSource(listOf(ServerSpec.Stdio("fetch", "unused", emptyList()))) { inProcessFetchServer(it) },
            logger = RoutingLogger(dir.resolve("logs/routing.jsonl")),
            envLookup = { if (it == "FAKE_KEY") "test-key" else null },
        )

    @Test
    fun `an MCP tool call goes through Koog's bridge on SDK 0_14`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(toolCall("fetch", """{"url":"https://acme.example/careers"}"""))
            chat.enqueue(answer("done"))

            val result = runner().runSkill("company-role-research", "{}")

            assertEquals("done", result.output)
            assertEquals(listOf("https://acme.example/careers"), calledWith)
            // Recorded as <server>.<tool>, the hand-rolled engine's shape.
            assertEquals(listOf("fetch.fetch"), result.toolCalls)
        }

    @Test
    fun `the model sees the MCP tool under its bare name with the server's schema`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(answer("done"))

            runner().runSkill("company-role-research", "{}")

            val function =
                chat.requests
                    .single()
                    .body["tools"]!!
                    .jsonArray
                    .single()
                    .jsonObject["function"]!!
                    .jsonObject
            assertEquals("fetch", function["name"]!!.jsonPrimitive.content)
            val properties = function["parameters"]!!.jsonObject["properties"]!!.jsonObject
            assertTrue("url" in properties, "schema: $function")
        }

    @Test
    fun `Koog renders the MCP result to the model as CallToolResult JSON, not plain text`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(toolCall("fetch", """{"url":"https://acme.example/careers"}"""))
            chat.enqueue(answer("done"))

            runner().runSkill("company-role-research", "{}")

            val toolMessage =
                chat.requests[1]
                    .body["messages"]!!
                    .jsonArray
                    .last()
                    .jsonObject
            val sent = contentText(toolMessage)
            // The hand-rolled host joins the TextContent parts and sends plain text.
            // Koog's McpTool sends the CallToolResult as JSON (minus `type`/`_meta`),
            // so the page arrives JSON-escaped and wrapped: more tokens, same facts.
            assertTrue(sent.trimStart().startsWith("{"), "tool content: $sent")
            assertTrue("\"content\"" in sent && "Remote-first." in sent, "tool content: $sent")
        }
}
