package dev.openclaw.ferryman.providers

import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/**
 * Tests that [OpenAiCompatibleProvider] parses the `usage` block from an
 * OpenAI-compatible chat completion response and threads the real token counts
 * into [CompletionResult]. Exercises the real `complete()` → `toResult()` path
 * via a [MockEngine], so this is an end-to-end check of token propagation for
 * one provider turn.
 */
class OpenAiCompatibleProviderTest {
    @Test
    fun `usage block is parsed into CompletionResult token counts`() =
        runBlocking {
            val responseJson =
                """
                {
                  "choices": [
                    {
                      "message": {"role": "assistant", "content": "final answer"},
                      "finish_reason": "stop"
                    }
                  ],
                  "usage": {
                    "prompt_tokens": 142,
                    "completion_tokens": 37
                  }
                }
                """.trimIndent()
            val provider = providerReturning(responseJson)

            val result = provider.complete(CompletionRequest(system = "sys", user = "usr"))

            assertEquals("final answer", result.output)
            assertEquals(142, result.inputTokens)
            assertEquals(37, result.outputTokens)
        }

    @Test
    fun `token counts are null when usage block is absent`() =
        runBlocking {
            val responseJson =
                """
                {
                  "choices": [
                    {
                      "message": {"role": "assistant", "content": "no usage here"},
                      "finish_reason": "stop"
                    }
                  ]
                }
                """.trimIndent()
            val provider = providerReturning(responseJson)

            val result = provider.complete(CompletionRequest(system = "sys", user = "usr"))

            assertEquals("no usage here", result.output)
            assertNull(result.inputTokens)
            assertNull(result.outputTokens)
        }

    @Test
    fun `tool calls are still parsed alongside a usage block`() =
        runBlocking {
            val responseJson =
                """
                {
                  "choices": [
                    {
                      "message": {
                        "role": "assistant",
                        "content": null,
                        "tool_calls": [
                          {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                              "name": "fetch",
                              "arguments": "{\"q\": \"x\"}"
                            }
                          }
                        ]
                      },
                      "finish_reason": "tool_calls"
                    }
                  ],
                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}
                }
                """.trimIndent()
            val provider = providerReturning(responseJson)

            val result = provider.complete(CompletionRequest(system = "sys", user = "usr"))

            assertEquals(1, result.toolCalls.size)
            assertEquals("fetch", result.toolCalls.first().name)
            assertEquals(10, result.inputTokens)
            assertEquals(5, result.outputTokens)
        }

    private fun providerReturning(json: String): OpenAiCompatibleProvider {
        val mockEngine =
            MockEngine { _ ->
                respond(
                    json,
                    HttpStatusCode.OK,
                    headersOf("Content-Type", ContentType.Application.Json.toString()),
                )
            }
        val client =
            io.ktor.client.HttpClient(mockEngine) {
                install(ContentNegotiation) {
                    json(Json { ignoreUnknownKeys = true })
                }
            }
        return OpenAiCompatibleProvider(
            id = "test",
            model = "test-model",
            baseUrl = "http://localhost",
            apiKey = "key",
            client = client,
        )
    }
}
