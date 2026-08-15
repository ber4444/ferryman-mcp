"""
Deterministic chess scorers for the chess-opening-coach skill.

Unlike the company-research rule scorers (positive-presence + citation checks),
these are **objective exact-match** scorers: the golden set (ChessQA-derived)
carries a single `correctAnswer` per case, and the model's answer is extracted
via the `FINAL ANSWER:` contract and compared. No judge, no substring fuzziness
— this is the maturity floor the chess-app repo's concept-substring scorer lacks.

Two answer formats, selected by the case's `answerFormat`:

  * "uci"             — a best move in UCI notation (e.g. "e2e4", "c2b1q").
                        Normalized to lowercase before comparison.
  * "centipawn-band"  — one of {-400, -200, 0, 200, 400}. Exact string match.

Plus a `forbiddenPhrases` honesty gate, ported verbatim from the chess app's
MoveCoachResponseValidator / PositionChatValidator: mentioning engine depth,
ELO, or unsupported certainty ("forced mate") is a hard fail regardless of
whether the final answer is correct.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Reused from rule_scorers so both skills share one verdict shape and the
# scorecard renderer needs no per-skill branching.
from eval_harness.rule_scorers import ScoreResult


# --- the FINAL ANSWER extraction contract (ported from ChessQA) -------------
#
# Source: CSSLab/chessqa-benchmark scripts/chessqa_prompt_utils.py, verbatim
# regex. The skill prompt instructs the model to end with a single line
# "FINAL ANSWER: <answer>". When multiple matches exist, the LAST one wins
# (a model that corrects itself mid-response). Fallback: a LaTeX \boxed{} form.
# Ported, not reimplemented, so our scoring matches ChessQA's published method.
_FINAL_ANSWER_PATTERN = re.compile(r"FINAL ANSWER:\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL)
_BOXED_PATTERN = re.compile(r"[Tt]he\s+final\s+answer\s+is\s+\$?\\boxed\{([^}]+)\}\$?")


def extract_final_answer(text: str) -> tuple[str, bool]:
    """
    Extract the model's final answer per the ChessQA contract.

    Returns ``(answer, found)``. ``found`` is False when neither the
    ``FINAL ANSWER:`` marker nor a ``\\boxed{}`` fallback is present.
    """
    matches = list(_FINAL_ANSWER_PATTERN.finditer(text or ""))
    if not matches:
        boxed = _BOXED_PATTERN.findall(text or "")
        if boxed:
            return boxed[-1].strip(), True
        return "", False
    answer = matches[-1].group(1).strip()
    answer = re.sub(r"^FINAL ANSWER:\s*", "", answer, flags=re.IGNORECASE).strip()
    answer = answer.strip("*").strip()
    return answer, True


# --- normalization ---------------------------------------------------------


def _normalize_uci(move: str) -> str:
    """
    Normalize a UCI move string for comparison.

    UCI is from-square + to-square + optional promotion piece, all lowercase
    (e.g. ``e2e4``, ``g1f3``, ``e7e8q``). We lowercase and strip so a model
    that emits ``E2E4`` or `` e2e4 `` still matches. We do NOT translate SAN —
    the skill prompt asks for UCI, and accepting SAN would require a board
    parser (a deliberate scope boundary; the canonical curation set can add it).
    """
    return re.sub(r"\s+", "", move).lower()


# --- scorers ---------------------------------------------------------------


def score_exact_move(output: str, case: dict) -> ScoreResult:
    """
    Score a UCI best-move case: extract the model's answer and exact-match it
    (after normalization) against ``case["correctAnswer"]``.
    """
    answer, found = extract_final_answer(output)
    if not found:
        return ScoreResult(
            "exactMove",
            False,
            "no FINAL ANSWER line found — cannot score a move case without it",
        )
    got = _normalize_uci(answer)
    want = _normalize_uci(case["correctAnswer"])
    if got == want:
        return ScoreResult("exactMove", True, f"move {got} matches expected {want}")
    return ScoreResult("exactMove", False, f"move {got!r} != expected {want!r}")


def score_eval_band(output: str, case: dict) -> ScoreResult:
    """
    Score a centipawn-band (position-judgment) case: exact-match the extracted
    answer against one of {-400, -200, 0, 200, 400}.
    """
    answer, found = extract_final_answer(output)
    if not found:
        return ScoreResult(
            "evalBand",
            False,
            "no FINAL ANSWER line found — cannot score a position-judgment case without it",
        )
    got = answer.strip()
    want = str(case["correctAnswer"]).strip()
    # Tolerate a leading '+' (some models write "+200"); the bands are unsigned
    # options except for the minus sign.
    if got.startswith("+"):
        got = got[1:]
    if got == want:
        return ScoreResult("evalBand", True, f"band {got} matches expected {want}")
    return ScoreResult("evalBand", False, f"band {got!r} != expected {want!r}")


# The forbidden-phrase block, verbatim from the chess app's validators
# (MoveCoachResponseValidator + PositionChatValidator). A coach must not assert
# engine provenance it cannot have, or claim certainty it cannot justify.
_FORBIDDEN_PHRASES = [
    "i think stockfish",
    "probably depth",
    "stockfish thinks",
    "engine depth",
    "elo ",
    "rating of",
    "forced mate",
    "guaranteed win",
    "winning by force",
    "forces checkmate",
]


def score_forbidden_phrases(output: str, case: dict) -> ScoreResult:
    """
    Hard honesty gate: fail if the output asserts engine provenance or
    unsupported certainty. Ported from the chess app so both skills enforce the
    same honesty floor.
    """
    lowered = output.lower()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            return ScoreResult(
                "forbiddenPhrases",
                False,
                f"output contains forbidden phrase {phrase!r} "
                "(engine-provenance or unsupported-certainty claim)",
            )
    return ScoreResult("forbiddenPhrases", True, "no forbidden phrases detected")


# --- reason faithfulness (Move Coach paraphrase contract) -------------------
#
# The exact-move scorer answers "did it find the right move?" The forbidden-
# phrase gate answers "did it claim engine provenance it can't have?" Neither
# answers the question the Move Coach contract is most exposed to: given a set
# of deterministic tags describing *why* the move is good, did the explanation
# stay faithful to them, or did it invent a reason the tags don't support?
#
# The failure mode this catches (named in the roadmap): the tags say
# `develops` / `center-control`, but the model writes "Nf3 attacks the enemy
# king" — grounded-sounding, honest-toned, factually false. That passes both
# the honesty gate and the move check, and inflates the score.
#
# Requires python-chess (optional) to derive the tags from the case's FEN +
# correctAnswer. Degrades to a skip when absent — never a silent pass.

# Tag → concept keywords the model's paraphrase is expected to contain when
# that tag was supplied. Mirrors MoveCoachPromptBuilder.describeTags' phrases.
# Coverage is checked only for tags that are *derivable* from a FEN (see
# tag_derivation); heuristic tags (defends/threatens/recapture) are omitted.
# Phase/context tags (opening) are deliberately absent: the model isn't asked
# to narrate "this is an opening move" — only to paraphrase the *reasoning*
# tags. Including opening would fail any explanation that didn't say "opening."
_TAG_CONCEPTS: dict[str, list[str]] = {
    # A capture claim names what was taken. Bare "take"/"win" matched ordinary
    # coaching prose — "takes space", "takes control", and above all "your
    # winning chances", which the Move Coach surface says on nearly every line.
    # Measured on a 100-case on-device run: 67 of 67 model and 17 of 17
    # deterministic "invents a capture" flags were that substring and *none*
    # named a captured piece. Same shape as the chess app's own
    # CAPTURE_OBJECT_PHRASES, arrived at independently.
    "capture": ["capture", "takes the", "takes a", "take the", "wins the", "wins a", "won the"],
    "check": ["check"],
    "checkmate": ["checkmate", "mate"],
    # Note: bare "kingside"/"queenside" are deliberately NOT keywords — they
    # name board halves ("the queenside pawns") and false-positive as castling.
    # "castle" as a bare word is chess-specific enough to keep as an indicator.
    "castle-kingside": ["castle", "o-o", "castles kingside", "castled kingside"],
    "castle-queenside": ["castle", "o-o-o", "castles queenside", "castled queenside"],
    "promotion": ["promot"],
    "material-swing": ["win material", "wins material", "winning material", "gains material", "gaining material", "up material"],
    "develops": ["develop", "activ"],
    "center-control": ["center", "centre", "central"],
    "king-safety": ["king safety", "tuck", "safe"],
    "pawn-push": ["space", "advance", "push"],
}

# Concepts whose assertion is high-stakes: claiming one when the tags don't
# support it is an invention (a factual claim about the position), not a
# stylistic flourish. Asymmetric by design — soft concepts (develops/center)
# are nearly always defensible for a good move, so volunteering them is not
# flagged; check/checkmate/material/capture are.
_HIGH_STAKES_TAGS = {
    "check",
    "checkmate",
    "material-swing",
    "capture",
    "castle-kingside",
    "castle-queenside",
    "promotion",
}


def check_invention(output: str, tags: set[str]) -> list[str]:
    """High-stakes concepts ``output`` asserts that ``tags`` do not supply.

    The invention half of [_check_faithfulness], callable on its own. That
    matters for any short-answer surface: `_check_faithfulness` returns the
    *coverage* failure first, so a candidate that omits a supplied tag never
    reports its inventions at all. Scoring a two-sentence Move Coach panel
    handed five tags, coverage fails ~99% of the time — which silently masked
    the deterministic baseline's inventions while the model's stayed visible,
    and made a head-to-head comparison of the two read backwards.
    """
    lowered = (output or "").lower()
    return [tag for tag in sorted(_HIGH_STAKES_TAGS) if tag not in tags and _mentions(tag, lowered)]


# "check" is a substring of "checkmate", and "mate" is a substring of "material"
# and "estimates" — both need a word boundary rather than `in`. Without it, every
# sentence containing "material swing" was scored as claiming checkmate: 4 of the
# 4 checkmate flags on the 2026-08-15 on-device run, none of them real.
_CHECK_CHESS = re.compile(r"\bcheck(?!mate| if\b| whether\b)")
_MATE_CHESS = re.compile(r"\b(?:checkmate|mate|mated|mating)\b")


def _mentions(tag: str, lowered: str) -> bool:
    """Whether ``lowered`` discusses ``tag``'s concept. One definition, used by
    both the coverage and the invention half."""
    concepts = _TAG_CONCEPTS.get(tag, [])
    if not concepts:
        return False
    if tag == "check":
        return bool(_CHECK_CHESS.search(lowered))
    if tag == "checkmate":
        return bool(_MATE_CHESS.search(lowered))
    return any(c in lowered for c in concepts)


def _check_faithfulness(output: str, tags: set[str]) -> ScoreResult:
    """
    The pure core of reason-faithfulness scoring: given a set of deterministic
    tags that describe the move, check whether ``output`` stays faithful to
    them. Extracted so it can run against either FEN-derived tags (ferryman's
    ChessQA cases) or pre-supplied tags (the chess app's candidates.json, where
    tags are hand-authored per case).

    See [score_reason_faithfulness] for the two checks (coverage + no
    unsupported invention). Returns a ScoreResult with key ``reasonFaithfulness``.
    """
    lowered = (output or "").lower()

    def mentions(tag: str) -> bool:
        return _mentions(tag, lowered)

    # 1. Coverage: each supplied tag's concept should be mentioned.
    missing = []
    for tag in tags:
        if _TAG_CONCEPTS.get(tag) is None:
            continue  # tag not in the coverage table (e.g. opening) — skip
        if not mentions(tag):
            missing.append(tag)

    # 2. Unsupported invention: high-stakes concept asserted but not supplied.
    invented = check_invention(output, tags)

    if missing:
        return ScoreResult(
            "reasonFaithfulness",
            False,
            f"omits supplied tag concept(s): {', '.join(sorted(missing))}",
        )
    if invented:
        return ScoreResult(
            "reasonFaithfulness",
            False,
            f"asserts unsupported concept(s): {', '.join(sorted(invented))}",
        )
    supplied = ", ".join(sorted(tags)) or "(none)"
    return ScoreResult(
        "reasonFaithfulness",
        True,
        f"explanation faithful to supplied tags: {supplied}",
    )


def score_reason_faithfulness(output: str, case: dict) -> ScoreResult:
    """
    Score whether the explanation stays faithful to the deterministic tags that
    describe the move.

    Two checks against the tag set derived from the case's FEN + correctAnswer:

    1. **Coverage** — each supplied tag's concept should appear in the output.
       A missing concept is an omission the prompt asked for.
    2. **No unsupported invention** — the output must not assert a high-stakes
       concept (check / checkmate / wins material / capture / castle / promo)
       that the derived tags don't include. That is the factual lie this scorer
       exists to catch.

    Faithfulness is a **strict gate**: a fail on either check fails the case.
    When python-chess is unavailable, returns a skip (passed=False but with a
    clearly marked reason) so the scorecard records the gap rather than passing
    silently — the same posture as the judge's family-exclusion skip.

    For pre-supplied tags (e.g. the chess app's hand-authored candidates.json),
    call [_check_faithfulness] directly instead of going through FEN derivation.
    """
    fen = case.get("input", {}).get("fen") if isinstance(case.get("input"), dict) else None
    move = case.get("correctAnswer")
    if not fen or not move:
        return ScoreResult(
            "reasonFaithfulness",
            False,
            "skip: case has no FEN/answer to derive tags from",
        )

    from eval_harness import tag_derivation

    try:
        tags = set(tag_derivation.derive_tags(fen, move))
    except tag_derivation.TagDerivationUnavailable:
        return ScoreResult(
            "reasonFaithfulness",
            False,
            "skip: python-chess not installed (install the [chess] extra)",
        )
    except ValueError as e:
        # Illegal move in the golden case is a fixture bug — surface it loudly
        # rather than silently passing or skipping.
        return ScoreResult("reasonFaithfulness", False, f"fixture error: {e}")

    return _check_faithfulness(output, tags)


# --- dispatch --------------------------------------------------------------


# --- readability -----------------------------------------------------------
#
# Why this exists: the judge's `tone_and_structure` criterion passes 120/120 on
# this skill's scorecard. Checking the raw scores shows why — the judge never
# rates it below 3.0, and 3.0 is the pass threshold, so its floor sits exactly
# on the bar. Every other judge criterion emits 1s and 2s. A criterion that has
# never failed is a criterion that has not been tested, so this replaces the
# fluency half of it with something deterministic that demonstrably varies.
#
# Note what this scorer is NOT: the chess app's three tone rules
# (process-praise, criticism-carries-a-next-step, no-self-reference) were tried
# here first and two of them are inert on this corpus — criticism vocabulary
# appears in 1 of 91 stored outputs, so "criticism must carry a next step"
# passes without ever firing. They discriminate in the app, where the coach
# critiques the player's own move. Reading level was the one that varied here.
# A scorer is only meaningful relative to the corpus it runs on.

# Grade bound, calibrated 2026-08-02 against the 91 non-blank stored outputs in
# scorecard-chess.json (29 zai-glm cases have no retained output).
#
#   bound  overall fail   gemini  hf-llama  zai-glm
#     6.0     62.6%         55%      72%      55%
#     8.0     36.3%         25%      50%      27%     <- chosen
#     9.5     14.3%         18%      15%       0%
#    12.0      0.0%          0%       0%       0%
#
# 8.0 is picked for discrimination, not for being a "correct" grade level: it
# separates hf-llama at roughly twice the others, which independently agrees
# with its worst-in-set rule pass rate (52%), reasoning quality (38%) and
# factual correctness (8%). A bound that ranks providers the same way the other
# scorers do is measuring something real. Above ~9.5 the separation collapses
# and inverts; at 12.0 nothing fails and we are back to a decorative column.
_READABILITY_MAX_GRADE = 8.0

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def _count_syllables(word: str) -> int:
    """
    Vowel-group syllable estimate. Approximate by design — Flesch-Kincaid needs
    an aggregate, and the bound above was calibrated with this same function, so
    its bias applies to both sides and largely cancels. Consequence: treat the
    grades as ordinal, not as real US grade levels.
    """
    clean = "".join(ch for ch in word.lower() if ch.isalpha())
    if not clean:
        return 1
    if len(clean) <= 3:
        return 1
    # Strip at most one trailing inflection, and only a silent "e". "-es" is
    # silent after a non-sibilant ("moves" = mov-es) but its own syllable after
    # c/s/x/z/ch/sh ("pieces" = pie-ces), so stripping it there under-counts.
    if clean.endswith("es") and not re.search(r"(c|s|x|z|ch|sh)$", clean[:-2]):
        stem = clean[:-2]
    elif clean.endswith("e") and not clean.endswith("le"):
        stem = clean[:-1]
    elif clean.endswith("s") and not clean.endswith("ss"):
        stem = clean[:-1]
    else:
        stem = clean
    groups = [g for g in re.split(r"[^aeiouy]+", stem) if g]
    return max(len(groups), 1)


def flesch_kincaid_grade(text: str) -> float:
    """0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59, floored at 0."""
    stripped = (text or "").strip()
    if not stripped:
        return 0.0
    sentences = [s for s in re.split(r"[.!?]+", stripped) if s.strip()] or [stripped]
    words = [w for w in re.split(r"[^a-z0-9']+", stripped.lower()) if w]
    if not words:
        return 0.0
    syllables = max(sum(_count_syllables(w) for w in words), 1)
    grade = 0.39 * (len(words) / len(sentences)) + 11.8 * (syllables / len(words)) - 15.59
    return max(round(grade, 1), 0.0)


def coaching_prose(output: str) -> str:
    """
    The part of the response a learner actually reads.

    Drops `<think>` deliberation (same contamination the faithfulness scorer had
    to strip) and the machine-readable `FINAL ANSWER:` line — "FINAL ANSWER:
    e2e4" is a protocol token, not prose, and counting it as a sentence skews
    both terms of the formula.
    """
    text = _THINK_BLOCK.sub(" ", output or "")
    text = _FINAL_ANSWER_PATTERN.sub(" ", text)
    return text.strip()


def score_readability(output: str, case: dict) -> ScoreResult:
    """
    Bound the reading level of the coaching prose. Coaching a learner in prose
    they have to re-read twice is a real failure even when the move is right —
    and it is a failure the exact-match scorers cannot see.
    """
    prose = coaching_prose(output)
    if not prose:
        return ScoreResult(
            "readability",
            False,
            "no coaching prose outside the FINAL ANSWER line",
        )
    grade = flesch_kincaid_grade(prose)
    if grade <= _READABILITY_MAX_GRADE:
        return ScoreResult("readability", True, f"grade {grade} <= {_READABILITY_MAX_GRADE}")
    return ScoreResult(
        "readability", False, f"grade {grade} exceeds {_READABILITY_MAX_GRADE}"
    )


def score_for_case(output: str, case: dict) -> list[ScoreResult]:
    """
    Run the correct exact-match scorer for the case's answer format, plus the
    always-on forbidden-phrase gate. UCI (best-move) cases additionally get the
    reason-faithfulness scorer, since they are the ones with a move — and thus
    a tag set — to be faithful to. Mirrors rule_scorers.score_all's shape so
    run_scorecard can treat both skills uniformly.
    """
    results: list[ScoreResult] = [score_forbidden_phrases(output, case)]
    fmt = case.get("answerFormat")
    if fmt == "uci":
        results.append(score_exact_move(output, case))
        results.append(score_reason_faithfulness(output, case))
    elif fmt == "centipawn-band":
        results.append(score_eval_band(output, case))
    else:
        # Unknown format — surface it rather than silently passing.
        results.append(
            ScoreResult("answerFormat", False, f"unknown answerFormat: {fmt!r}")
        )
    results.append(score_readability(output, case))
    return results
