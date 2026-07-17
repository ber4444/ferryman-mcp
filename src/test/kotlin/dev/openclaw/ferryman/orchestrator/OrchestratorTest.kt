package dev.openclaw.ferryman.orchestrator

import dev.openclaw.ferryman.config.FerryConfig
import dev.openclaw.ferryman.config.ProviderConfig
import dev.openclaw.ferryman.config.ProviderType
import dev.openclaw.ferryman.host.McpHost
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.memory.MemoryStore
import dev.openclaw.ferryman.providers.CompletionRequest
import dev.openclaw.ferryman.providers.CompletionResult
import dev.openclaw.ferryman.providers.LlmProvider
import dev.openclaw.ferryman.providers.ProviderRegistry
import dev.openclaw.ferryman.skills.SkillLoader
import kotlinx.coroutines.runBlocking
import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class OrchestratorTest {
    private val tmp = Files.createTempDirectory("ferry-orch-test")
    private val skillsDir = tmp.resolve("skills")
    private val memoryDir = tmp.resolve("memory")
    private val logPath = tmp.resolve("routing.jsonl")

    @AfterTest
    fun cleanup() {
        tmp.toFile().deleteRecursively()
    }

    @Test
    fun `runSkill returns error text for unknown skill`() =
        runBlocking {
            val orchestrator = buildOrchestrator(FakeProvider("zai-glm", "glm-5.2"))
            val result = orchestrator.runSkill("missing", "anything")
            assertTrue(result.output.contains("not found"))
        }

    @Test
    fun `runSkill runs skill against provider when no tools are requested`() {
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
        val provider = FakeProvider("zai-glm", "glm-5.2", response = CompletionResult(output = "summary!"))
        val orchestrator = buildOrchestrator(provider)

        val result = runBlocking { orchestrator.runSkill("hello-repo", "summarise") }

        assertEquals("summary!", result.output)
        assertEquals("zai-glm", result.provider)
        assertEquals("glm-5.2", result.model)
    }

    @Test
    fun `runSkill writes a routing log line on success`() {
        Files.createDirectories(skillsDir.resolve("hello-repo"))
        Files.writeString(
            skillsDir.resolve("hello-repo").resolve("SKILL.md"),
            "---\nname: hello-repo\ndescription: t\n---\nBody",
        )
        val orchestrator =
            buildOrchestrator(
                FakeProvider("zai-glm", "glm-5.2", CompletionResult(output = "ok")),
            )
        runBlocking { orchestrator.runSkill("hello-repo", "input") }
        assertTrue(Files.exists(logPath))
        val line = Files.readString(logPath)
        assertTrue(line.contains("\"skill\":\"hello-repo\""))
        assertTrue(line.contains("\"outcome\":\"ok\""))
    }

    @Test
    fun `runSkill writes error outcome when provider throws`() {
        Files.createDirectories(skillsDir.resolve("boom"))
        Files.writeString(
            skillsDir.resolve("boom").resolve("SKILL.md"),
            "---\nname: boom\ndescription: t\n---\nBody",
        )
        val orchestrator = buildOrchestrator(ThrowingProvider("zai-glm", "glm-5.2"))
        var threw = false
        try {
            runBlocking { orchestrator.runSkill("boom", "x") }
        } catch (e: RuntimeException) {
            threw = true
        }
        assertTrue(threw)
        val line = Files.readString(logPath)
        assertTrue(line.contains("\"outcome\":\"error\""))
    }

    @Test
    fun `runSkill seeds user preferences and prepends them to the system prompt`() {
        writeSkill("company-role-research", "You are a researcher.", null, "Skill body.")
        val provider = CapturingProvider("zai-glm", "glm-5.2", CompletionResult(output = "done"))
        val orchestrator = buildOrchestrator(provider, memory = MemoryStore(memoryDir))

        runBlocking {
            orchestrator.runSkill(
                "company-role-research",
                """{"company":"EarnIn","role":"Eng"}""",
            )
        }

        // First run seeds fit-criteria and the prompt reflects it.
        val system = provider.capturedSystem
        assertTrue(system.contains("User preferences (remembered)"))
        assertTrue(system.contains("Jetpack Compose"))
        assertTrue(system.contains("mobile-first"))
        // Skill body survives after the memory prefix.
        assertTrue(system.contains("Skill body."))
    }

    @Test
    fun `runSkill saves a company-research memory after completing`() {
        writeSkill("company-role-research", "researcher", null, "Body.")
        val provider = FakeProvider("zai-glm", "glm-5.2", CompletionResult(output = "EarnIn verdict."))
        val store = MemoryStore(memoryDir)
        val orchestrator = buildOrchestrator(provider, memory = store)

        runBlocking {
            orchestrator.runSkill(
                "company-role-research",
                """{"company":"EarnIn","role":"Eng"}""",
            )
        }

        // Keyed on the company name parsed from the input JSON.
        val saved = store.load(MemoryStore.COMPANY_RESEARCH, "EarnIn")
        assertNotNull(saved)
        assertEquals("EarnIn verdict.", saved.content)
    }

    @Test
    fun `runSkill surfaces prior research in the system prompt on the next run`() {
        writeSkill("company-role-research", "researcher", null, "Body.")
        val store = MemoryStore(memoryDir)
        // Simulate a prior run's saved result.
        store.save(MemoryStore.COMPANY_RESEARCH, "EarnIn", "EarnIn previously: Compose yes, KMP no.")
        val provider = CapturingProvider("zai-glm", "glm-5.2", CompletionResult(output = "updated"))
        val orchestrator = buildOrchestrator(provider, memory = store)

        runBlocking {
            orchestrator.runSkill(
                "company-role-research",
                """{"company":"EarnIn","role":"Eng"}""",
            )
        }

        val system = provider.capturedSystem
        assertTrue(system.contains("Prior research on EarnIn"))
        assertTrue(system.contains("Compose yes, KMP no"))
    }

    @Test
    fun `runSkill without a memory store still works as before`() {
        writeSkill("solo", "solo skill", null, "Body.")
        val provider = FakeProvider("zai-glm", "glm-5.2", CompletionResult(output = "out"))
        val orchestrator = buildOrchestrator(provider, memory = null)

        val result = runBlocking { orchestrator.runSkill("solo", "anything") }
        assertEquals("out", result.output)
    }

    private fun buildOrchestrator(
        provider: LlmProvider,
        memory: MemoryStore? = null,
    ): Orchestrator {
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
        // Inject both the fake provider factory and a stub env lookup so the
        // registry returns our provider without any real env var.
        val registry =
            ProviderRegistry(
                config = config,
                factory = { _, _ -> provider },
                envLookup = { "test-key" },
            )
        return Orchestrator(
            skills = SkillLoader(skillsDir),
            // Empty host — no real MCP servers spawned.
            host = McpHost(emptyList()),
            providers = registry,
            logger = RoutingLogger(logPath),
            memory = memory,
        )
    }

    private fun writeSkill(
        name: String,
        description: String,
        provider: String?,
        body: String,
    ) {
        val dir = skillsDir.resolve(name)
        Files.createDirectories(dir)
        val providerLine = provider?.let { "provider: $it\n" } ?: ""
        Files.writeString(
            dir.resolve("SKILL.md"),
            """
            ---
            name: $name
            description: $description
            $providerLine---
            $body
            """.trimIndent(),
        )
    }
}

private class FakeProvider(
    override val id: String,
    override val model: String,
    private val response: CompletionResult = CompletionResult(output = ""),
) : LlmProvider {
    override suspend fun complete(request: CompletionRequest): CompletionResult = response
}

/** Records the system prompt it was called with, then returns a fixed response. */
private class CapturingProvider(
    override val id: String,
    override val model: String,
    private val response: CompletionResult,
) : LlmProvider {
    var capturedSystem: String = ""
        private set

    override suspend fun complete(request: CompletionRequest): CompletionResult {
        capturedSystem = request.system
        return response
    }
}

private class ThrowingProvider(
    override val id: String,
    override val model: String,
) : LlmProvider {
    override suspend fun complete(request: CompletionRequest): CompletionResult = throw RuntimeException("provider failed")
}
