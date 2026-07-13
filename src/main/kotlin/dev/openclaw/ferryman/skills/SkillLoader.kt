package dev.openclaw.ferryman.skills

import java.nio.file.Files
import java.nio.file.Path

/**
 * One loaded skill, parsed from `skills/<name>/SKILL.md` per the Agent Skills
 * open standard (a directory containing a `SKILL.md` with YAML frontmatter).
 */
data class Skill(
    val name: String,
    val description: String,
    val providerHint: String?,
    val body: String,
    val path: Path,
)

/**
 * Scans the skills directory for SKILL.md files, parses YAML frontmatter
 * (`name`, `description`, optional `provider` hint), and keeps the Markdown
 * body as the skill's instructions. This is the eval-harness contract for
 * skill discovery.
 */
class SkillLoader(
    private val root: Path,
) {
    /** Loads every discoverable skill under [root], sorted by name for stable output. */
    fun load(): List<Skill> {
        if (!Files.isDirectory(root)) return emptyList()
        return Files.list(root).use { dirs ->
            dirs
                .filter { Files.isDirectory(it) }
                .map { it.resolve("SKILL.md") }
                .filter { Files.isRegularFile(it) }
                .map { parse(it) }
                .toList()
                .filterNotNull()
                .sortedBy { it.name }
        }
    }

    fun find(name: String): Skill? = load().firstOrNull { it.name == name }

    /** JSON description of discovered skills — feeds `ferry skills list`. */
    fun describe(): String {
        val arr =
            kotlinx.serialization.json.JsonArray(
                load().map { skill ->
                    kotlinx.serialization.json.JsonObject(
                        mapOf(
                            "name" to kotlinx.serialization.json.JsonPrimitive(skill.name),
                            "description" to kotlinx.serialization.json.JsonPrimitive(skill.description),
                            "provider" to kotlinx.serialization.json.JsonPrimitive(skill.providerHint ?: ""),
                            "path" to kotlinx.serialization.json.JsonPrimitive(skill.path.toString()),
                        ),
                    )
                },
            )
        return arr.toString()
    }

    private fun parse(path: Path): Skill? {
        val text = Files.readString(path)
        val (frontmatter, body) = splitFrontmatter(text) ?: return null
        val fm = parseSimpleYaml(frontmatter)
        val name = fm["name"] ?: path.parent.fileName.toString()
        val description = fm["description"] ?: ""
        return Skill(
            name = name,
            description = description,
            providerHint = fm["provider"],
            body = body.trim(),
            path = path,
        )
    }
}

/**
 * Splits a `SKILL.md` into `(frontmatter, body)`. Frontmatter is the text
 * between `---` fences at the top of the file. Returns null if no frontmatter.
 */
internal fun splitFrontmatter(text: String): Pair<String, String>? {
    val trimmed = text.trimStart()
    if (!trimmed.startsWith("---")) return null
    val end = trimmed.indexOf("\n---", startIndex = 3)
    if (end < 0) return null
    val frontmatter = trimmed.substring(3, end).trim()
    val body = trimmed.substring(end + 4).trim()
    return frontmatter to body
}

/**
 * Minimal YAML parser for flat `key: value` frontmatter. Skills frontmatter is
 * intentionally simple (strings only, no nesting), so a full YAML dependency
 * would be overkill. Quotes are stripped if present.
 */
internal fun parseSimpleYaml(text: String): Map<String, String> {
    val result = mutableMapOf<String, String>()
    for (line in text.lines()) {
        val entry = parseYamlLine(line) ?: continue
        result[entry.first] = entry.second
    }
    return result
}

/** Parses one `key: value` line; returns null for blanks, comments, or lines without a colon. */
private fun parseYamlLine(line: String): Pair<String, String>? {
    val trimmed = line.trim()
    if (trimmed.isEmpty() || trimmed.startsWith("#")) return null
    val colon = trimmed.indexOf(':')
    if (colon < 0) return null
    val key = trimmed.substring(0, colon).trim()
    val value = trimmed.substring(colon + 1).trim()
    return key to stripQuotes(value)
}

private fun stripQuotes(value: String): String =
    when {
        value.length >= 2 && value.first() == value.last() && value.first() in "\"'" ->
            value.substring(1, value.length - 1)
        else -> value
    }
