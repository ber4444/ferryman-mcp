"""
Controls for the nl2sql execution-accuracy scorer. No model, no network.

    python eval_harness/nl2sql_controls.py

Execution accuracy has a known weakness: a wrong query can return the right
rows by coincidence, and the smaller the database, the likelier that is. These
Spider databases are small (pets_1 has three pets), so before reading a
hill-climb delta it is worth knowing how often the scorer passes a query it
should fail. This measures it directly:

  * **identity** — every gold query, scored as the prediction. Must be 100%;
    anything less is a scorer or fixture bug.
  * **constant** — `SELECT 1` for every case. The chance floor.
  * **mutants** — each gold query with one plausible-but-wrong edit (flip a
    sort, swap MIN/MAX, flip a comparison, ...). The pass rate over mutants
    that still execute is the scorer's false-pass rate on this data: the
    share of near-miss queries EX cannot tell from the answer.

One mutant passes *by construction*: dropping DISTINCT. The official Spider
evaluator strips DISTINCT from both queries before running them (ported
as-is, see nl2sql_scorers), so EX is blind to it. It is reported so that blind
spot is a measured number, not a footnote.

A note on reading the mutant rate: it is not "the probability a wrong answer
is scored right". Mutants are one-token edits of a correct query — the hardest
wrong answers to catch. Real model errors (wrong table, missing join) are
usually caught. Treat the rate as an upper bound on coincidental matches.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval_harness import nl2sql_scorers  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden" / "nl2sql_golden.json"


def _sub_unquoted(sql: str, pattern: str, repl, count: int = 0) -> str:
    """re.sub applied only outside string literals / quoted identifiers.
    count=0 replaces every match; count=1 only the first one in the query."""
    parts = nl2sql_scorers._QUOTED.split(sql)
    remaining = count
    rx = re.compile(pattern, re.IGNORECASE)
    for i in range(0, len(parts), 2):
        if count and remaining <= 0:
            break
        new, n = rx.subn(repl, parts[i], count=remaining if count else 0)
        parts[i] = new
        remaining -= n
    return "".join(parts)


def flip_order(sql: str) -> str | None:
    if not re.search(r"\border\s+by\b", sql, re.IGNORECASE):
        return None
    if re.search(r"\bdesc\b", sql, re.IGNORECASE):
        return _sub_unquoted(sql, r"\bdesc\b", "ASC")
    if re.search(r"\basc\b", sql, re.IGNORECASE):
        return _sub_unquoted(sql, r"\basc\b", "DESC")
    # Implicit ASC: make the last ORDER BY descending, before any LIMIT.
    m = list(re.finditer(r"\border\s+by\b", sql, re.IGNORECASE))[-1]
    tail = sql[m.end():]
    lim = re.search(r"\blimit\b", tail, re.IGNORECASE)
    cut = m.end() + (lim.start() if lim else len(tail))
    return sql[:cut].rstrip() + " DESC " + sql[cut:].lstrip()


def swap_min_max(sql: str) -> str | None:
    if not re.search(r"\b(min|max)\s*\(", sql, re.IGNORECASE):
        return None
    # First occurrence only. Swapping every one turns `SELECT max(a), min(a)`
    # into `SELECT min(a), max(a)` — the same answer with its columns swapped,
    # which EX rightly accepts. That is not a wrong query, so not a control.
    return _sub_unquoted(sql, r"\b(min|max)(\s*\()",
                         lambda m: ("max" if m.group(1).lower() == "min" else "min") + m.group(2), count=1)


def flip_comparison(sql: str) -> str | None:
    flips = {">=": "<=", "<=": ">=", ">": "<", "<": ">"}
    if not re.search(r"(?<![<>!])(>=|<=|>|<)(?![>=])", sql):
        return None
    return _sub_unquoted(sql, r"(?<![<>!])(>=|<=|>|<)(?![>=])", lambda m: flips[m.group(1)], count=1)


def and_to_or(sql: str) -> str | None:
    if re.search(r"\bbetween\b", sql, re.IGNORECASE) or not re.search(r"\band\b", sql, re.IGNORECASE):
        return None
    return _sub_unquoted(sql, r"\band\b", "OR", count=1)


def shift_limit(sql: str) -> str | None:
    if not re.search(r"\blimit\s+\d+", sql, re.IGNORECASE):
        return None
    return _sub_unquoted(sql, r"\blimit\s+(\d+)", lambda m: f"LIMIT {int(m.group(1)) + 1}", count=1)


def avg_to_sum(sql: str) -> str | None:
    if not re.search(r"\bavg\s*\(", sql, re.IGNORECASE):
        return None
    return _sub_unquoted(sql, r"\bavg(\s*\()", r"sum\1")


def flip_in(sql: str) -> str | None:
    if re.search(r"\bnot\s+in\b", sql, re.IGNORECASE):
        return _sub_unquoted(sql, r"\bnot\s+in\b", "IN", count=1)
    if re.search(r"\bin\s*\(\s*select\b", sql, re.IGNORECASE):
        return _sub_unquoted(sql, r"\bin(\s*\(\s*select\b)", r"NOT IN\1", count=1)
    return None


def swap_set_op(sql: str) -> str | None:
    swaps = {"intersect": "UNION", "union": "INTERSECT", "except": "INTERSECT"}
    if not re.search(r"\b(intersect|union|except)\b", sql, re.IGNORECASE):
        return None
    return _sub_unquoted(sql, r"\b(intersect|union|except)\b", lambda m: swaps[m.group(1).lower()], count=1)


def drop_distinct(sql: str) -> str | None:
    if not re.search(r"\bdistinct\b", sql, re.IGNORECASE):
        return None
    return _sub_unquoted(sql, r"\bdistinct\s*", "")


MUTATIONS: dict[str, Callable[[str], str | None]] = {
    "flip ORDER BY direction": flip_order,
    "swap MIN/MAX": swap_min_max,
    "flip a comparison": flip_comparison,
    "AND → OR": and_to_or,
    "LIMIT n → n+1": shift_limit,
    "AVG → SUM": avg_to_sum,
    "IN ↔ NOT IN": flip_in,
    "swap set operator": swap_set_op,
    "drop DISTINCT (blind by design)": drop_distinct,
}


@dataclass
class Row:
    name: str
    applicable: int = 0
    executes: int = 0
    passed: int = 0

    @property
    def rate(self) -> str:
        return f"{self.passed / self.executes:.0%}" if self.executes else "—"


def _as_output(sql: str) -> str:
    return f"```sql\n{sql}\n```"


def run_controls(cases: list[dict]) -> tuple[Row, Row, list[Row], Row]:
    identity, constant = Row("identity (gold as prediction)"), Row("constant `SELECT 1`")
    for c in cases:
        for row, sql in ((identity, c["goldSql"]), (constant, "SELECT 1")):
            row.applicable += 1
            r = nl2sql_scorers.score_for_case(_as_output(sql), c)
            row.executes += r[1].passed
            row.passed += r[2].passed
    mutants = []
    pooled = Row("all mutants except DISTINCT")
    for name, mutate in MUTATIONS.items():
        row = Row(name)
        for c in cases:
            mutated = mutate(c["goldSql"])
            if mutated is None or mutated.strip() == c["goldSql"].strip():
                continue
            row.applicable += 1
            r = nl2sql_scorers.score_for_case(_as_output(mutated), c)
            if r[1].passed:
                row.executes += 1
                row.passed += r[2].passed
        mutants.append(row)
        if mutate is not drop_distinct:
            pooled.applicable += row.applicable
            pooled.executes += row.executes
            pooled.passed += row.passed
    return identity, constant, mutants, pooled


def render(cases: list[dict], split: str | None) -> str:
    identity, constant, mutants, pooled = run_controls(cases)
    scope = f"split `{split}`" if split else "all splits"
    lines = [
        f"# nl2sql scorer controls — {len(cases)} cases, {scope}",
        "",
        "| Control | Applicable | Executes | Passes EX | EX pass rate |",
        "|---|---|---|---|---|",
    ]
    for row in [identity, constant, *mutants, pooled]:
        lines.append(f"| {row.name} | {row.applicable} | {row.executes} | {row.passed} | {row.rate} |")
    lines += [
        "",
        "Pass rate is over mutants that still execute. Identity must be 100%. For mutants",
        "it is the false-pass rate: wrong queries EX cannot tell from the answer on this data.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the nl2sql EX scorer's identity, chance, and false-pass rates.")
    ap.add_argument("--golden", type=Path, default=GOLDEN)
    ap.add_argument("--split", help="hillclimb | heldout (default: both)")
    args = ap.parse_args(argv)
    cases = json.loads(args.golden.read_text())
    if args.split:
        cases = [c for c in cases if c.get("split") == args.split]
    print(render(cases, args.split))
    return 0


if __name__ == "__main__":
    sys.exit(main())
