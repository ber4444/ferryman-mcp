package dev.openclaw.ferryman.config

import org.tomlj.Toml
import java.nio.file.Files
import java.nio.file.Path

/**
 * Top-level configuration. Loaded from a single readable TOML file so the
 * Python eval harness can parse it with stdlib `tomllib` — see the
 * eval-harness compatibility contract.
 */
data class FerryConfig(
    val providers: List<ProviderConfig>,
    val defaultProviderId: String,
) {
    fun providerById(id: String): ProviderConfig? = providers.firstOrNull { it.id == id }
}

/**
 * One provider entry. [apiKeyEnv] is the *name* of the environment variable
 * holding the key, never the key itself — secrets live in env only.
 */
data class ProviderConfig(
    val id: String,
    val type: ProviderType,
    val baseUrl: String,
    val model: String,
    val apiKeyEnv: String,
)

enum class ProviderType { ANTHROPIC, OPENAI_COMPATIBLE }

object ConfigLoader {
    /**
     * Reads `ferryman/config.toml`. Exits the process with a clear message if the
     * file is missing — the Success command depends on it existing.
     */
    fun load(path: Path): FerryConfig {
        if (!Files.exists(path)) {
            error(
                "Config not found at $path. Expected a TOML file with a [providers.<id>] table per provider.",
            )
        }
        val toml = Toml.parse(path)
        check(toml.errors().isEmpty()) {
            toml.errors().joinToString("\n") { it.toString() }
        }

        val default =
            toml.getString("default_provider")
                ?: error("config.toml must define `default_provider`")

        val providersTable =
            toml.getTable("providers")
                ?: error("config.toml must define a [providers.<id>] table")

        val providers = mutableListOf<ProviderConfig>()
        for (key in providersTable.keySet()) {
            val entry = providersTable.getTable(key) ?: continue
            val type =
                when (entry.getString("type")) {
                    "anthropic" -> ProviderType.ANTHROPIC
                    "openai-compatible" -> ProviderType.OPENAI_COMPATIBLE
                    else ->
                        error(
                            "provider '$key': unknown type '${entry.getString("type")}'. " +
                                "Expected 'anthropic' or 'openai-compatible'.",
                        )
                }
            providers.add(
                ProviderConfig(
                    id = key,
                    type = type,
                    baseUrl =
                        entry.getString("baseUrl")
                            ?: error("provider '$key': missing baseUrl"),
                    model =
                        entry.getString("model")
                            ?: error("provider '$key': missing model"),
                    apiKeyEnv =
                        entry.getString("apiKeyEnv")
                            ?: error("provider '$key': missing apiKeyEnv"),
                ),
            )
        }

        require(providers.any { it.id == default }) {
            "default_provider '$default' is not present in [providers.*]"
        }
        return FerryConfig(providers = providers, defaultProviderId = default)
    }
}
