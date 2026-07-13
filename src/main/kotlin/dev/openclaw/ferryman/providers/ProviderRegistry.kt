package dev.openclaw.ferryman.providers

import dev.openclaw.ferryman.config.FerryConfig
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Holds the configured providers, lazily instantiated. A provider is only live
 * when its referenced env var is set; otherwise it is reported as `apiKeySet=false`
 * and cannot be selected. This is the eval-harness enumeration contract.
 */
class ProviderRegistry(
    private val config: FerryConfig,
    private val factory: (dev.openclaw.ferryman.config.ProviderConfig, String) -> LlmProvider = LlmProviderFactory::create,
    private val envLookup: (String) -> String? = { System.getenv(it) },
) {
    /** Returns the live provider for [id], or null if the env var is unset. */
    fun get(id: String): LlmProvider? {
        val cfg = config.providerById(id) ?: return null
        val key = envLookup(cfg.apiKeyEnv) ?: return null
        return factory(cfg, key)
    }

    /** The default provider, or null if its env var isn't set. */
    fun default(): LlmProvider? = get(config.defaultProviderId)

    /** JSON enumeration of configured providers — feeds `ferry providers list`. */
    fun describe(): String {
        val arr =
            JsonArray(
                config.providers.map { p ->
                    JsonObject(
                        mapOf(
                            "id" to JsonPrimitive(p.id),
                            "type" to
                                JsonPrimitive(
                                    p.type.name
                                        .lowercase()
                                        .replace('_', '-'),
                                ),
                            "model" to JsonPrimitive(p.model),
                            "baseUrl" to JsonPrimitive(p.baseUrl),
                            "apiKeyEnv" to JsonPrimitive(p.apiKeyEnv),
                            "apiKeySet" to JsonPrimitive(envLookup(p.apiKeyEnv) != null),
                            "isDefault" to JsonPrimitive(p.id == config.defaultProviderId),
                        ),
                    )
                },
            )
        return arr.toString()
    }

    companion object {
        fun fromConfig(config: FerryConfig): ProviderRegistry = ProviderRegistry(config)
    }
}
