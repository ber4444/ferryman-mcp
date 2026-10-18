"""
Engine head-to-head: the same skill, golden set, providers and scorers, run on
two agent hosts — ferryman's hand-rolled orchestrator and the Koog rebuild.

Each case runs on both engines back to back, alternating which goes first, so
both see the same live web pages, the same model version and the same rate-limit
weather. A separate run per engine a day apart would fold all of that into the
"engine" difference.

Usage:
    python eval_harness/head_to_head.py --all-providers
    python eval_harness/head_to_head.py --provider gemini --limit 5     # smoke run
    python eval_harness/head_to_head.py --all-providers --judge         # + judge layer
    python eval_harness/head_to_head.py --compare-only                  # rebuild the report

Output (eval_harness/head_to_head/ by default):
    ferryman.json, koog.json  per-case rows, the scorecard JSON shape
    report.md, report.json    the comparison

The report is regenerated from real invocations only. With no provider key or
no ferry binary the run errors per case; it never fills in numbers.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eval_harness import run_scorecard
    from eval_harness.run_scorecard import CaseResult
else:
    from . import run_scorecard
    from .run_scorecard import CaseResult

BASELINE = "ferryman"
CHALLENGER = "koog"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "head_to_head"
_REPO_ROOT = Path(__file__).resolve().parent.parent

# The skill's own instruction is "call fetch exactly once" — so the tool trace is
# an instruction-following signal the rule scorers don't see.
_FETCH_TOOL = "fetch.fetch"
_BUDGET_MESSAGE = re.compile(r"^Reached tool-call limit \(\d+\) without a final answer\.$")


# --- running -----------------------------------------------------------------


def engine_order(case_index: int, engines: tuple[str, ...]) -> tuple[str, ...]:
    """Alternate which engine goes first, so neither always hits a warm (or cold) cache."""
    return engines if case_index % 2 == 0 else tuple(reversed(engines))


def run_head_to_head(
    golden: list[dict],
    *,
    providers: list[str | None],
    mode: str,
    spec: run_scorecard.SkillSpec,
    engines: tuple[str, ...] = (BASELINE, CHALLENGER),
    use_judge: bool = False,
    on_provider_done: Callable[[dict[str, list[CaseResult]]], None] | None = None,
) -> dict[str, list[CaseResult]]:
    """Run every (provider, case) on every engine, interleaved. Returns rows per engine."""
    specs = {engine: run_scorecard.with_engine(spec, engine) for engine in engines}
    results: dict[str, list[CaseResult]] = {engine: [] for engine in engines}
    for provider in providers:
        for i, case in enumerate(golden):
            for engine in engine_order(i, engines):
                print(f"  [{provider or 'default'}] {case['id']} on {engine}...", flush=True)
                row = run_scorecard.run_case_isolated(case, provider, mode, spec=specs[engine], use_judge=use_judge)
                results[engine].append(row)
                if run_scorecard._THROTTLE_SECONDS > 0:
                    time.sleep(run_scorecard._THROTTLE_SECONDS)
        if on_provider_done is not None:
            on_provider_done(results)
    return results


# --- comparing ---------------------------------------------------------------


def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided exact sign test over the non-tied pairs (ties carry no direction)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def _mean(values: Iterable[float | int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _median(values: Iterable[float | int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None


def engine_summary(rows: list[CaseResult]) -> dict:
    """Everything the report says about one engine on one slice of rows."""
    answered = [r for r in rows if not r.error]
    per_claim: dict[str, dict[str, int]] = {}
    for r in rows:
        for score in r.rule_scores:
            claim = per_claim.setdefault(str(score["key"]), {"passed": 0, "applicable": 0})
            claim["applicable"] += 1
            claim["passed"] += 1 if score["passed"] else 0
    judge: dict[str, dict[str, int]] = {}
    for r in rows:
        for score in r.judge_scores:
            if score["key"] == "_judge":
                continue
            crit = judge.setdefault(str(score["key"]), {"passed": 0, "applicable": 0})
            crit["applicable"] += 1
            crit["passed"] += 1 if score["passed"] else 0
    return {
        "cases": len(rows),
        "errors": len(rows) - len(answered),
        "rulePassRate": _mean(r.rule_pass_rate for r in rows),
        "rulePassRateAnswered": _mean(r.rule_pass_rate for r in answered),
        "perClaim": dict(sorted(per_claim.items())),
        "judge": dict(sorted(judge.items())),
        "meanLatencyMs": _mean(r.latency_ms for r in rows),
        "medianLatencyMs": _median(r.latency_ms for r in rows),
        "meanCostUsd": _mean(r.estimated_cost_usd for r in rows),
        "meanInputTokens": _mean(r.input_tokens for r in answered),
        "meanOutputTokens": _mean(r.output_tokens for r in answered),
        "meanToolCalls": _mean(len(r.tool_calls) for r in answered),
        "exactlyOneFetch": sum(1 for r in answered if r.tool_calls == [_FETCH_TOOL]),
        "budgetExhausted": sum(1 for r in answered if _BUDGET_MESSAGE.match(r.output.strip())),
        "models": sorted({r.model for r in rows if r.model not in ("unknown", "none")}),
        "errorSamples": sorted({(r.error or "")[:160] for r in rows if r.error})[:3],
    }


def paired(baseline: list[CaseResult], challenger: list[CaseResult]) -> dict:
    """Per-case comparison on rows matched by (provider, case id). diff = challenger − baseline."""
    base = {(r.provider, r.id): r for r in baseline}
    pairs = [(base[(c.provider, c.id)], c) for c in challenger if (c.provider, c.id) in base]
    diffs = [c.rule_pass_rate - b.rule_pass_rate for b, c in pairs]
    wins = sum(1 for d in diffs if d > 1e-9)
    losses = sum(1 for d in diffs if d < -1e-9)
    disagreements = [
        {
            "provider": b.provider,
            "case": b.id,
            "company": b.input.get("company", ""),
            BASELINE: round(b.rule_pass_rate, 3),
            CHALLENGER: round(c.rule_pass_rate, 3),
            f"{BASELINE}Failed": sorted(str(s["key"]) for s in b.rule_scores if not s["passed"]) or (["error"] if b.error else []),
            f"{CHALLENGER}Failed": sorted(str(s["key"]) for s in c.rule_scores if not s["passed"]) or (["error"] if c.error else []),
        }
        for (b, c), d in zip(pairs, diffs)
        if abs(d) > 1e-9 or bool(b.error) != bool(c.error)
    ]
    return {
        "pairs": len(pairs),
        "challengerWins": wins,
        "baselineWins": losses,
        "ties": len(pairs) - wins - losses,
        "meanDiff": _mean(diffs),
        "signTestP": sign_test_p(wins, losses),
        "disagreements": disagreements,
    }


def compare(results: dict[str, list[CaseResult]]) -> dict:
    """The full report: per provider and overall, each engine's summary plus the paired test."""
    baseline, challenger = results.get(BASELINE, []), results.get(CHALLENGER, [])
    providers = sorted({r.provider for r in baseline + challenger})
    slices: dict[str, dict] = {}
    for provider in providers + ["all"]:
        def pick(rows: list[CaseResult]) -> list[CaseResult]:
            return rows if provider == "all" else [r for r in rows if r.provider == provider]

        slices[provider] = {
            BASELINE: engine_summary(pick(baseline)),
            CHALLENGER: engine_summary(pick(challenger)),
            "paired": paired(pick(baseline), pick(challenger)),
        }
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gitCommit": _git_commit(),
        "koogVersion": _koog_version(),
        "baseline": BASELINE,
        "challenger": CHALLENGER,
        "providers": providers,
        "slices": slices,
    }


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _koog_version() -> str:
    try:
        text = (_REPO_ROOT / "build.gradle.kts").read_text()
    except OSError:
        return "unknown"
    match = re.search(r'"ai\.koog:agents-core:([^"]+)"', text)
    return match.group(1) if match else "unknown"


# --- rendering ---------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _secs(ms: float | None) -> str:
    return "—" if ms is None else f"{ms / 1000:.1f} s"


def _usd(value: float | None) -> str:
    return "—" if value is None else f"${value:.4f}"


def _num(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _signed_pts(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f} pts"


def render_markdown(report: dict) -> str:
    b, c = report["baseline"], report["challenger"]
    lines = [
        f"# Engine head-to-head — {b} (hand-rolled) vs {c} (Koog {report['koogVersion']})",
        "",
        f"Generated {report['generated']} at commit `{report['gitCommit']}`. Same skill prompt, golden set, "
        "provider endpoints, MCP servers, 4-model-call budget, 2048-token output cap and retry policy on both "
        "engines; each case ran on both back to back, alternating order.",
        "",
        "## Summary",
        "",
        f"| Provider | Engine | Rule pass | Pass (answered) | Errors | Mean latency | Mean cost | Tool calls/run | Exactly one fetch |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for provider in report["providers"] + ["all"]:
        s = report["slices"][provider]
        for engine in (b, c):
            e = s[engine]
            answered = e["cases"] - e["errors"]
            lines.append(
                f"| {provider} | {engine} | {_pct(e['rulePassRate'])} | {_pct(e['rulePassRateAnswered'])} | "
                f"{e['errors']}/{e['cases']} | {_secs(e['meanLatencyMs'])} | {_usd(e['meanCostUsd'])} | "
                f"{_num(e['meanToolCalls'])} | {e['exactlyOneFetch']}/{answered} |"
            )
    lines += [
        "",
        "## Paired comparison (per case, same provider)",
        "",
        f"Difference is {c} − {b} in per-case rule pass rate. The sign test asks whether the cases where the "
        "engines disagree lean one way more than a coin would; ties carry no information.",
        "",
        f"| Provider | Pairs | {c} better | {b} better | Ties | Mean diff | Sign test p |",
        "|---|---|---|---|---|---|---|",
    ]
    for provider in report["providers"] + ["all"]:
        p = report["slices"][provider]["paired"]
        lines.append(
            f"| {provider} | {p['pairs']} | {p['challengerWins']} | {p['baselineWins']} | {p['ties']} | "
            f"{_signed_pts(p['meanDiff'])} | {p['signTestP']:.3f} |"
        )
    lines += ["", "## Per-claim pass rate", "", f"| Provider | Claim | {b} | {c} |", "|---|---|---|---|"]
    for provider in report["providers"] + ["all"]:
        s = report["slices"][provider]
        for claim in sorted(set(s[b]["perClaim"]) | set(s[c]["perClaim"])):
            cells = []
            for engine in (b, c):
                tally = s[engine]["perClaim"].get(claim)
                cells.append(f"{tally['passed']}/{tally['applicable']}" if tally else "—")
            lines.append(f"| {provider} | {claim} | {cells[0]} | {cells[1]} |")
    judged = any(report["slices"]["all"][e]["judge"] for e in (b, c))
    if judged:
        lines += ["", "## Judge layer", "", f"| Provider | Criterion | {b} | {c} |", "|---|---|---|---|"]
        for provider in report["providers"] + ["all"]:
            s = report["slices"][provider]
            for crit in sorted(set(s[b]["judge"]) | set(s[c]["judge"])):
                cells = []
                for engine in (b, c):
                    tally = s[engine]["judge"].get(crit)
                    cells.append(f"{tally['passed']}/{tally['applicable']}" if tally else "—")
                lines.append(f"| {provider} | {crit} | {cells[0]} | {cells[1]} |")
    lines += [
        "",
        "## Tokens and budget",
        "",
        f"| Provider | Engine | Mean input tokens | Mean output tokens | Hit the 4-call budget | Models |",
        "|---|---|---|---|---|---|",
    ]
    for provider in report["providers"]:
        s = report["slices"][provider]
        for engine in (b, c):
            e = s[engine]
            lines.append(
                f"| {provider} | {engine} | {_num(e['meanInputTokens'], 0)} | {_num(e['meanOutputTokens'], 0)} | "
                f"{e['budgetExhausted']} | {', '.join(e['models']) or '—'} |"
            )
    errors = [
        (provider, engine, sample)
        for provider in report["providers"]
        for engine in (b, c)
        for sample in report["slices"][provider][engine]["errorSamples"]
    ]
    if errors:
        lines += ["", "## Error samples", "", "| Provider | Engine | Error (first 160 chars) |", "|---|---|---|"]
        for provider, engine, sample in errors:
            lines.append(f"| {provider} | {engine} | {sample.replace('|', '/')} |")
    disagreements = report["slices"]["all"]["paired"]["disagreements"]
    lines += ["", f"## Cases where the engines disagree ({len(disagreements)})", ""]
    if disagreements:
        lines += [f"| Provider | Case | Company | {b} | {c} | {b} failed | {c} failed |", "|---|---|---|---|---|---|---|"]
        for d in disagreements:
            lines.append(
                f"| {d['provider']} | {d['case']} | {d['company']} | {_pct(d[b])} | {_pct(d[c])} | "
                f"{', '.join(d[b + 'Failed']) or '—'} | {', '.join(d[c + 'Failed']) or '—'} |"
            )
    else:
        lines.append("None — every pair scored the same.")
    lines.append("")
    return "\n".join(lines)


# --- I/O ---------------------------------------------------------------------


def write_outputs(results: dict[str, list[CaseResult]], out_dir: Path, *, quiet: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    for engine, rows in results.items():
        (out_dir / f"{engine}.json").write_text(json.dumps([asdict(r) for r in rows], indent=2))
    report = compare(results)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "report.md").write_text(render_markdown(report))
    if not quiet:
        print(f"\nHead-to-head written to {out_dir}/report.md")
    return report


def load_saved(out_dir: Path, engines: tuple[str, ...]) -> dict[str, list[CaseResult]]:
    return {engine: run_scorecard.load_results(out_dir / f"{engine}.json") for engine in engines}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score one skill on both agent hosts, head to head.")
    parser.add_argument("--skill", default=run_scorecard.DEFAULT_SKILL, choices=sorted(run_scorecard._SKILL_SPECS))
    parser.add_argument("--provider", help="single provider id to run")
    parser.add_argument("--all-providers", action="store_true", help="run every provider in config.toml")
    parser.add_argument("--mode", choices=["auto", "http", "subprocess"], default="subprocess",
                        help="invocation mode (default subprocess: each case gets a fresh JVM on both engines)")
    parser.add_argument("--golden", type=Path, default=None, help="golden set JSON (default: the skill's)")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases (smoke runs)")
    parser.add_argument("--judge", action="store_true", help="add the LLM-judge layer to both engines")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--compare-only", action="store_true", help="rebuild the report from saved JSON, no calls")
    args = parser.parse_args(argv)
    engines = (BASELINE, CHALLENGER)

    if args.compare_only:
        write_outputs(load_saved(args.out_dir, engines), args.out_dir)
        return 0

    spec = run_scorecard.get_skill_spec(args.skill)
    golden = run_scorecard.load_golden(args.golden or spec.golden_path)
    if args.limit is not None:
        golden = golden[: args.limit]
    if args.all_providers:
        providers: list[str | None] = run_scorecard.order_providers(run_scorecard.enumerate_providers())
    elif args.provider:
        providers = [args.provider]
    else:
        providers = [None]
    print(f"Head-to-head: {spec.name}, {len(golden)} cases × {providers} × {engines} (mode={args.mode})")
    results = run_head_to_head(
        golden,
        providers=providers,
        mode=args.mode,
        spec=spec,
        engines=engines,
        use_judge=args.judge,
        # Incremental save after each provider, so an interrupted run keeps its rows.
        on_provider_done=lambda so_far: write_outputs(so_far, args.out_dir, quiet=True),
    )
    write_outputs(results, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
