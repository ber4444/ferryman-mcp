package dev.openclaw.ferryman.channels

import dev.openclaw.ferryman.orchestrator.Engine
import dev.openclaw.ferryman.orchestrator.SkillResult
import dev.openclaw.ferryman.orchestrator.SkillRunner
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import io.ktor.server.testing.testApplication
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals

class HttpServerTest {
    private val runners =
        Engine.entries.associateWith { engine ->
            object : SkillRunner {
                override suspend fun runSkill(
                    name: String,
                    input: String,
                    providerOverride: String?,
                ) = SkillResult(output = "ran", provider = "p", model = "m", toolCalls = emptyList(), engine = engine.id)
            }
        }

    private fun invoke(body: String): Pair<HttpStatusCode, String> {
        var result: Pair<HttpStatusCode, String>? = null
        testApplication {
            application { module(runners) }
            val response =
                client.post("/invoke") {
                    contentType(ContentType.Application.Json)
                    setBody(body)
                }
            result = response.status to response.bodyAsText()
        }
        return result!!
    }

    private fun engineOf(json: String) =
        Json
            .parseToJsonElement(json)
            .jsonObject["engine"]!!
            .jsonPrimitive.content

    @Test
    fun `no engine field runs the hand-rolled orchestrator`() {
        val (status, body) = invoke("""{"skill":"s","input":"{}"}""")
        assertEquals(HttpStatusCode.OK, status)
        assertEquals("ferryman", engineOf(body))
    }

    @Test
    fun `engine koog routes to the Koog runner and echoes it back`() {
        val (status, body) = invoke("""{"skill":"s","input":"{}","engine":"koog"}""")
        assertEquals(HttpStatusCode.OK, status)
        assertEquals("koog", engineOf(body))
    }

    @Test
    fun `an unknown engine is a 400, not a silent fallback`() {
        assertEquals(HttpStatusCode.BadRequest, invoke("""{"skill":"s","input":"{}","engine":"langchain"}""").first)
    }
}
