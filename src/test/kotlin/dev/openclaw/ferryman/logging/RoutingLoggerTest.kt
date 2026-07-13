package dev.openclaw.ferryman.logging

import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class RoutingLoggerTest {
    private val tmp = Files.createTempDirectory("ferry-logger-test")
    private val logPath = tmp.resolve("routing.jsonl")

    @AfterTest
    fun cleanup() {
        tmp.toFile().deleteRecursively()
    }

    @Test
    fun `writes one JSON object per line`() {
        val logger = RoutingLogger(logPath)
        logger.log(sample("ok"))
        logger.log(sample("ok"))
        val lines = Files.readAllLines(logPath).filter { it.isNotBlank() }
        assertEquals(2, lines.size)
        lines.forEach { assertTrue(it.trimStart().startsWith("{")) }
    }

    @Test
    fun `log line contains skill, provider, model, latency, outcome`() {
        val logger = RoutingLogger(logPath)
        logger.log(sample("ok", skill = "hello-repo", provider = "zai-glm", model = "glm-5.2"))
        val line = Files.readString(logPath).trim()
        assertTrue(line.contains("\"skill\":\"hello-repo\""))
        assertTrue(line.contains("\"provider\":\"zai-glm\""))
        assertTrue(line.contains("\"model\":\"glm-5.2\""))
        assertTrue(line.contains("\"outcome\":\"ok\""))
        assertTrue(line.contains("\"latency_ms\""))
    }

    @Test
    fun `timed records outcome and latency on success`() {
        val logger = RoutingLogger(logPath)
        val result = logger.timed(skill = "s", provider = "p", model = "m") { "answer" }
        assertEquals("answer", result)
        val line = Files.readString(logPath).trim()
        assertTrue(line.contains("\"outcome\":\"ok\""))
    }

    @Test
    fun `timed records error outcome on exception`() {
        val logger = RoutingLogger(logPath)
        var threw = false
        try {
            logger.timed(skill = "s", provider = "p", model = "m") { error("boom") }
        } catch (e: IllegalStateException) {
            threw = true
        }
        assertTrue(threw)
        val line = Files.readString(logPath).trim()
        assertTrue(line.contains("\"outcome\":\"error\""))
        assertTrue(line.contains("\"error\":\"boom\""))
    }

    @Test
    fun `missing parent directory is created`() {
        val nested = tmp.resolve("deep").resolve("routing.jsonl")
        RoutingLogger(nested).log(sample("ok"))
        assertTrue(Files.exists(nested))
    }

    private fun sample(
        outcome: String,
        skill: String = "s",
        provider: String = "p",
        model: String = "m",
    ) = RoutingDecision(
        timestamp = "2026-07-13T00:00:00Z",
        skill = skill,
        provider = provider,
        model = model,
        latencyMs = 42,
        outcome = outcome,
    )
}
