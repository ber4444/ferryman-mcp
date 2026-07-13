package dev.openclaw.ferryman.config

import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ConfigLoaderTest {
    private val tmp = Files.createTempDirectory("ferry-config-test")

    @AfterTest
    fun cleanup() {
        tmp.toFile().deleteRecursively()
    }

    @Test
    fun `parses two providers and a default`() {
        val path = tmp.resolve("config.toml")
        Files.writeString(
            path,
            """
            default_provider = "zai-glm"

            [providers.anthropic]
            type = "anthropic"
            baseUrl = "https://api.anthropic.com/v1"
            model = "claude-sonnet-4-5"
            apiKeyEnv = "ANTHROPIC_API_KEY"

            [providers.zai-glm]
            type = "openai-compatible"
            baseUrl = "https://api.z.ai/api/coding/paas/v4"
            model = "glm-5.2"
            apiKeyEnv = "ZAI_API_KEY"
            """.trimIndent(),
        )

        val config = ConfigLoader.load(path)

        assertEquals(2, config.providers.size)
        assertEquals("zai-glm", config.defaultProviderId)
        assertEquals(ProviderType.ANTHROPIC, config.providerById("anthropic")?.type)
        assertEquals(ProviderType.OPENAI_COMPATIBLE, config.providerById("zai-glm")?.type)
        assertEquals("glm-5.2", config.providerById("zai-glm")?.model)
    }

    @Test
    fun `rejects default_provider not in providers`() {
        val path = tmp.resolve("config.toml")
        Files.writeString(
            path,
            """
            default_provider = "missing"

            [providers.anthropic]
            type = "anthropic"
            baseUrl = "x"
            model = "y"
            apiKeyEnv = "Z"
            """.trimIndent(),
        )

        assertFailsWith<IllegalArgumentException> { ConfigLoader.load(path) }
    }

    @Test
    fun `rejects unknown provider type`() {
        val path = tmp.resolve("config.toml")
        Files.writeString(
            path,
            """
            default_provider = "x"

            [providers.x]
            type = "magic"
            baseUrl = "x"
            model = "y"
            apiKeyEnv = "Z"
            """.trimIndent(),
        )

        assertFailsWith<IllegalStateException> { ConfigLoader.load(path) }
    }

    @Test
    fun `providerById returns null for unknown id`() {
        val path = tmp.resolve("config.toml")
        Files.writeString(
            path,
            """
            default_provider = "a"

            [providers.a]
            type = "anthropic"
            baseUrl = "x"
            model = "y"
            apiKeyEnv = "Z"
            """.trimIndent(),
        )

        val config = ConfigLoader.load(path)
        assertNull(config.providerById("nope"))
        assertNotNull(config.providerById("a"))
    }

    @Test
    fun `missing file throws`() {
        assertFailsWith<IllegalStateException> {
            ConfigLoader.load(tmp.resolve("absent.toml"))
        }
    }

    @Test
    fun `apiKeyEnv is a name, not a value`() {
        val path = tmp.resolve("config.toml")
        Files.writeString(
            path,
            """
            default_provider = "a"

            [providers.a]
            type = "anthropic"
            baseUrl = "x"
            model = "y"
            apiKeyEnv = "MY_KEY_VAR"
            """.trimIndent(),
        )

        val config = ConfigLoader.load(path)
        assertTrue(config.providers.all { it.apiKeyEnv == "MY_KEY_VAR" })
    }
}
