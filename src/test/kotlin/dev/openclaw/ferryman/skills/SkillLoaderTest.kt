package dev.openclaw.ferryman.skills

import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SkillLoaderTest {
    private val tmp = Files.createTempDirectory("ferry-skills-test")

    @AfterTest
    fun cleanup() {
        tmp.toFile().deleteRecursively()
    }

    @Test
    fun `loads skill with frontmatter and body`() {
        writeSkill("hello-repo", "Summarises a repo.", "zai-glm", "Body instructions here.")
        val skills = SkillLoader(tmp).load()
        assertEquals(1, skills.size)
        val skill = skills.first()
        assertEquals("hello-repo", skill.name)
        assertEquals("Summarises a repo.", skill.description)
        assertEquals("zai-glm", skill.providerHint)
        assertTrue(skill.body.contains("Body instructions"))
    }

    @Test
    fun `find returns skill by name`() {
        writeSkill("alpha", "First skill.", null, "alpha body")
        writeSkill("beta", "Second skill.", null, "beta body")
        val loader = SkillLoader(tmp)
        assertNotNull(loader.find("alpha"))
        assertEquals("beta", loader.find("beta")?.name)
    }

    @Test
    fun `find returns null for unknown skill`() {
        writeSkill("alpha", "First skill.", null, "body")
        assertNull(SkillLoader(tmp).find("missing"))
    }

    @Test
    fun `describe emits valid JSON`() {
        writeSkill("alpha", "First skill.", "anthropic", "body")
        val json = SkillLoader(tmp).describe()
        assertTrue(json.contains("\"name\":\"alpha\""))
        assertTrue(json.contains("\"provider\":\"anthropic\""))
    }

    @Test
    fun `empty directory returns no skills`() {
        assertTrue(SkillLoader(tmp).load().isEmpty())
    }

    @Test
    fun `quoted frontmatter values have quotes stripped`() {
        val skillDir = tmp.resolve("quoted")
        Files.createDirectories(skillDir)
        Files.writeString(
            skillDir.resolve("SKILL.md"),
            """
            ---
            name: "quoted-name"
            description: 'single-quoted description'
            ---
            Body.
            """.trimIndent(),
        )
        val skill = SkillLoader(tmp).find("quoted-name")
        assertNotNull(skill)
        assertEquals("single-quoted description", skill.description)
    }

    @Test
    fun `splitFrontmatter returns null when no frontmatter present`() {
        assertNull(splitFrontmatter("just plain markdown, no frontmatter"))
    }

    @Test
    fun `splitFrontmatter parses fenced content`() {
        val (fm, body) = splitFrontmatter("---\nname: x\n---\n# Body")!!
        assertEquals("name: x", fm)
        assertEquals("# Body", body)
    }

    private fun writeSkill(
        name: String,
        description: String,
        provider: String?,
        body: String,
    ) {
        val dir = tmp.resolve(name)
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
