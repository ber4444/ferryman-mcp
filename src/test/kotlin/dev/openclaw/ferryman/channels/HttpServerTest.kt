package dev.openclaw.ferryman.channels

import dev.openclaw.ferryman.config.FerryConfig
import dev.openclaw.ferryman.config.ProviderConfig
import dev.openclaw.ferryman.config.ProviderType
import dev.openclaw.ferryman.host.McpHost
import dev.openclaw.ferryman.orchestrator.Orchestrator
import dev.openclaw.ferryman.providers.CompletionRequest
import dev.openclaw.ferryman.providers.CompletionResult
import dev.openclaw.ferryman.providers.LlmProvider
import dev.openclaw.ferryman.providers.ProviderRegistry
import dev.openclaw.ferryman.skills.SkillLoader
import io.ktor.client.call.body
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import io.ktor.server.testing.ApplicationTestBuilder
import io.ktor.server.testing.testApplication
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonPrimitive
import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Exercises [module] via Ktor's `testApplication` — no real listening socket.
 * Builds a real `Orchestrator` against fakes (same shape as OrchestratorTest)
 * so the request flows through the same routing + auth code a live server uses.
 */
class HttpServerTest {
    private val token = "test-bearer-token"
    private val tmp = Files.createTempDirectory("ferry-http-test")
    private val skillsDir = tmp.resolve("skills")
    private val logPath = tmp.resolve("routing.jsonl")

    @AfterTest
    fun cleanup() {
        tmp.toFile().deleteRecursively()
    }

    @Test
    fun `invoke without Authorization header is 401`() =
        testApp(buildOrchestrator(fakeProvider())) {
            val resp =
                client.post("/invoke") {
                    contentType(ContentType.Application.Json)
                    setBody("""{"skill":"hello-repo","input":"x"}""")
                }
            assertEquals(HttpStatusCode.Unauthorized, resp.status)
            assertTrue(resp.body<String>().contains("unauthorized"))
        }

    @Test
    fun `invoke with wrong bearer token is 401`() =
        testApp(buildOrchestrator(fakeProvider())) {
            val resp =
                client.post("/invoke") {
                    header("Authorization", "Bearer not-the-token")
                    contentType(ContentType.Application.Json)
                    setBody("""{"skill":"hello-repo","input":"x"}""")
                }
            assertEquals(HttpStatusCode.Unauthorized, resp.status)
        }

    @Test
    fun `invoke with correct bearer token is 200 with skill output`() {
        writeHelloRepoSkill()
        testApp(buildOrchestrator(fakeProvider(output = "summary!"))) {
            val resp =
                client.post("/invoke") {
                    header("Authorization", "Bearer $token")
                    contentType(ContentType.Application.Json)
                    setBody("""{"skill":"hello-repo","input":"summarise"}""")
                }
            assertEquals(HttpStatusCode.OK, resp.status)
            val body: kotlinx.serialization.json.JsonObject = Json.decodeFromString(resp.body<String>())
            assertEquals("summary!", body["output"]?.jsonPrimitive?.content)
        }
    }

    @Test
    fun `provider failure is mapped to 502 not 500`() {
        writeHelloRepoSkill()
        testApp(buildOrchestrator(ThrowingProvider())) {
            val resp =
                client.post("/invoke") {
                    header("Authorization", "Bearer $token")
                    contentType(ContentType.Application.Json)
                    setBody("""{"skill":"hello-repo","input":"x"}""")
                }
            assertEquals(HttpStatusCode.BadGateway, resp.status)
            assertTrue(resp.body<String>().contains("upstream provider failure"))
        }
    }

    @Test
    fun `health endpoint is reachable without auth`() =
        testApp(buildOrchestrator(fakeProvider())) {
            val resp = client.post("/health")
            assertEquals(HttpStatusCode.OK, resp.status)
            assertEquals("ok", resp.body<String>())
        }

    private fun testApp(
        orchestrator: Orchestrator,
        block: suspend ApplicationTestBuilder.() -> Unit,
    ) = testApplication {
        application { module(orchestrator, token) }
        block()
    }

    private fun writeHelloRepoSkill() {
        Files.createDirectories(skillsDir.resolve("hello-repo"))
        Files.writeString(
            skillsDir.resolve("hello-repo").resolve("SKILL.md"),
            """
            ---
            name: hello-repo
            description: test
            ---
            You are a summariser.
            """.trimIndent(),
        )
    }

    private fun buildOrchestrator(provider: LlmProvider): Orchestrator {
        val config =
            FerryConfig(
                providers =
                    listOf(
                        ProviderConfig(
                            id = provider.id,
                            type = ProviderType.OPENAI_COMPATIBLE,
                            baseUrl = "http://unused",
                            model = provider.model,
                            apiKeyEnv = "TEST_KEY",
                        ),
                    ),
                defaultProviderId = provider.id,
            )
        val registry =
            ProviderRegistry(
                config = config,
                factory = { _, _ -> provider },
                envLookup = { "test-key" },
            )
        return Orchestrator(
            skills = SkillLoader(skillsDir),
            host = McpHost(emptyList()),
            providers = registry,
            logger =
                dev.openclaw.ferryman.logging
                    .RoutingLogger(logPath),
        )
    }
}

private fun fakeProvider(output: String = ""): LlmProvider =
    object : LlmProvider {
        override val id = "zai-glm"
        override val model = "glm-5.2"

        override suspend fun complete(request: CompletionRequest): CompletionResult = CompletionResult(output = output)
    }

private class ThrowingProvider : LlmProvider {
    override val id = "zai-glm"
    override val model = "glm-5.2"

    override suspend fun complete(request: CompletionRequest): CompletionResult = throw RuntimeException("provider failed")
}
