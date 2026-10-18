package dev.openclaw.ferryman.koog

import ai.koog.prompt.executor.clients.ConnectionTimeoutConfig
import ai.koog.prompt.executor.clients.LLMClient
import ai.koog.prompt.executor.clients.openai.OpenAIClientSettings
import ai.koog.prompt.executor.clients.openai.OpenAILLMClient
import ai.koog.prompt.executor.clients.retry.RetryConfig
import ai.koog.prompt.executor.clients.retry.RetryablePattern
import ai.koog.prompt.executor.clients.retry.RetryingLLMClient
import ai.koog.prompt.llm.LLMCapability
import ai.koog.prompt.llm.LLMProvider
import ai.koog.prompt.llm.LLModel
import dev.openclaw.ferryman.config.ProviderConfig
import dev.openclaw.ferryman.config.ProviderType
import kotlin.time.Duration.Companion.seconds

/**
 * Turns a `config.toml` provider entry into Koog's client + model.
 *
 * Same endpoints as the hand-rolled engine: every configured provider is an
 * OpenAI-compatible `chat/completions` endpoint, so each maps onto Koog's
 * [OpenAILLMClient] with the base URL overridden. The client is wrapped in
 * [RetryingLLMClient] configured to match the hand-rolled provider's policy
 * (3 attempts, 5 s then 10 s backoff, retry on 429/503 and timeouts) so retry
 * behaviour isn't a hidden difference between the engines.
 */
object KoogProviders {
    /** Per-request timeout. Matches the hand-rolled CIO client (Koog defaults to 900 s). */
    const val REQUEST_TIMEOUT_MS = 120_000L

    fun client(
        config: ProviderConfig,
        apiKey: String,
    ): LLMClient =
        when (config.type) {
            ProviderType.OPENAI_COMPATIBLE ->
                RetryingLLMClient(
                    OpenAILLMClient(apiKey = apiKey, settings = settings(config.baseUrl)),
                    RETRY,
                )
            ProviderType.ANTHROPIC ->
                throw UnsupportedOperationException(
                    "Provider '${config.id}': the Koog engine only wires OpenAI-compatible providers.",
                )
        }

    /**
     * Requests go to `<baseUrl>/chat/completions` — the URL the hand-rolled engine
     * posts to (KoogSkillRunnerTest pins it against a path-prefixed base URL).
     * Koog's default path is OpenAI's `v1/chat/completions`, which none of the
     * configured base URLs expect.
     */
    fun settings(baseUrl: String): OpenAIClientSettings =
        OpenAIClientSettings(
            baseUrl = baseUrl.trimEnd('/'),
            timeoutConfig = ConnectionTimeoutConfig(requestTimeoutMillis = REQUEST_TIMEOUT_MS),
            chatCompletionsPath = "chat/completions",
        )

    /**
     * A model descriptor for a non-OpenAI model served over the OpenAI wire format.
     * `OpenAIEndpoint.Completions` makes Koog use `chat/completions` rather than
     * OpenAI's Responses API, which none of the configured providers serve.
     */
    fun model(config: ProviderConfig): LLModel =
        LLModel(
            provider = LLMProvider.OpenAI,
            id = config.model,
            capabilities =
                listOf(
                    LLMCapability.Completion,
                    LLMCapability.Tools,
                    LLMCapability.OpenAIEndpoint.Completions,
                ),
        )

    private val RETRY =
        RetryConfig(
            maxAttempts = 3,
            initialDelay = 5.seconds,
            maxDelay = 10.seconds,
            backoffMultiplier = 2.0,
            // Not 0.0: Koog 1.3.0 accepts jitterFactor = 0.0 but then computes
            // Random.nextDouble(0.0, delay * 0.0), which throws "Random range is
            // empty" on the first retry. 1% only ever adds delay (≤ 100 ms here).
            jitterFactor = 0.01,
            retryablePatterns =
                listOf(
                    RetryablePattern.Status(429),
                    RetryablePattern.Status(503),
                    RetryablePattern.Keyword("request timeout"),
                    RetryablePattern.Keyword("connection timeout"),
                    RetryablePattern.Keyword("read timeout"),
                    RetryablePattern.Keyword("connection reset by peer"),
                    RetryablePattern.Keyword("connection refused"),
                ),
            retryAfterExtractor = null,
        )
}
