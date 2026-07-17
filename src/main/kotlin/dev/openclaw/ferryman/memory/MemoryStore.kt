package dev.openclaw.ferryman.memory

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant

/**
 * One persisted note. Files are named `{category}_{key}.json` under the memory
 * root so a specific memory can be looked up directly without scanning.
 *
 * Inspired by OpenClaw's Markdown-memory approach, but JSON so the CLI can emit
 * structured output and the orchestrator can reason over fields. Local-first:
 * no vector DB, no network — [search] is a plain case-insensitive substring scan.
 */
@Serializable
data class Memory(
    val category: String,
    val key: String,
    val content: String,
    val timestamp: String,
)

/**
 * File-based persistent memory. Stores [Memory]s as JSON files in [root]
 * (default `memory/` at the repo root, set via [AppContext][dev.openclaw.ferryman.AppContext]).
 *
 * The store never throws on I/O failure — memory is context enrichment, not a
 * correctness gate. Failures are surfaced on stderr and the call returns an
 * empty result, so a permissions issue on the memory dir can't break a skill run.
 */
class MemoryStore(
    private val root: Path,
) {
    private val json = Json { prettyPrint = true }

    /**
     * Writes (or overwrites) the memory for `category`/`key`. Timestamped now
     * unless [timestamp] is given (used by [ensureSeeded] to pin the seed time).
     */
    fun save(
        category: String,
        key: String,
        content: String,
        timestamp: String = Instant.now().toString(),
    ) {
        val memory = Memory(category = category, key = key, content = content, timestamp = timestamp)
        try {
            Files.createDirectories(root)
            Files.writeString(fileFor(category, key), json.encodeToString(memory))
        } catch (e: IOException) {
            // Saving memory must never break a skill run — it is context, not state.
            System.err.println("[ferryman] failed to write memory to $root: ${e.message}")
        }
    }

    /**
     * Seeds a `user-preferences`/`fit-criteria` memory the first time the store
     * is used (i.e. the `memory/` directory does not yet exist). Idempotent —
     * safe to call before every skill run. Called by the orchestrator so a fresh
     * checkout is immediately useful without a manual seed step.
     */
    fun ensureSeeded() {
        if (Files.isDirectory(root)) return
        try {
            Files.createDirectories(root)
        } catch (e: IOException) {
            System.err.println("[ferryman] failed to create memory dir $root: ${e.message}")
            return
        }
        save(
            category = USER_PREFERENCES,
            key = FIT_CRITERIA,
            content = FIT_CRITERIA_CONTENT,
            timestamp = SEEDED_AT,
        )
    }

    /** Reads one memory, or null if absent / unreadable. */
    fun load(
        category: String,
        key: String,
    ): Memory? {
        val file = fileFor(category, key)
        if (!Files.isRegularFile(file)) return null
        return read(file)
    }

    /** Every memory in [category], sorted by key for stable output. */
    fun loadAll(category: String): List<Memory> =
        all()
            .filter { it.category == category }
            .sortedBy { it.key }

    /**
     * Case-insensitive substring search across every memory's content, key, and
     * category. No ranking, no embeddings — just a scan, matching ferryman's
     * local-first ethos. Sorted by (category, key) for stable output.
     */
    fun search(query: String): List<Memory> {
        val needle = query.lowercase()
        if (needle.isEmpty()) return emptyList()
        return all()
            .filter { mem ->
                mem.content.lowercase().contains(needle) ||
                    mem.key.lowercase().contains(needle) ||
                    mem.category.lowercase().contains(needle)
            }.sortedWith(compareBy({ it.category }, { it.key }))
    }

    /** Deletes the memory for `category`/`key`; returns true if a file was removed. */
    fun forget(
        category: String,
        key: String,
    ): Boolean {
        val file = fileFor(category, key)
        return try {
            Files.deleteIfExists(file)
        } catch (e: IOException) {
            System.err.println("[ferryman] failed to forget memory $file: ${e.message}")
            false
        }
    }

    /** Every memory in the store, sorted by (category, key). */
    fun all(): List<Memory> {
        if (!Files.isDirectory(root)) return emptyList()
        val memories = mutableListOf<Memory>()
        try {
            Files
                .list(root)
                .use { stream ->
                    stream
                        .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".json") }
                        .forEach { read(it)?.let(memories::add) }
                }
        } catch (e: IOException) {
            System.err.println("[ferryman] failed to list memory dir $root: ${e.message}")
            return emptyList()
        }
        return memories.sortedWith(compareBy({ it.category }, { it.key }))
    }

    private fun read(file: Path): Memory? =
        try {
            json.decodeFromString(Files.readString(file))
        } catch (e: Exception) {
            // A corrupt or non-memory JSON file shouldn't poison the whole store.
            System.err.println("[ferryman] skipping unreadable memory $file: ${e.message}")
            null
        }

    /** `{sanitizedCategory}_{sanitizedKey}.json` — deterministic lookup, no scans. */
    private fun fileFor(
        category: String,
        key: String,
    ): Path = root.resolve("${sanitize(category)}_${sanitize(key)}.json")

    /**
     * Collapses anything that isn't `[A-Za-z0-9._-]` to `_` so a key can't escape
     * the memory dir or create subdirectories. Load/forget sanitise the same way.
     */
    private fun sanitize(value: String): String =
        value
            .trim()
            .lowercase()
            .replace(Regex("[^A-Za-z0-9._-]"), "_")
            .ifBlank { "blank" }

    companion object {
        const val USER_PREFERENCES = "user-preferences"
        const val COMPANY_RESEARCH = "company-research"
        const val FIT_CRITERIA = "fit-criteria"
        const val SEEDED_AT = "2026-07-16T00:00:00Z"
        const val FIT_CRITERIA_CONTENT =
            "Evaluating companies for a mobile-engineer candidate. Priorities: " +
                "Jetpack Compose, Kotlin Multiplatform (KMP), remote-friendly, " +
                "SF Bay Area hybrid, AI-native, mobile-first."
    }
}

/**
 * Serialises a list of memories as a compact JSON array — feeds the CLI
 * (`ferry memory list` / `search`). Top-level because it touches no instance
 * state; pure data → JSON.
 */
fun memoriesToJson(memories: List<Memory>): String {
    val arr =
        JsonArray(
            memories.map { mem ->
                JsonObject(
                    mapOf(
                        "category" to JsonPrimitive(mem.category),
                        "key" to JsonPrimitive(mem.key),
                        "content" to JsonPrimitive(mem.content),
                        "timestamp" to JsonPrimitive(mem.timestamp),
                    ),
                )
            },
        )
    return arr.toString()
}
