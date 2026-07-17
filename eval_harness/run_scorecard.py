"""
Run the full golden set through invoke.py, apply scorers, write a scorecard.

Usage:
    python -m eval_harness.run_scorecard                      # rule scorers, default provider
    python -m eval_harness.run_scorecard --judge               # add the LLM judge layer
    python -m eval_harness.run_scorecard --all-providers       # run once per configured provider
    python -m eval_harness.run_scorecard --provider anthropic  # single provider override

Output:
    eval_harness/scorecard.md  — human-readable scorecard
    eval_harness/scorecard.json — machine-readable results

The scorecard is regenerated on every run with real numbers from real
invocations. No placeholder rows, no fabricated deltas — the hard rule.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

# Support running both as `python -m eval_harness.run_scorecard` and `python run_scorecard.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eval_harness import invoke as invoke_mod
    from eval_harness import rule_scorers
    from eval_harness import chess_scorers
else:
    from . import invoke as invoke_mod
    from . import rule_scorers
    from . import chess_scorers

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_PATH = _GOLDEN_DIR / "golden_set.json"
SCORECARD_MD = Path(__file__).resolve().parent / "scorecard.md"
SCORECARD_JSON = Path(__file__).resolve().parent / "scorecard.json"

# Default skill id when --skill is not given. Preserved for backward
# compatibility — the company-research harness runs unchanged with no args.
DEFAULT_SKILL = "company-role-research"


@dataclass
class SkillSpec:
    """Per-skill eval wiring: which golden set + scorer + skill id + outputs.

    The eval-harness contract (AGENTS.md: skills enumerable, config TOML,
    Orchestrator.runSkill) is unchanged — this just lets one harness score more
    than one skill. Each spec names the skill the orchestrator should invoke and
    the scorer callable that turns a case into ScoreResults.
    """

    name: str
    skill_id: str  # the ferryman skill name passed to Orchestrator.runSkill
    golden_path: Path
    scorecard_md: Path
    scorecard_json: Path
    # (scorer_callable, case) -> list[ScoreResult]. Mirrors rule_scorers.score_all's shape.
    scorer: Callable[[str, dict], list]
    # The claim-like field the scorer reads from each case. company-research uses
    # expectedClaims; chess uses the whole case (correctAnswer lives on the case).
    # Kept as a label for the scorecard, not a behavioral branch.
    rubric_path: Path | None = None


# Registry of evaluable skills. Adding a skill is: add a SKILL.md, a golden set,
# a scorer module, and one entry here.
_SKILL_SPECS: dict[str, SkillSpec] = {
    "company-role-research": SkillSpec(
        name="company-role-research",
        skill_id="company-role-research",
        golden_path=_GOLDEN_DIR / "golden_set.json",
        scorecard_md=Path(__file__).resolve().parent / "scorecard.md",
        scorecard_json=Path(__file__).resolve().parent / "scorecard.json",
        scorer=lambda output, case: rule_scorers.score_all(
            output, case["expectedClaims"], case_input=case.get("input", {})
        ),
        rubric_path=Path(__file__).resolve().parent / "rubric.md",
    ),
    "chess-opening-coach": SkillSpec(
        name="chess-opening-coach",
        skill_id="chess-opening-coach",
        golden_path=_GOLDEN_DIR / "chess_golden.json",
        scorecard_md=Path(__file__).resolve().parent / "scorecard-chess.md",
        scorecard_json=Path(__file__).resolve().parent / "scorecard-chess.json",
        scorer=chess_scorers.score_for_case,
        rubric_path=Path(__file__).resolve().parent / "rubric-chess.md",
    ),
}


def get_skill_spec(name: str | None) -> SkillSpec:
    """Resolve a skill name to its spec, defaulting to company-role-research."""
    return _SKILL_SPECS[name or DEFAULT_SKILL]


@dataclass
class CaseResult:
    """One golden case's full scoring result."""

    id: str
    input: dict[str, str]
    provider: str
    model: str
    output: str
    rule_scores: list[dict[str, object]] = field(default_factory=list)
    judge_scores: list[dict[str, object]] = field(default_factory=list)
    error: str | None = None
    latency_ms: int | None = None
    estimated_cost_usd: float | None = None

    @property
    def rule_pass_rate(self) -> float:
        if not self.rule_scores:
            return 0.0
        return sum(1 for s in self.rule_scores if s["passed"]) / len(self.rule_scores)


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    with path.open() as f:
        return json.load(f)


def load_results(path: Path) -> list[CaseResult]:
    """Reconstruct CaseResult objects from a previously-written scorecard JSON.

    Used by ``--rescore-judge`` to re-judge captured outputs without re-paying
    for provider generations. Every field the judge needs (``output``, ``model``,
    ``error``) is preserved in the JSON, so the rescored verdicts are identical
    to what a fresh ``--judge`` run would have produced on the same outputs.
    """
    with path.open() as f:
        rows = json.load(f)
    return [CaseResult(**row) for row in rows]


def rescore_judge(results: list[CaseResult], spec: "SkillSpec | None" = None) -> list[CaseResult]:
    """Re-run only the judge scorer over captured outputs.

    Rule scores, latency, cost, and the captured ``output`` text are preserved
    as-is — only ``judge_scores`` is recomputed. This costs N judge calls (one
    per row) and zero provider calls, vs. a full ``--judge`` re-run which
    re-pays for both. Use it when only the judge config changed (model swap,
    rubric edit, a botched key) and the underlying generations are still valid.

    The ``case`` dict each judge call needs is rebuilt from the stored ``input``
    field — the golden set is not re-read, so the rescore is faithful to what
    was captured even if the golden set has since changed.
    """
    spec = spec or get_skill_spec(DEFAULT_SKILL)
    for r in results:
        # judge() reads case["input"] and case["expectedClaims"] (company) /
        # case fields (chess). The captured row's `input` + `id` are enough;
        # expectedClaims isn't used by the judge prompt for chess, and for
        # company-research the judge reads ground truth from the case dict —
        # which the stored input doesn't carry. Load the golden case to restore
        # it so the judge sees the same ground truth the original run did.
        case = {"id": r.id, "input": r.input}
        golden_case = _find_golden_case(spec.golden_path, r.id)
        if golden_case is not None:
            case.update(golden_case)
        # Mirror run_all's errored-case skip: don't send an empty output to the
        # judge (it would burn an API call and record scores that look like
        # model-quality failures when the real failure is infra).
        if r.error:
            r.judge_scores = [
                {
                    "key": "_judge",
                    "passed": False,
                    "reason": f"skipped — case errored: {r.error}",
                }
            ]
            continue
        r.judge_scores = _judge_if_available(r, case, spec=spec)
    return results


def _find_golden_case(golden_path: Path, case_id: str) -> dict | None:
    """Look up one golden case by id, to restore ground-truth fields the judge reads."""
    try:
        for case in load_golden(golden_path):
            if case.get("id") == case_id:
                return case
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def enumerate_providers() -> list[str]:
    """
    Read ferryman's config.toml to list configured providers. Falls back to the
    ZAI/ANTHROPIC env-var convention if the config or toml parse fails.
    """
    config_path = Path(__file__).resolve().parent.parent / "ferryman" / "config.toml"
    try:
        import tomllib  # Python 3.11+ stdlib
        with config_path.open("rb") as f:
            config = tomllib.load(f)
        return list(config.get("providers", {}).keys())
    except (FileNotFoundError, ImportError, Exception):
        # Graceful fallback — never crash the harness on config read.
        return ["zai-glm", "anthropic"]


# When running --all-providers, run the most reliable first so an interrupted
# run still leaves the good providers' results on disk (incremental writes
# fire after each provider). Gemini is last because it rate-limits hardest.
# Providers not listed here sort after the known ones, preserving config order.
_PROVIDER_RUN_ORDER = ["hf-llama", "zai-glm", "gemini"]


def order_providers(providers: list[str]) -> list[str]:
    """Sort providers into the preferred run order; unknowns keep relative order at the end."""
    rank = {p: i for i, p in enumerate(_PROVIDER_RUN_ORDER)}
    return sorted(providers, key=lambda p: rank.get(p, len(_PROVIDER_RUN_ORDER)))


PRICING_PATH = Path(__file__).resolve().parent / "pricing.json"


def load_pricing() -> dict:
    """Load the recorded per-token pricing. Empty dict if missing — cost stays None."""
    try:
        with PRICING_PATH.open() as f:
            return json.load(f).get("providers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# Rough chars-per-token factor for the fallback cost estimate. Used only when a
# provider returns no `usage` block (or an error row). English prose averages ~4
# chars/token across modern tokenizers — an ESTIMATE, never exact.
_CHARS_PER_TOKEN = 4.0


def estimate_cost(result: invoke_mod.InvocationResult, pricing: dict) -> float | None:
    """
    Compute USD cost from recorded pricing + token counts.

    Uses the real prompt/completion token counts threaded through from the
    provider's ``usage`` block when present (exact cost). Falls back to the
    chars/4 estimate only when those counts are absent — e.g. a provider that
    reports no usage, or an error row with no invocation data. Returns None if
    the provider isn't listed in pricing.json.
    """
    entry = pricing.get(result.provider)
    if entry is None:
        return None
    input_tokens = (
        result.input_tokens
        if result.input_tokens is not None
        else result.input_chars / _CHARS_PER_TOKEN
    )
    output_tokens = (
        result.output_tokens
        if result.output_tokens is not None
        else result.output_chars / _CHARS_PER_TOKEN
    )
    cost = (
        input_tokens * entry.get("inputPricePerMillionTokens", 0.0)
        + output_tokens * entry.get("outputPricePerMillionTokens", 0.0)
    ) / 1_000_000
    return round(cost, 6)


def run_one(case: dict, provider: str | None, mode: str, spec: SkillSpec | None = None) -> CaseResult:
    """Invoke the skill for one case and apply its scorers.

    [spec] selects the skill + scorer; defaults to company-role-research so the
    pre-existing call sites (and the harness's backward-compat contract) are
    unchanged.
    """
    spec = spec or get_skill_spec(DEFAULT_SKILL)
    result = invoke_mod.invoke(case["input"], skill=spec.skill_id, provider=provider, mode=mode)
    rule_scores = [s.as_dict() for s in spec.scorer(result.output, case)]
    pricing = load_pricing()
    return CaseResult(
        id=case["id"],
        input=case["input"],
        provider=result.provider,
        model=result.model,
        output=result.output,
        rule_scores=rule_scores,
        error=result.error,
        latency_ms=result.latency_ms,
        estimated_cost_usd=estimate_cost(result, pricing),
    )


# Inter-call delay (seconds) to stay under per-provider rate limits, e.g.
# Gemini's ~1 req/s free tier. Defaults to off so reliable providers aren't
# slowed; set FERRY_THROTTLE_SECONDS=1.0 for a rate-limited provider.
_THROTTLE_SECONDS = float(os.environ.get("FERRY_THROTTLE_SECONDS", "0") or 0)


def run_all(
    golden: list[dict],
    *,
    providers: list[str | None],
    mode: str,
    use_judge: bool = False,
    on_provider_done: "Callable[[list[CaseResult]], None] | None" = None,
    spec: SkillSpec | None = None,
) -> list[CaseResult]:
    """Run the full golden set across the given providers.

    Each case is isolated: an exception (timeout, connection error) records a
    CaseResult with ``error`` set and the loop continues — one flaky provider
    can no longer abort the whole batch. After each provider finishes,
    [on_provider_done] (if given) is called with the accumulated results so a
    partial scorecard is always on disk if the run is interrupted later.

    [spec] selects the skill + scorer + output paths; defaults to
    company-role-research.
    """
    spec = spec or get_skill_spec(DEFAULT_SKILL)
    results: list[CaseResult] = []
    for provider in providers:
        for case in golden:
            print(f"  [{provider or 'default'}] {case['id']}...", flush=True)
            # Per-case isolation: a thrown exception becomes an error CaseResult,
            # not a batch abort. Follows the harness's own _judge_if_available
            # convention (broad except, record, continue).
            try:
                case_result = run_one(case, provider, mode, spec=spec)
            except Exception as e:  # noqa: BLE001 — one case must not kill the batch
                case_result = CaseResult(
                    id=case["id"],
                    input=case.get("input", {}),
                    provider=provider or "unknown",
                    model="unknown",
                    output="",
                    error=f"{type(e).__name__}: {e}",
                )
            if use_judge:
                # Don't judge a case that errored: its output is empty, so the
                # judge would burn an API call and record scores that look like
                # model-quality failures when the real failure is infra. The
                # error is already captured on case_result.error.
                if case_result.error:
                    case_result.judge_scores = [
                        {
                            "key": "_judge",
                            "passed": False,
                            "reason": f"skipped — case errored: {case_result.error}",
                        }
                    ]
                else:
                    case_result.judge_scores = _judge_if_available(case_result, case, spec=spec)
            results.append(case_result)
            if _THROTTLE_SECONDS > 0:
                time.sleep(_THROTTLE_SECONDS)
        # Incremental save: write whatever's accumulated after each provider.
        if on_provider_done is not None:
            on_provider_done(results)
    return results


def _judge_if_available(case_result: CaseResult, case: dict, spec: SkillSpec | None = None) -> list[dict]:
    """Apply the judge scorer if its deps are installed; otherwise record a skip."""
    spec = spec or get_skill_spec(DEFAULT_SKILL)
    try:
        # Mirror the top-of-file import guard: the bare relative `from .` only
        # resolves when this module is imported as part of the eval_harness
        # package (python -m). When run as a script (python run_scorecard.py)
        # __package__ is empty and the relative import raises ImportError — which
        # used to be misreported as "judge deps not installed", sending users
        # down a wrong httpx rabbit hole. Use the absolute import in script mode.
        if __package__:
            from . import judge_scorer
        else:
            from eval_harness import judge_scorer
    except ImportError:
        return [{"key": "_judge", "passed": False, "reason": "judge deps not installed (pip install -e .[judge])"}]
    try:
        # Pass the evaluated model so the judge can enforce family-exclusion
        # (a judge never grades its own family), and the skill's rubric so chess
        # is judged against the chess rubric, not the company-research one.
        verdicts = judge_scorer.judge(
            case_result.output,
            case,
            evaluated_model=case_result.model,
            rubric_path=spec.rubric_path,
        )
        return [v.as_dict() for v in verdicts]
    except judge_scorer.JudgeFamilyConflict as e:
        return [{"key": "_judge", "passed": False, "reason": f"family conflict (skipped): {e}"}]
    except Exception as e:  # noqa: BLE001 — judge failures must not crash the scorecard
        return [{"key": "_judge", "passed": False, "reason": f"judge error: {e}"}]


def write_scorecard(results: list[CaseResult], *, use_judge: bool, spec: SkillSpec | None = None) -> None:
    """Write the scorecard markdown + json for the given skill's spec."""
    spec = spec or get_skill_spec(DEFAULT_SKILL)
    spec.scorecard_json.write_text(json.dumps([asdict(r) for r in results], indent=2))
    spec.scorecard_md.write_text(_render_markdown(results, use_judge=use_judge, spec=spec))
    print(f"\nScorecard written:\n  {spec.scorecard_md}\n  {spec.scorecard_json}")


def _render_markdown(results: list[CaseResult], *, use_judge: bool, spec: SkillSpec | None = None) -> str:
    spec = spec or get_skill_spec(DEFAULT_SKILL)
    pricing = load_pricing()
    lines = [
        f"# ferryman eval scorecard — {spec.name}",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Cases: {len(results)}",
        "",
        "## Rule-scorer results",
        "",
        "| Case | Provider | Pass rate | Failed checks | Latency | Cost |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        failed = [s["key"] for s in r.rule_scores if not s["passed"]]
        latency = f"{r.latency_ms} ms" if r.latency_ms is not None else "—"
        cost = f"${r.estimated_cost_usd:.4f}" if r.estimated_cost_usd is not None else "—"
        lines.append(
            f"| {r.id} | {r.provider} | {r.rule_pass_rate:.0%} | "
            f"{', '.join(failed) or '—'} | {latency} | {cost} |",
        )
    overall = sum(r.rule_pass_rate for r in results) / max(len(results), 1)
    lines.append("")
    lines.append(f"**Overall rule pass rate: {overall:.0%}**")
    # Per-provider aggregate row (the multi-provider matrix comparison).
    lines.append("")
    lines.append("## Per-provider summary")
    lines.append("")
    lines.append("| Provider | Cases | Mean pass rate | Mean latency | Mean cost | Pricing date |")
    lines.append("|---|---|---|---|---|---|")
    for provider_id in sorted({r.provider for r in results}):
        prov_results = [r for r in results if r.provider == provider_id]
        mean_pass = sum(r.rule_pass_rate for r in prov_results) / len(prov_results)
        latencies = [r.latency_ms for r in prov_results if r.latency_ms is not None]
        mean_lat = f"{sum(latencies) // len(latencies)} ms" if latencies else "—"
        costs = [r.estimated_cost_usd for r in prov_results if r.estimated_cost_usd is not None]
        mean_cost = f"${sum(costs) / len(costs):.4f}" if costs else "—"
        date_checked = pricing.get(provider_id, {}).get("dateChecked", "—")
        lines.append(
            f"| {provider_id} | {len(prov_results)} | {mean_pass:.0%} | {mean_lat} | {mean_cost} | {date_checked} |",
        )
    if use_judge:
        lines.append("")
        lines.append("## Judge-scorer results")
        lines.append("")
        lines.append("| Case | Judge scores |")
        lines.append("|---|---|")
        for r in results:
            scores = r.judge_scores
            summary = ", ".join(f"{s['key']}={'pass' if s['passed'] else 'fail'}" for s in scores)
            lines.append(f"| {r.id} | {summary or '—'} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ferryman eval scorecard.")
    parser.add_argument(
        "--skill",
        default=DEFAULT_SKILL,
        choices=sorted(_SKILL_SPECS.keys()),
        help=f"skill to evaluate (default: {DEFAULT_SKILL})",
    )
    parser.add_argument("--provider", help="single provider id to run")
    parser.add_argument(
        "--all-providers",
        action="store_true",
        help="run the full golden set once per configured provider",
    )
    parser.add_argument("--judge", action="store_true", help="add the LLM-judge layer")
    parser.add_argument(
        "--rescore-judge",
        action="store_true",
        help="re-judge the existing scorecard JSON's captured outputs only (no provider "
        "re-spend); use after a judge-config change (model swap, rubric edit, bad key)",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "http", "subprocess"],
        default="auto",
        help="invocation mode (default: try http, fall back to subprocess)",
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="path to golden set JSON (default: the skill's registered golden set)",
    )
    args = parser.parse_args(argv)

    spec = get_skill_spec(args.skill)
    golden_path = args.golden or spec.golden_path
    golden = load_golden(golden_path)
    print(f"Skill: {spec.name} (invoking skill id '{spec.skill_id}')")
    print(f"Loaded {len(golden)} golden cases from {golden_path}")

    # --rescore-judge short-circuits: load the existing captured outputs and
    # re-judge them only. Zero provider re-spend — just N judge calls. The rule
    # scores, latency, cost, and output text are preserved from the prior run.
    if args.rescore_judge:
        if not spec.scorecard_json.exists():
            print(
                f"error: {spec.scorecard_json} not found — run a full scorecard first "
                f"(without --rescore-judge) to capture outputs.",
                file=sys.stderr,
            )
            return 1
        print(f"Re-judging captured outputs from {spec.scorecard_json} (no provider calls)...")
        results = load_results(spec.scorecard_json)
        results = rescore_judge(results, spec=spec)
        write_scorecard(results, use_judge=True, spec=spec)
        return 0

    if args.all_providers:
        providers: list[str | None] = order_providers(enumerate_providers())
        print(f"--all-providers: running against {providers}")
    elif args.provider:
        providers = [args.provider]
    else:
        providers = [None]

    # Incremental save: after each provider finishes, write the scorecard so
    # an interrupted run (a later provider timing out) keeps earlier results.
    def save_so_far(so_far: list[CaseResult]) -> None:
        write_scorecard(so_far, use_judge=args.judge, spec=spec)

    print(f"Running golden set (mode={args.mode}, judge={args.judge})...")
    results = run_all(
        golden,
        providers=providers,
        mode=args.mode,
        use_judge=args.judge,
        on_provider_done=save_so_far if args.all_providers else None,
        spec=spec,
    )
    write_scorecard(results, use_judge=args.judge, spec=spec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
