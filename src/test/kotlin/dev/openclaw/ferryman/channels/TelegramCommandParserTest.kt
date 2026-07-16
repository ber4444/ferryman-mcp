package dev.openclaw.ferryman.channels

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The parser is the only Telegram-channel logic with real branching, so it gets
 * its own unit test. Network/HTTP behaviour is covered by the shared orchestrator
 * tests — the bot just forwards to `runSkill`.
 */
class TelegramCommandParserTest {
    @Test
    fun `research with company only uses default role`() {
        val command = TelegramCommandParser.parse("research EarnIn")

        assertTrue(command is TelegramCommand.Research)
        assertEquals("EarnIn", command.company)
        assertEquals("Senior Engineer", command.role)
    }

    @Test
    fun `research with company and role keeps the full role`() {
        val command = TelegramCommandParser.parse("research EarnIn Senior Mobile Engineer")

        assertTrue(command is TelegramCommand.Research)
        assertEquals("EarnIn", command.company)
        assertEquals("Senior Mobile Engineer", command.role)
    }

    @Test
    fun `research with no company returns null`() {
        assertNull(TelegramCommandParser.parse("research"))
    }

    @Test
    fun `Tell me about Stripe parses as research on the company`() {
        val command = TelegramCommandParser.parse("Tell me about Stripe")

        assertTrue(command is TelegramCommand.Research)
        assertEquals("Stripe", command.company)
        assertEquals("Senior Engineer", command.role)
    }

    @Test
    fun `help parses as Help`() {
        assertEquals(TelegramCommand.Help, TelegramCommandParser.parse("help"))
    }

    @Test
    fun `skills parses as Skills`() {
        assertEquals(TelegramCommand.Skills, TelegramCommandParser.parse("skills"))
    }

    @Test
    fun `providers parses as Providers`() {
        assertEquals(TelegramCommand.Providers, TelegramCommandParser.parse("providers"))
    }

    @Test
    fun `slash-prefixed commands also match`() {
        assertEquals(TelegramCommand.Help, TelegramCommandParser.parse("/help"))
        assertEquals(TelegramCommand.Skills, TelegramCommandParser.parse("/skills"))
        assertEquals(TelegramCommand.Providers, TelegramCommandParser.parse("/providers"))
    }

    @Test
    fun `bare company name is treated as research`() {
        val command = TelegramCommandParser.parse("Stripe")

        assertTrue(command is TelegramCommand.Research)
        assertEquals("Stripe", command.company)
    }

    @Test
    fun `role defaults when trailing words do not look like a title`() {
        val command = TelegramCommandParser.parse("research EarnIn something random")

        assertTrue(command is TelegramCommand.Research)
        assertEquals("EarnIn", command.company)
        assertEquals("Senior Engineer", command.role)
    }

    @Test
    fun `blank input returns null`() {
        assertNull(TelegramCommandParser.parse(""))
        assertNull(TelegramCommandParser.parse("   "))
    }
}
