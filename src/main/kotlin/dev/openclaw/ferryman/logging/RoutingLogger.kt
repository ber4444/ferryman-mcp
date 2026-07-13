package dev.openclaw.ferryman.logging

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.time.Instant

/**
 * One structured routing decision, appended to `logs/routing.jsonl` per skill run.
 * The eval harness reads this file to see which skill ran on which provider, how
 * long it took, and which tools it called.
 */
@Serializable
data class RoutingDecision(
    val timestamp: String,
    val skill: String,
    val provider: String,
    val model: String,
    @SerialName("tool_calls") val toolCalls: List<String> = emptyList(),
    @SerialName("input_tokens") val inputTokens: Int? = null,
    @SerialName("output_tokens") val outputTokens: Int? = null,
    @SerialName("latency_ms") val latencyMs: Long,
    val outcome: String,
    val error: String? = null,
)

/**
 * Appends [RoutingDecision]s as JSON Lines to [path]. One object per line, no
 * enclosing array — the canonical JSONL shape so the file is streamable and
 * append-safe.
 */
class RoutingLogger(
    private val path: Path,
) {
    private val json = Json { encodeDefaults = false }

    fun log(decision: RoutingDecision) {
        try {
            Files.createDirectories(path.parent ?: return)
            val line = json.encodeToString(decision) + "\n"
            Files.writeString(
                path,
                line,
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND,
            )
        } catch (e: IOException) {
            // Logging must never break a skill run — the routing log is observability,
            // not a correctness gate. Surface the failure on stderr and continue.
            System.err.println("[ferryman] failed to write routing log to $path: ${e.message}")
        }
    }

    /** Convenience: records a decision with a measured latency around [block]. */
    inline fun <T> timed(
        skill: String,
        provider: String,
        model: String,
        crossinline block: () -> T,
    ): T {
        val started = System.currentTimeMillis()
        var outcome = "ok"
        var err: String? = null
        try {
            return block()
        } catch (e: Throwable) {
            outcome = "error"
            err = e.message
            throw e
        } finally {
            log(
                RoutingDecision(
                    timestamp = Instant.now().toString(),
                    skill = skill,
                    provider = provider,
                    model = model,
                    latencyMs = System.currentTimeMillis() - started,
                    outcome = outcome,
                    error = err,
                ),
            )
        }
    }
}
