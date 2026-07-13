package dev.openclaw.ferryman.host

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class McpConfigParserTest {
    @Test
    fun `parses a stdio server with command args and env`() {
        val json =
            """
            {
              "mcpServers": {
                "filesystem": {
                  "type": "stdio",
                  "command": "npx",
                  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                  "env": { "FOO": "bar" }
                }
              }
            }
            """.trimIndent()
        val specs = McpConfigParser.parse(json)
        assertEquals(1, specs.size)
        val spec = specs.first() as ServerSpec.Stdio
        assertEquals("filesystem", spec.name)
        assertEquals("npx", spec.command)
        assertEquals(listOf("-y", "@modelcontextprotocol/server-filesystem", "/tmp"), spec.args)
        assertEquals("bar", spec.env["FOO"])
    }

    @Test
    fun `type defaults to stdio when omitted`() {
        val json = """{"mcpServers":{"x":{"command":"echo","args":[]}}}"""
        val specs = McpConfigParser.parse(json)
        assertEquals(1, specs.size)
        assertTrue(specs.first() is ServerSpec.Stdio)
    }

    @Test
    fun `non-stdio servers are skipped not crashed`() {
        val json =
            """
            {
              "mcpServers": {
                "http": {"type": "sse", "url": "http://x"},
                "stdio": {"command": "echo"}
              }
            }
            """.trimIndent()
        // Only the stdio entry survives.
        val specs = McpConfigParser.parse(json)
        assertTrue(specs.any { it.name == "stdio" })
    }

    @Test
    fun `empty input returns no specs`() {
        assertTrue(McpConfigParser.parse("").isEmpty())
        assertTrue(McpConfigParser.parse("   ").isEmpty())
    }

    @Test
    fun `no mcpServers key returns no specs`() {
        assertTrue(McpConfigParser.parse("""{"other":"value"}""").isEmpty())
    }
}
