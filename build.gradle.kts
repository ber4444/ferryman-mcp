import org.jlleitschuh.gradle.ktlint.reporter.ReporterType

plugins {
    application
    kotlin("jvm") version "2.3.21"
    kotlin("plugin.serialization") version "2.3.21"
    id("org.jlleitschuh.gradle.ktlint") version "14.2.0"
    id("io.gitlab.arturbosch.detekt") version "1.23.8"
}

group = "dev.openclaw"
version = "0.1.0"

application {
    mainClass.set("dev.openclaw.ferryman.MainKt")
    applicationName = "ferry"
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    // MCP host (client side of the protocol). Verify version at:
    // https://github.com/modelcontextprotocol/kotlin-sdk/releases
    implementation("io.modelcontextprotocol:kotlin-sdk-client:0.14.0")

    // kotlinx-io — the SDK reads kotlinx.io Source/Sink; the JVM variant exposes
    // InputStream.asSource() / OutputStream.asSink() (JvmCoreKt) which we use to
    // bridge the spawned server's process streams.
    implementation("org.jetbrains.kotlinx:kotlinx-io-core:0.9.1")

    // HTTP client + server. 3.4.3 is the version the MCP SDK 0.14.0 is built
    // against; matching it avoids forcing two Ktor lines onto the classpath.
    implementation("io.ktor:ktor-client-cio:3.4.3")
    implementation("io.ktor:ktor-client-content-negotiation:3.4.3")
    implementation("io.ktor:ktor-serialization-kotlinx-json:3.4.3")
    implementation("io.ktor:ktor-server-core:3.4.3")
    implementation("io.ktor:ktor-server-cio:3.4.3")
    implementation("io.ktor:ktor-server-content-negotiation:3.4.3")
    testImplementation("io.ktor:ktor-server-test-host:3.4.3")
    testImplementation("io.ktor:ktor-client-mock:3.4.3")

    // Serialization + coroutines. Versions verified on Maven Central.
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.10.2")

    // CLI parsing.
    implementation("com.github.ajalt.clikt:clikt:5.0.3")

    // TOML config parsing (readable by both Kotlin here and Python's tomllib).
    implementation("org.tomlj:tomlj:1.1.1")

    // Koog — the second engine (`ferry run --engine koog`). Only the modules the
    // rebuilt skill uses, not the `koog-agents` umbrella (which pulls Bedrock,
    // OpenTelemetry, RAG, …). Core, OpenAI client and event handler are stable
    // 1.3.0; the MCP bridge only ships as 1.3.0-beta, outside Koog's 1.x
    // stability guarantee. Verify at https://repo.maven.apache.org/maven2/ai/koog/
    implementation("ai.koog:agents-core:1.3.0")
    implementation("ai.koog:agents-features-event-handler:1.3.0")
    implementation("ai.koog:prompt-executor-openai-client:1.3.0")
    implementation("ai.koog:http-client-ktor:1.3.0")
    implementation("ai.koog:agents-mcp:1.3.0-beta") {
        // agents-mcp drags in every Koog LLM client (Bedrock → the AWS SDK, …).
        // The MCP bridge doesn't use them; the OpenAI client above is enough.
        exclude(group = "ai.koog", module = "prompt-executor-llms-all")
    }
    // Note: agents-mcp is built against MCP Kotlin SDK 0.11.1; Gradle resolves it
    // up to the 0.14.0 the host uses. KoogMcpBridgeTest drives an in-process MCP
    // server through Koog's bridge to prove it still links against 0.14.0.

    // In-process MCP server for the Koog bridge test (no npx/network in CI).
    testImplementation("io.modelcontextprotocol:kotlin-sdk-server:0.14.0")

    testImplementation(kotlin("test"))
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}

tasks.test {
    useJUnitPlatform()
}

ktlint {
    version.set("1.5.0")
    reporters {
        reporter(ReporterType.PLAIN)
    }
}

detekt {
    buildUponDefaultConfig = true
    config.setFrom("$projectDir/detekt.yml")
}

tasks.named("check") {
    dependsOn("ktlintCheck", "detekt")
}
