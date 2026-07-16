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
    testImplementation("io.ktor:ktor-server-sse:3.4.3")
    testImplementation("io.ktor:ktor-client-mock:3.4.3")

    // Serialization + coroutines. Versions verified on Maven Central.
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.10.2")

    // CLI parsing.
    implementation("com.github.ajalt.clikt:clikt:5.0.3")

    // TOML config parsing (readable by both Kotlin here and Python's tomllib).
    implementation("org.tomlj:tomlj:1.1.1")

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
