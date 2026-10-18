package dev.openclaw.ferryman.koog

import ai.koog.agents.core.tools.SimpleTool
import ai.koog.agents.core.tools.ToolRegistry
import ai.koog.serialization.typeToken
import dev.openclaw.ferryman.config.FerryConfig
import dev.openclaw.ferryman.config.ProviderConfig
import dev.openclaw.ferryman.config.ProviderType
import dev.openclaw.ferryman.koog.FakeChatServer.Companion.answer
import dev.openclaw.ferryman.koog.FakeChatServer.Companion.contentText
import dev.openclaw.ferryman.koog.FakeChatServer.Companion.toolCall
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.skills.SkillLoader
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlin.time.Duration.Companion.seconds

class KoogSkillRunnerTest {
    private lateinit var dir: Path
    private lateinit var chat: FakeChatServer
    private lateinit var baseUrl: String
    private val fetched = mutableListOf<String>()

    private val skillBody = "You are a company-research agent. Call fetch once, then answer."
    private val input = """{"company":"Acme","role":"Senior Android Engineer"}"""

    @BeforeTest
    fun setUp() {
        dir = Files.createTempDirectory("koog-runner")
        val skillDir = Files.createDirectories(dir.resolve("skills/company-role-research"))
        Files.writeString(
            skillDir.resolve("SKILL.md"),
            "---\nname: company-role-research\ndescription: test\n---\n\n$skillBody\n",
        )
        chat = FakeChatServer()
        baseUrl = chat.start()
    }

    @AfterTest
    fun tearDown() {
        chat.close()
        dir.toFile().deleteRecursively()
    }

    private fun runner(apiKey: String? = "test-key") =
        KoogSkillRunner(
            skills = SkillLoader(dir.resolve("skills")),
            config =
                FerryConfig(
                    providers = listOf(ProviderConfig("fake", ProviderType.OPENAI_COMPATIBLE, baseUrl, "fake-model", "FAKE_KEY")),
                    defaultProviderId = "fake",
                ),
            tools = { OpenedTools(ToolRegistry { tool(FakeFetch(fetched)) }, mapOf("fetch" to "web")) },
            logger = RoutingLogger(dir.resolve("logs/routing.jsonl")),
            envLookup = { if (it == "FAKE_KEY") apiKey else null },
        )

    private fun messages(request: Int) =
        chat.requests[request]
            .body["messages"]!!
            .jsonArray
            .map { it.jsonObject }

    private fun role(message: JsonObject) = message["role"]!!.jsonPrimitive.content

    @Test
    fun `runs the tool loop and reports output, tool calls and summed tokens`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(toolCall("fetch", """{"url":"https://acme.example/careers"}""", promptTokens = 100, completionTokens = 20))
            chat.enqueue(answer("**Tech stack:** Jetpack Compose.", promptTokens = 150, completionTokens = 30))

            val result = runner().runSkill("company-role-research", input)

            assertEquals("**Tech stack:** Jetpack Compose.", result.output)
            assertEquals("koog", result.engine)
            assertEquals("fake", result.provider)
            assertEquals("fake-model", result.model)
            assertEquals(listOf("web.fetch"), result.toolCalls)
            assertEquals(250, result.inputTokens)
            assertEquals(50, result.outputTokens)
            assertEquals(listOf("https://acme.example/careers"), fetched)
            assertEquals(2, chat.requests.size)
        }

    @Test
    fun `posts to baseUrl chat completions with the configured key, like the hand-rolled engine`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(answer("done"))

            runner().runSkill("company-role-research", input)

            assertEquals("/api/coding/paas/v4/chat/completions", chat.requests.single().path)
            assertEquals("Bearer test-key", chat.requests.single().authorization)
        }

    @Test
    fun `sends the skill body as system prompt and the input as the user message`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(answer("done"))

            runner().runSkill("company-role-research", input)

            val body = chat.requests.single().body
            assertEquals("fake-model", body["model"]!!.jsonPrimitive.content)
            val sent = messages(0)
            assertEquals(listOf("system", "user"), sent.map { role(it) })
            assertEquals(skillBody, contentText(sent[0]))
            assertEquals(input, contentText(sent[1]))
            val toolNames =
                body["tools"]!!.jsonArray.map {
                    it.jsonObject["function"]!!
                        .jsonObject["name"]!!
                        .jsonPrimitive.content
                }
            assertEquals(listOf("fetch"), toolNames)
        }

    @Test
    fun `caps output at 2048 tokens, sent as max_completion_tokens`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(answer("done"))

            runner().runSkill("company-role-research", input)

            // The hand-rolled engine sends `max_tokens`; Koog's OpenAI client sends
            // the newer `max_completion_tokens`. Same budget, different field name —
            // whether a non-OpenAI endpoint honours it is the provider's call.
            val body = chat.requests.single().body
            assertEquals(2048, body["max_completion_tokens"]!!.jsonPrimitive.content.toInt())
            assertTrue("max_tokens" !in body, "Koog should not also send max_tokens: $body")
        }

    @Test
    fun `feeds the tool result back before the next model call`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(toolCall("fetch", """{"url":"https://acme.example/careers"}""", id = "call_42"))
            chat.enqueue(answer("done"))

            runner().runSkill("company-role-research", input)

            val second = messages(1)
            assertEquals(listOf("system", "user", "assistant", "tool"), second.map { role(it) })
            val assistantCall = second[2]["tool_calls"]!!.jsonArray.single().jsonObject
            assertEquals("call_42", assistantCall["id"]!!.jsonPrimitive.content)
            assertEquals("call_42", second[3]["tool_call_id"]!!.jsonPrimitive.content)
            assertTrue(FakeFetch.PAGE in contentText(second[3]), "tool message: ${second[3]}")
        }

    @Test
    fun `stops after four model calls with the hand-rolled loop's message`() =
        runTest(timeout = 60.seconds) {
            repeat(5) { chat.enqueue(toolCall("fetch", """{"url":"https://acme.example/$it"}""", id = "call_$it")) }

            val result = runner().runSkill("company-role-research", input)

            assertEquals(budgetExhaustedMessage(4), result.output)
            assertEquals(4, chat.requests.size, "a fifth model call means the budget leaked")
            // The fourth response's tool calls still run, as in the hand-rolled loop.
            assertEquals(4, fetched.size)
            assertEquals(List(4) { "web.fetch" }, result.toolCalls)
        }

    @Test
    fun `a response with text and tool calls continues the loop`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue(toolCall("fetch", """{"url":"https://acme.example"}""", text = "Let me look that up."))
            chat.enqueue(answer("final"))

            val result = runner().runSkill("company-role-research", input)

            assertEquals("final", result.output)
            assertEquals(2, chat.requests.size)
        }

    @Test
    fun `missing API key returns the hand-rolled message without calling the provider`() =
        runTest(timeout = 60.seconds) {
            val result = runner(apiKey = null).runSkill("company-role-research", input)

            assertTrue(result.output.startsWith("No provider available for skill 'company-role-research'"), result.output)
            assertEquals("koog", result.engine)
            assertTrue(chat.requests.isEmpty())
        }

    @Test
    fun `unknown skill returns the hand-rolled message`() =
        runTest(timeout = 60.seconds) {
            val result = runner().runSkill("no-such-skill", input)

            assertEquals("Skill 'no-such-skill' not found. Run `ferry skills list` to see available skills.", result.output)
            assertTrue(chat.requests.isEmpty())
        }

    @Test
    fun `a provider error surfaces as a RuntimeException and is logged with the engine`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue("""{"error":{"message":"model not found: fake-model"}}""", status = 400)

            val e = assertFailsWith<RuntimeException> { runner().runSkill("company-role-research", input) }

            assertTrue("model not found" in e.message.orEmpty() || "400" in e.message.orEmpty(), "message: ${e.message}")
            assertEquals(1, chat.requests.size, "a 400 must not be retried")
            val logged = Json.parseToJsonElement(Files.readAllLines(dir.resolve("logs/routing.jsonl")).single()).jsonObject
            assertEquals("koog", logged["engine"]!!.jsonPrimitive.content)
            assertEquals("error", logged["outcome"]!!.jsonPrimitive.content)
        }

    @Test
    fun `retries a 503 once, like the hand-rolled provider`() =
        runTest(timeout = 60.seconds) {
            chat.enqueue("""{"error":{"message":"overloaded"}}""", status = 503)
            chat.enqueue(answer("recovered"))

            val result = runner().runSkill("company-role-research", input)

            assertEquals("recovered", result.output)
            assertEquals(2, chat.requests.size)
        }
}

/** A Koog-native stand-in for the fetch MCP tool. */
class FakeFetch(
    private val calls: MutableList<String>,
) : SimpleTool<FakeFetch.Args>(
        argsType = typeToken<Args>(),
        name = "fetch",
        description = "Fetches a URL and returns its contents as text.",
    ) {
    @Serializable
    data class Args(
        val url: String,
    )

    override suspend fun execute(args: Args): String {
        calls.add(args.url)
        return PAGE
    }

    companion object {
        const val PAGE = "Acme careers: remote-first, Android in Jetpack Compose."
    }
}
