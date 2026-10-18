package dev.openclaw.ferryman.koog

import ai.koog.agents.core.agent.AIAgent
import ai.koog.agents.core.agent.config.AIAgentConfig
import ai.koog.agents.features.eventHandler.feature.EventHandler
import ai.koog.prompt.dsl.prompt
import ai.koog.prompt.executor.clients.LLMClient
import ai.koog.prompt.executor.llms.MultiLLMPromptExecutor
import ai.koog.prompt.message.Message
import ai.koog.prompt.params.LLMParams
import dev.openclaw.ferryman.config.FerryConfig
import dev.openclaw.ferryman.config.ProviderConfig
import dev.openclaw.ferryman.logging.RoutingDecision
import dev.openclaw.ferryman.logging.RoutingLogger
import dev.openclaw.ferryman.orchestrator.Engine
import dev.openclaw.ferryman.orchestrator.Orchestrator
import dev.openclaw.ferryman.orchestrator.SkillResult
import dev.openclaw.ferryman.orchestrator.SkillRunner
import dev.openclaw.ferryman.skills.SkillLoader
import java.time.Instant
import kotlin.coroutines.cancellation.CancellationException

/**
 * The same skill, rebuilt on JetBrains Koog: `ferry run --engine koog`.
 *
 * What is held equal with the hand-rolled [Orchestrator], so a scorecard
 * difference is the framework's doing:
 * - the prompt: the `SKILL.md` body as the system message, the caller's input as
 *   the user message;
 * - the model and endpoint: the same `config.toml` entry and provider selection
 *   (override → skill hint → default);
 * - the tools: the same `.mcp.json` server processes;
 * - the budget: [Orchestrator.MAX_ITERATIONS] model calls ([boundedToolLoop]),
 *   2048 max output tokens, provider-default temperature, the same retry policy.
 *
 * What Koog owns: the agent loop ([boundedToolLoop] runs on Koog's graph
 * runtime), the wire format ([ai.koog.prompt.executor.clients.openai.OpenAILLMClient]),
 * the MCP client and tool rendering ([McpKoogToolSource]), and the observability
 * hooks ([EventHandler]) that feed token counts and tool calls back into [SkillResult].
 */
class KoogSkillRunner(
    private val skills: SkillLoader,
    private val config: FerryConfig,
    private val tools: KoogToolSource,
    private val logger: RoutingLogger,
    private val envLookup: (String) -> String? = { System.getenv(it) },
    private val clientFactory: (ProviderConfig, String) -> LLMClient = KoogProviders::client,
) : SkillRunner {
    override suspend fun runSkill(
        name: String,
        input: String,
        providerOverride: String?,
    ): SkillResult {
        val skill =
            skills.find(name)
                ?: return SkillResult(
                    output = "Skill '$name' not found. Run `ferry skills list` to see available skills.",
                    provider = "none",
                    model = "none",
                    toolCalls = emptyList(),
                    engine = Engine.KOOG.id,
                )

        val providerId = providerOverride ?: skill.providerHint ?: config.defaultProviderId
        val providerConfig = config.providerById(providerId)
        val apiKey = providerConfig?.let { envLookup(it.apiKeyEnv) }
        if (providerConfig == null || apiKey == null) {
            return SkillResult(
                output =
                    "No provider available for skill '$name'. " +
                        "Set \${apiKeyEnv} for provider '$providerId' (see `ferry providers list`).",
                provider = providerId,
                model = "none",
                toolCalls = emptyList(),
                engine = Engine.KOOG.id,
            )
        }

        val started = System.currentTimeMillis()
        val trace = RunTrace()
        var outcome = "ok"
        var error: String? = null
        try {
            val output = runAgent(skill.body, input, providerConfig, apiKey, trace)
            return SkillResult(
                output = output,
                provider = providerConfig.id,
                model = providerConfig.model,
                toolCalls = trace.toolCalls.toList(),
                inputTokens = trace.inputTokens,
                outputTokens = trace.outputTokens,
                engine = Engine.KOOG.id,
            )
        } catch (e: CancellationException) {
            outcome = "error"
            error = "cancelled"
            throw e
        } catch (e: Exception) {
            outcome = "error"
            error = rootMessage(e)
            // The CLI turns a RuntimeException into a one-line error and a non-zero
            // exit, which the harness records as a per-case error. Koog wraps
            // provider failures a few layers deep; surface the cause, not the wrapper.
            throw RuntimeException(error, e)
        } finally {
            logger.log(
                RoutingDecision(
                    timestamp = Instant.now().toString(),
                    skill = name,
                    provider = providerConfig.id,
                    model = providerConfig.model,
                    toolCalls = trace.toolCalls.toList(),
                    inputTokens = trace.inputTokens,
                    outputTokens = trace.outputTokens,
                    latencyMs = System.currentTimeMillis() - started,
                    outcome = outcome,
                    error = error,
                    engine = Engine.KOOG.id,
                ),
            )
        }
    }

    private suspend fun runAgent(
        systemPrompt: String,
        input: String,
        providerConfig: ProviderConfig,
        apiKey: String,
        trace: RunTrace,
    ): String {
        val opened = tools.open()
        val executor = MultiLLMPromptExecutor(clientFactory(providerConfig, apiKey))
        try {
            val agent =
                AIAgent(
                    promptExecutor = executor,
                    agentConfig =
                        AIAgentConfig(
                            prompt =
                                prompt("ferry-${providerConfig.id}", LLMParams(maxTokens = MAX_OUTPUT_TOKENS)) {
                                    system(systemPrompt)
                                },
                            model = KoogProviders.model(providerConfig),
                            // Koog counts graph-node executions, not model calls. The real
                            // bound is boundedToolLoop's model-call budget; this is a backstop.
                            maxAgentIterations = MAX_GRAPH_STEPS,
                        ),
                    strategy = boundedToolLoop(Orchestrator.MAX_ITERATIONS),
                    toolRegistry = opened.registry,
                ) {
                    install(EventHandler) {
                        onLLMCallCompleted { ctx -> trace.record(ctx.response) }
                        onToolCallStarting { ctx -> trace.toolCalls.add(opened.qualifiedName(ctx.toolName)) }
                    }
                }
            try {
                return agent.run(input)
            } finally {
                agent.close()
            }
        } finally {
            // Close the HTTP client and the MCP server processes, or their
            // non-daemon threads keep the CLI's JVM alive after the answer prints.
            executor.close()
            opened.close()
        }
    }

    private fun rootMessage(e: Throwable): String {
        var cause: Throwable = e
        while (cause.cause != null && cause.cause !== cause) cause = cause.cause!!
        return cause.message ?: e.message ?: e::class.simpleName.orEmpty()
    }

    companion object {
        /** Matches the hand-rolled provider's `max_tokens`. */
        const val MAX_OUTPUT_TOKENS = 2048

        private const val MAX_GRAPH_STEPS = 50
    }
}

/** Per-run observations collected from Koog's event hooks. */
internal class RunTrace {
    val toolCalls = mutableListOf<String>()
    var inputTokens: Int? = null
        private set
    var outputTokens: Int? = null
        private set

    /** Sum real usage across model calls; stays null if the provider never reports it. */
    fun record(response: Message.Assistant?) {
        val meta = response?.metaInfo ?: return
        meta.inputTokensCount?.let { inputTokens = (inputTokens ?: 0) + it }
        meta.outputTokensCount?.let { outputTokens = (outputTokens ?: 0) + it }
    }
}
