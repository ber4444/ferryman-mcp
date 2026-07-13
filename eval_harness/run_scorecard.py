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

# Support running both as `python -m eval_harness.run_scorecard` and `python run_scorecard.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eval_harness import invoke as invoke_mod
    from eval_harness import rule_scorers
else:
    from . import invoke as invoke_mod
    from . import rule_scorers

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "golden_set.json"
SCORECARD_MD = Path(__file__).resolve().parent / "scorecard.md"
SCORECARD_JSON = Path(__file__).resolve().parent / "scorecard.json"


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

    @property
    def rule_pass_rate(self) -> float:
        if not self.rule_scores:
            return 0.0
        return sum(1 for s in self.rule_scores if s["passed"]) / len(self.rule_scores)


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    with path.open() as f:
        return json.load(f)


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


def run_one(case: dict, provider: str | None, mode: str) -> CaseResult:
    """Invoke the skill for one case and apply rule scorers."""
    result = invoke_mod.invoke(case["input"], provider=provider, mode=mode)
    rule_scores = [
        s.as_dict()
        for s in rule_scorers.score_all(
            result.output,
            case["expectedClaims"],
            case_input=case["input"],
        )
    ]
    return CaseResult(
        id=case["id"],
        input=case["input"],
        provider=result.provider,
        model=result.model,
        output=result.output,
        rule_scores=rule_scores,
        error=result.error,
    )


def run_all(
    golden: list[dict],
    *,
    providers: list[str | None],
    mode: str,
    use_judge: bool = False,
) -> list[CaseResult]:
    """Run the full golden set across the given providers."""
    results: list[CaseResult] = []
    for provider in providers:
        for case in golden:
            print(f"  [{provider or 'default'}] {case['id']}...", flush=True)
            case_result = run_one(case, provider, mode)
            if use_judge:
                case_result.judge_scores = _judge_if_available(case_result, case)
            results.append(case_result)
    return results


def _judge_if_available(case_result: CaseResult, case: dict) -> list[dict]:
    """Apply the judge scorer if its deps are installed; otherwise record a skip."""
    try:
        from . import judge_scorer
    except ImportError:
        return [{"key": "_judge", "passed": False, "reason": "judge deps not installed (pip install -e .[judge])"}]
    try:
        verdicts = judge_scorer.judge(case_result.output, case)
        return [v.as_dict() for v in verdicts]
    except Exception as e:  # noqa: BLE001 — judge failures must not crash the scorecard
        return [{"key": "_judge", "passed": False, "reason": f"judge error: {e}"}]


def write_scorecard(results: list[CaseResult], *, use_judge: bool) -> None:
    """Write scorecard.md and scorecard.json."""
    SCORECARD_JSON.write_text(json.dumps([asdict(r) for r in results], indent=2))
    SCORECARD_MD.write_text(_render_markdown(results, use_judge=use_judge))
    print(f"\nScorecard written:\n  {SCORECARD_MD}\n  {SCORECARD_JSON}")


def _render_markdown(results: list[CaseResult], *, use_judge: bool) -> str:
    lines = [
        "# ferryman eval scorecard",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Cases: {len(results)}",
        "",
        "## Rule-scorer results",
        "",
        "| Case | Provider | Pass rate | Failed checks |",
        "|---|---|---|---|",
    ]
    for r in results:
        failed = [s["key"] for s in r.rule_scores if not s["passed"]]
        lines.append(
            f"| {r.id} | {r.provider} | {r.rule_pass_rate:.0%} | {', '.join(failed) or '—'} |",
        )
    overall = sum(r.rule_pass_rate for r in results) / max(len(results), 1)
    lines.append("")
    lines.append(f"**Overall rule pass rate: {overall:.0%}**")
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
    parser.add_argument("--provider", help="single provider id to run")
    parser.add_argument(
        "--all-providers",
        action="store_true",
        help="run the full golden set once per configured provider",
    )
    parser.add_argument("--judge", action="store_true", help="add the LLM-judge layer")
    parser.add_argument(
        "--mode",
        choices=["auto", "http", "subprocess"],
        default="auto",
        help="invocation mode (default: try http, fall back to subprocess)",
    )
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH, help="path to golden set JSON")
    args = parser.parse_args(argv)

    golden = load_golden(args.golden)
    print(f"Loaded {len(golden)} golden cases from {args.golden}")

    if args.all_providers:
        providers: list[str | None] = enumerate_providers()
        print(f"--all-providers: running against {providers}")
    elif args.provider:
        providers = [args.provider]
    else:
        providers = [None]

    print(f"Running golden set (mode={args.mode}, judge={args.judge})...")
    results = run_all(golden, providers=providers, mode=args.mode, use_judge=args.judge)
    write_scorecard(results, use_judge=args.judge)
    return 0


if __name__ == "__main__":
    sys.exit(main())
