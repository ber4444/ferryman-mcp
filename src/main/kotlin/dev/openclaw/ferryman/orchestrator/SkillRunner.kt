package dev.openclaw.ferryman.orchestrator

/**
 * Anything that can run a skill end to end and hand back a [SkillResult].
 *
 * Two implementations exist so the eval harness can score the same skill on two
 * agent hosts: the hand-rolled [Orchestrator] and the Koog rebuild
 * ([dev.openclaw.ferryman.koog.KoogSkillRunner]). Both channels (CLI, HTTP) pick
 * one by [Engine]; the harness passes `--engine` / `"engine"` through.
 */
interface SkillRunner {
    suspend fun runSkill(
        name: String,
        input: String,
        providerOverride: String? = null,
    ): SkillResult
}

/** Which agent host runs the skill. [id] is the wire name used by the CLI, HTTP and the harness. */
enum class Engine(
    val id: String,
) {
    /** The hand-rolled model↔tool loop in [Orchestrator]. The default. */
    FERRYMAN("ferryman"),

    /** The same skill rebuilt on JetBrains Koog's agent runtime. */
    KOOG("koog"),
    ;

    companion object {
        fun fromId(id: String?): Engine? = if (id == null) FERRYMAN else entries.firstOrNull { it.id == id }
    }
}
