package dev.openclaw.ferryman.koog

import ai.koog.agents.core.agent.context.AIAgentGraphContextBase
import ai.koog.agents.core.agent.entity.AIAgentGraphStrategy
import ai.koog.agents.core.dsl.builder.strategy
import ai.koog.agents.core.dsl.extension.nodeExecuteTools
import ai.koog.agents.core.dsl.extension.nodeLLMRequest
import ai.koog.agents.core.dsl.extension.nodeLLMSendToolResults
import ai.koog.agents.core.dsl.extension.onToolCalls
import ai.koog.prompt.message.Message

/**
 * The skill's model↔tool loop as a Koog strategy graph.
 *
 * It keeps the hand-rolled orchestrator's semantics so the eval measures the
 * framework, not a different algorithm:
 *
 * - A response that carries tool calls always continues the loop, even when it
 *   also carries text.
 * - Every tool call in a response is executed (sequentially, as the hand-rolled
 *   loop does) before the model is called again.
 * - At most [maxLlmCalls] model calls per run. When the last allowed call still
 *   asks for tools, those tools run and the agent stops with the same message
 *   [dev.openclaw.ferryman.orchestrator.Orchestrator] returns.
 *
 * ```
 * start → callLLM ─tool calls→ executeTools ─budget left→ sendToolResults ─tool calls→ executeTools
 *            │                      └─budget spent→ finish("Reached tool-call limit …")
 *            └─text→ finish                                  sendToolResults ─text→ finish
 * ```
 *
 * The budget is counted from the conversation itself (assistant turns in the
 * prompt), so the graph keeps no mutable state of its own.
 */
fun boundedToolLoop(maxLlmCalls: Int): AIAgentGraphStrategy<String, String> =
    strategy("ferry-bounded-tool-loop") {
        val callLLM by nodeLLMRequest()
        val executeTools by nodeExecuteTools(parallel = false)
        val sendToolResults by nodeLLMSendToolResults()

        edge(nodeStart forwardTo callLLM)

        // Edge order matters: Koog takes the first edge whose condition holds.
        edge(callLLM forwardTo executeTools onToolCalls { true })
        edge(callLLM forwardTo nodeFinish transformed { it.textContent() })

        edge(
            executeTools forwardTo nodeFinish
                onCondition { llmCallsSoFar() >= maxLlmCalls }
                transformed { budgetExhaustedMessage(maxLlmCalls) },
        )
        edge(executeTools forwardTo sendToolResults)

        edge(sendToolResults forwardTo executeTools onToolCalls { true })
        edge(sendToolResults forwardTo nodeFinish transformed { it.textContent() })
    }

/** Same wording as the hand-rolled orchestrator, so the scorers see identical failures. */
fun budgetExhaustedMessage(maxLlmCalls: Int): String = "Reached tool-call limit ($maxLlmCalls) without a final answer."

/** Model calls made so far in this run: one assistant message per call. */
private suspend fun AIAgentGraphContextBase.llmCallsSoFar(): Int = llm.readSession { prompt.messages.count { it is Message.Assistant } }
