package dev.openclaw.ferryman.channels

import dev.openclaw.ferryman.orchestrator.Orchestrator
import dev.openclaw.ferryman.skills.SkillLoader
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpRequestTimeoutException
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

private const val COMPANY_ROLE_SKILL = "company-role-research"
private const val PROGRESS_DELAY_MS = 30_000L
private const val POLL_TIMEOUT_SECONDS = 30

private val HELP_TEXT =
    """
    |I'm the ferryman bot. Try:
    |• research <company> [role] — get a fit summary
    |• Tell me about <company> — same thing
    |• skills — list available skills
    |• providers — list configured providers
    |• help — show this message
    """.trimMargin()

private const val USAGE_HINT =
    "Send `research <company>` (e.g. `research EarnIn`), or `help` to see commands."

/**
 * The Telegram channel — a third surface alongside the CLI (`Main.kt`) and
 * `HttpServer`. Like them it calls the *same* [Orchestrator.runSkill], so a
 * chat message and a `ferry run` hit identical code.
 *
 * No webhook infra is needed: [start] long-polls `getUpdates` and answers each
 * message with [sendMessage]. The bot token is read from the environment by
 * the CLI command and passed in here.
 */
class TelegramBot(
    private val token: String,
    private val orchestrator: Orchestrator,
    private val skills: SkillLoader,
    private val providers: dev.openclaw.ferryman.providers.ProviderRegistry,
    private val client: HttpClient = defaultClient(),
) {
    private val baseApiUrl: String get() = "https://api.telegram.org/bot$token"

    /** Polls for updates until cancelled. Safe to call inside `runBlocking`. */
    suspend fun start() {
        var offset = 0L
        while (true) {
            offset = pollOnce(offset)
        }
    }

    /**
     * One long-poll cycle. Returns the next offset (last id + 1) so [start] can
     * keep acknowledging without touching the loop's branching. Network blips and
     * transport timeouts are swallowed here — the bot retries rather than dies.
     */
    private suspend fun pollOnce(offset: Long): Long {
        val updates =
            try {
                getUpdates(offset)
            } catch (e: HttpRequestTimeoutException) {
                // Long-poll timed out at the transport layer — retry with same offset.
                return offset
            } catch (e: java.io.IOException) {
                System.err.println("[telegram] getUpdates I/O error: ${e.message}; retrying")
                delay(RETRY_BACKOFF_MS)
                return offset
            }
        var next = offset
        for (update in updates) {
            handle(update)
            // Acknowledge so Telegram never replays this update.
            next = update.updateId + 1
        }
        return next
    }

    /**
     * Handles one update: parse the text, run the matching command, send the
     * reply. Never throws — a failure becomes a friendly message to the user.
     */
    suspend fun handle(update: TelegramUpdate) {
        val message = update.message ?: return
        val chatId = message.chat?.id ?: return
        val text = message.text?.trim().orEmpty()
        if (text.isEmpty()) return

        val command = TelegramCommandParser.parse(text)
        // Research owns its own sending because it may emit a progress message
        // before the final answer; the other commands return one string we send.
        when (command) {
            is TelegramCommand.Research -> runResearch(command, chatId)
            TelegramCommand.Skills -> send(chatId, skillsList())
            TelegramCommand.Providers -> send(chatId, providersList())
            TelegramCommand.Help -> send(chatId, HELP_TEXT)
            null -> send(chatId, USAGE_HINT)
        }
    }

    /**
     * Runs the research skill. If it hasn't finished within [PROGRESS_DELAY_MS],
     * sends a hold-on message first so the user isn't staring at silence, then
     * the full result when ready. Any skill failure is mapped to a friendly reply
     * instead of crashing the poll loop.
     */
    private suspend fun runResearch(
        command: TelegramCommand.Research,
        chatId: Long,
    ) {
        val input = """{"company":"${command.company}","role":"${command.role}"}"""
        val result =
            coroutineScope {
                val job =
                    async {
                        try {
                            orchestrator.runSkill(COMPANY_ROLE_SKILL, input).output.ifBlank { "No answer produced." }
                        } catch (e: RuntimeException) {
                            "Sorry — research failed: ${e.message ?: "unknown error"}"
                        }
                    }
                delay(PROGRESS_DELAY_MS)
                // If still running after the grace window, nudge the user.
                if (job.isActive) send(chatId, "Researching ${command.company}... this may take a moment.")
                job.await()
            }
        send(chatId, result)
    }

    private fun skillsList(): String {
        val list = skills.load()
        if (list.isEmpty()) return "No skills discovered."
        val body = list.joinToString("\n") { s -> "- ${s.name}: ${s.description}" }
        return "Available skills:\n$body"
    }

    private fun providersList(): String {
        // describe() returns a compact JSON array of provider entries. Pretty-print
        // it so the chat reply is legible.
        val pretty = Json { prettyPrint = true }.parseToJsonElement(providers.describe()).toString()
        return "Configured providers:\n$pretty"
    }

    /**
     * Long-poll `getUpdates`. [offset] is the last acknowledged update id + 1
     * (Telegram's contract). `timeout` keeps the request open waiting for new
     * messages, so this loop idles instead of hammering the API.
     */
    private suspend fun getUpdates(offset: Long): List<TelegramUpdate> {
        val response: GetUpdatesResponse =
            client
                .get("$baseApiUrl/getUpdates") {
                    parameter("offset", offset)
                    parameter("timeout", POLL_TIMEOUT_SECONDS)
                }.body()
        return response.result
    }

    /**
     * Sends [text] to [chatId], splitting overlong output on newline boundaries
     * and retrying once on a transient API error (rate limit, network). Never
     * throws — a failed send is logged so the poll loop keeps running.
     */
    private suspend fun send(
        chatId: Long,
        text: String,
    ) {
        for (chunk in splitForTelegram(text)) {
            var ok = postMessage(chatId, chunk)
            if (!ok) {
                delay(RETRY_BACKOFF_MS)
                ok = postMessage(chatId, chunk)
            }
            if (!ok) System.err.println("[telegram] giving up on a chunk to chat $chatId")
        }
    }

    /** One `sendMessage` call; returns false on HTTP error or I/O failure. */
    private suspend fun postMessage(
        chatId: Long,
        text: String,
    ): Boolean {
        val payload =
            SendMessageRequest(chatId = chatId, text = text)
        return try {
            val response =
                client
                    .post("$baseApiUrl/sendMessage") {
                        contentType(ContentType.Application.Json)
                        setBody(payload)
                    }
            if (response.status.isSuccess()) return true
            val body = response.bodyAsText()
            System.err.println("[telegram] sendMessage ${response.status.value}: ${body.take(300)}")
            false
        } catch (e: java.io.IOException) {
            System.err.println("[telegram] sendMessage I/O error: ${e.message}")
            false
        }
    }

    private fun splitForTelegram(text: String): List<String> {
        if (text.length <= MAX_MESSAGE_CHARS) return listOf(text)
        val out = mutableListOf<String>()
        var remaining = text
        while (remaining.length > MAX_MESSAGE_CHARS) {
            val cut =
                remaining.lastIndexOf('\n', MAX_MESSAGE_CHARS).let { idx ->
                    if (idx > MIN_SPLIT_CHARS) idx else MAX_MESSAGE_CHARS
                }
            out += remaining.substring(0, cut)
            remaining = remaining.substring(cut).trimStart()
        }
        if (remaining.isNotEmpty()) out += remaining
        return out
    }

    companion object {
        private const val MAX_MESSAGE_CHARS = 4000
        private const val MIN_SPLIT_CHARS = 1000
        private const val RETRY_BACKOFF_MS = 5_000L
    }
}

// ---- Telegram Bot API request/response data classes. ----

@Serializable
private data class SendMessageRequest(
    @SerialName("chat_id") val chatId: Long,
    val text: String,
)

@Serializable
private data class GetUpdatesResponse(
    val result: List<TelegramUpdate> = emptyList(),
)

@Serializable
data class TelegramUpdate(
    @SerialName("update_id") val updateId: Long,
    val message: TelegramMessage? = null,
)

@Serializable
data class TelegramMessage(
    @SerialName("message_id") val messageId: Long = 0,
    val date: Long = 0,
    val chat: TelegramChat? = null,
    val text: String? = null,
)

@Serializable
data class TelegramChat(
    val id: Long = 0L,
)

/**
 * Shared Ktor client for the Telegram API: JSON content negotiation plus a
 * request timeout that comfortably exceeds the long-poll window.
 */
private fun defaultClient(): HttpClient =
    HttpClient(CIO) {
        install(ContentNegotiation) {
            json(
                Json {
                    ignoreUnknownKeys = true
                    encodeDefaults = false
                },
            )
        }
        // Long polling waits up to POLL_TIMEOUT_SECONDS server-side; give the
        // transport headroom beyond that for the reply round-trip.
        engine {
            requestTimeout = (POLL_TIMEOUT_SECONDS + 15) * 1000L
        }
    }
