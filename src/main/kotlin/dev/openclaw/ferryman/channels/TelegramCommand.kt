package dev.openclaw.ferryman.channels

/**
 * The parsed intent of one incoming Telegram message. Pure data so the parser
 * stays trivially testable without touching the network or orchestrator.
 */
sealed class TelegramCommand {
    /** Run the company-role-research skill for [company] and [role]. */
    data class Research(
        val company: String,
        val role: String,
    ) : TelegramCommand()

    /** `ferry skills list` — enumerate discovered skills as text. */
    data object Skills : TelegramCommand()

    /** `ferry providers list` — enumerate configured providers as text. */
    data object Providers : TelegramCommand()

    /** Show the supported commands. */
    data object Help : TelegramCommand()
}

/**
 * Turns a raw Telegram message into a [TelegramCommand]. The grammar is
 * forgiving on purpose: a chat user won't type a CLI flag, so we match natural
 * phrases ("research EarnIn", "Tell me about Stripe") rather than exact syntax.
 *
 * Role parsing: the first token after the company is treated as the role only
 * when the remainder reads like a title ("Senior Mobile Engineer"). A lone
 * company name falls back to [DEFAULT_ROLE] so the skill always gets both fields.
 */
object TelegramCommandParser {
    private const val RESEARCH_PREFIX = "research"
    private val ABOUT_PREFIXES =
        listOf("tell me about", "about", "what about", "who is", "who are")
    private const val DEFAULT_ROLE = "Senior Engineer"

    /**
     * Words that, when they follow the company, signal the user is naming a
     * role rather than continuing the company name. Kept broad — false positives
     * just produce a more specific role, never a crash.
     */
    private val ROLE_KEYWORDS =
        setOf(
            "senior",
            "junior",
            "staff",
            "principal",
            "lead",
            "manager",
            "director",
            "engineer",
            "developer",
            "mobile",
            "android",
            "ios",
            "frontend",
            "backend",
            "fullstack",
            "full-stack",
            "architect",
            "intern",
            "vp",
            "head",
            "chief",
            "sr",
            "jr",
        )

    fun parse(raw: String): TelegramCommand? {
        val text = raw.trim()
        if (text.isEmpty()) return null
        val lower = text.lowercase()

        // Exact single-word commands first so "skills" never reads as a company.
        return when (lower) {
            "help", "start", "/help", "/start" -> TelegramCommand.Help
            "skills", "/skills" -> TelegramCommand.Skills
            "providers", "/providers" -> TelegramCommand.Providers
            else -> parseResearch(text, lower)
        }
    }

    private fun parseResearch(
        text: String,
        lower: String,
    ): TelegramCommand? {
        val (body, fallbackCompany) = stripPrefix(text, lower)
        // No prefix matched and no trailing word → not a recognisable request.
        if (body == null) return researchFromCompany(fallbackCompany)

        val tokens = body.split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (tokens.isEmpty()) {
            // "research" alone — no company given.
            return null
        }
        val company = tokens.first()
        val rest = tokens.drop(1)
        val role = roleFromRest(rest)
        return TelegramCommand.Research(company = company, role = role)
    }

    /**
     * Strips the leading command phrase, returning the remainder (to be split
     * into company + role) and the original text (as a fallback company for the
     * bare-name path). Prefix matching is case-insensitive; we slice the *original*
     * text by character offset so the company name keeps its capitalisation.
     *
     * Returns `""` (empty remainder) — not `null` — when a prefix matches but has
     * nothing after it (e.g. bare "research"); [parseResearch] turns that into null.
     */
    private fun stripPrefix(
        text: String,
        lower: String,
    ): Pair<String?, String> {
        val candidates =
            buildList {
                val isResearch = lower == RESEARCH_PREFIX || lower.startsWith("$RESEARCH_PREFIX ")
                if (isResearch) add(RESEARCH_PREFIX)
                addAll(ABOUT_PREFIXES)
            }
        for (prefix in candidates) {
            val withSpace = "$prefix "
            if (lower == prefix) return "" to text
            if (lower.startsWith(withSpace)) {
                val remainder = text.substring(withSpace.length).trim()
                return remainder to text
            }
        }
        // No prefix matched: treat the whole message as a company name.
        return null to text
    }

    private fun researchFromCompany(company: String): TelegramCommand? {
        val trimmed = company.trim().trimEnd('.', '?', '!')
        if (trimmed.isEmpty()) return null
        // A lone command word with no prefix shouldn't be misread as a company.
        if (trimmed.lowercase() in setOf("research", "help", "skills", "providers")) return null
        return TelegramCommand.Research(company = trimmed, role = DEFAULT_ROLE)
    }

    /**
     * Everything after the company becomes the role when it looks like a title;
     * otherwise the default. We look at the first trailing word: if it is a known
     * seniority/discipline term the rest is treated as a full role string.
     */
    private fun roleFromRest(rest: List<String>): String {
        if (rest.isEmpty()) return DEFAULT_ROLE
        val firstWord = rest.first().lowercase().trimEnd('.', ',')
        val firstIsRole = firstWord in ROLE_KEYWORDS
        return if (firstIsRole) rest.joinToString(" ") else DEFAULT_ROLE
    }
}
