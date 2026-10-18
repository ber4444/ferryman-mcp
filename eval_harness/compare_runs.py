"""
Compare two scorecard snapshots case by case: did a change move the metric?

    python eval_harness/compare_runs.py \\
        eval_harness/scorecard-nl2sql.baseline.json \\
        eval_harness/scorecard-nl2sql.column-discipline.json

A hill-climb claim is two numbers ("EX went from X% to Y%"), but the two runs
share their cases, so the honest test is paired: which cases flipped? A change
that fixes 9 cases and breaks 5 is a +4 point move with a lot of churn, and on
100 cases that is not distinguishable from noise. This prints, per provider:

  * before / after pass counts on the cases both runs share;
  * fixed (fail → pass) and broke (pass → fail) counts;
  * the exact McNemar p-value — a two-sided binomial test on the flips, the
    standard test for paired binary outcomes. It only looks at discordant
    cases, so it is unaffected by how many cases both runs got right.

Rows that errored on either side (timeouts, transport failures) are left out of
the paired comparison and counted separately: an infra failure says nothing
about the prompt. That differs on purpose from the scorecard's headline, which
counts an errored row as a miss.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path


def mcnemar_exact_p(fixed: int, broke: int) -> float:
    """Two-sided exact McNemar p-value: P(a split at least this lopsided)
    when each discordant case is equally likely to flip either way."""
    n = fixed + broke
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(fixed, broke) + 1)) / 2**n
    return min(1.0, 2 * tail)


def _passed(row: dict, key: str) -> bool:
    return any(s.get("key") == key and s.get("passed") for s in row.get("rule_scores", []))


@dataclass
class Tally:
    pairs: int = 0
    before: int = 0
    after: int = 0
    fixed: int = 0
    broke: int = 0
    fixed_ids: list[str] = field(default_factory=list)
    broke_ids: list[str] = field(default_factory=list)

    def add(self, case_id: str, b: bool, a: bool) -> None:
        self.pairs += 1
        self.before += b
        self.after += a
        if a and not b:
            self.fixed += 1
            self.fixed_ids.append(case_id)
        elif b and not a:
            self.broke += 1
            self.broke_ids.append(case_id)

    @property
    def p_value(self) -> float:
        return mcnemar_exact_p(self.fixed, self.broke)


@dataclass
class ProviderComparison:
    provider: str
    total: Tally = field(default_factory=Tally)
    groups: dict[str, dict[object, Tally]] = field(default_factory=dict)
    errored: int = 0
    unmatched: int = 0


def _index(rows: list[dict]) -> dict[tuple[str, str], dict]:
    return {(r["provider"], r["id"]): r for r in rows}


def compare(before: list[dict], after: list[dict], key: str, by: tuple[str, ...] = ()) -> list[ProviderComparison]:
    b_idx, a_idx = _index(before), _index(after)
    providers = sorted({p for p, _ in b_idx} | {p for p, _ in a_idx})
    out = []
    for provider in providers:
        cmp = ProviderComparison(provider)
        ids = sorted({i for p, i in b_idx if p == provider} | {i for p, i in a_idx if p == provider})
        for case_id in ids:
            b_row, a_row = b_idx.get((provider, case_id)), a_idx.get((provider, case_id))
            if b_row is None or a_row is None:
                cmp.unmatched += 1
                continue
            if b_row.get("error") or a_row.get("error"):
                cmp.errored += 1
                continue
            b, a = _passed(b_row, key), _passed(a_row, key)
            cmp.total.add(case_id, b, a)
            meta = a_row.get("meta") or b_row.get("meta") or {}
            for field_name in by:
                value = meta.get(field_name)
                if value is not None:
                    cmp.groups.setdefault(field_name, {}).setdefault(value, Tally()).add(case_id, b, a)
        out.append(cmp)
    return out


def _settings(rows: list[dict]) -> dict[str, set]:
    """Non-breakdown meta values that are constant per run (e.g. sampleRows) —
    shown so a comparison makes its input-representation change explicit."""
    seen: dict[str, set] = {}
    for r in rows:
        for k, v in (r.get("meta") or {}).items():
            if isinstance(v, (int, float, bool)):
                seen.setdefault(k, set()).add(v)
    return seen


def _pct(k: int, n: int) -> str:
    return f"{k / n:.0%}" if n else "—"


def render(before_path: Path, after_path: Path, before: list[dict], after: list[dict],
           key: str, by: tuple[str, ...]) -> str:
    comparisons = compare(before, after, key, by)
    lines = [
        f"# {key}: {before_path.name} → {after_path.name}",
        "",
        "| Provider | Pairs | Before | After | Δ | Fixed | Broke | McNemar p |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in comparisons:
        t = c.total
        delta = (t.after - t.before) / t.pairs * 100 if t.pairs else 0.0
        lines.append(
            f"| {c.provider} | {t.pairs} | {t.before}/{t.pairs} ({_pct(t.before, t.pairs)}) | "
            f"{t.after}/{t.pairs} ({_pct(t.after, t.pairs)}) | {delta:+.0f} pp | {t.fixed} | {t.broke} | "
            f"{t.p_value:.3g} |"
        )
    notes = []
    for c in comparisons:
        if c.errored:
            notes.append(f"{c.provider}: {c.errored} case(s) left out — errored in one of the runs")
        if c.unmatched:
            notes.append(f"{c.provider}: {c.unmatched} case(s) left out — present in only one run (different --split?)")
    if notes:
        lines += ["", *[f"- {n}" for n in notes]]
    sb, sa = _settings(before), _settings(after)
    changed = {k for k in sb.keys() | sa.keys() if sb.get(k) != sa.get(k)}
    if changed:
        lines += ["", "Input settings differ between runs: " + ", ".join(
            f"{k} {sorted(sb.get(k, set()))} → {sorted(sa.get(k, set()))}" for k in sorted(changed))]
    for field_name in by:
        # Golden-set order (easy, medium, hard, extra), not alphabetical.
        values = list(dict.fromkeys(
            (r.get("meta") or {}).get(field_name) for r in after + before
            if (r.get("meta") or {}).get(field_name) is not None
        ))
        if not values:
            continue
        lines += ["", f"## By {field_name}", "", "| Provider | " + " | ".join(map(str, values)) + " |",
                  "|---|" + "---|" * len(values)]
        for c in comparisons:
            cells = []
            for v in values:
                t = c.groups.get(field_name, {}).get(v)
                cells.append(
                    f"{_pct(t.before, t.pairs)} → {_pct(t.after, t.pairs)} (+{t.fixed}/−{t.broke})" if t else "—"
                )
            lines.append(f"| {c.provider} | " + " | ".join(cells) + " |")
    for c in comparisons:
        if c.total.fixed_ids or c.total.broke_ids:
            lines += ["", f"## Flipped cases — {c.provider}", ""]
            lines.append(f"- fixed: {', '.join(c.total.fixed_ids) or '—'}")
            lines.append(f"- broke: {', '.join(c.total.broke_ids) or '—'}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Paired comparison of two scorecard JSON snapshots.")
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--key", default="executionAccuracy", help="scorer key to compare (default: executionAccuracy)")
    ap.add_argument("--by", action="append", default=None,
                    help="CaseResult.meta field to break down by; repeatable (default: split, hardness)")
    ap.add_argument("--out", type=Path, help="also write the markdown here")
    args = ap.parse_args(argv)
    by = tuple(args.by) if args.by else ("split", "hardness")
    before = json.loads(args.before.read_text())
    after = json.loads(args.after.read_text())
    text = render(args.before, args.after, before, after, args.key, by)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
