"""
Deterministic execution-accuracy scorers for the nl2sql skill.

Same posture as chess_scorers.py: the golden set is a vendored subset of a
public benchmark (Spider 1.0 dev), the model's answer is extracted through a
fixed output contract, and the verdict is objective. No judge in the loop for
the floor.

The floor is **execution accuracy (EX)**: run the gold SQL and the predicted
SQL against the same database and compare the result tables. Exact string
match on SQL would punish correct rewrites (`a JOIN b` vs a subquery); EX asks
the question a caller of the skill actually cares about — did the query return
the right rows?

The comparison is ported from the Spider test-suite evaluator
(taoyds/test-suite-sql-eval, `exec_eval.py`, Apache-2.0 — the same license as
this repo), including its defaults:

  * results are compared as bags of rows, under any column permutation;
  * row order matters only when the gold query has ORDER BY;
  * DISTINCT is stripped from both queries before execution
    (`keep_distinct=False`, the evaluator's default — Spider's annotations use
    DISTINCT inconsistently, so the official metric ignores it);
  * `> =` / `< =` / `! =` are re-joined before execution.

One deliberate deviation: `get_constraint_permutation` samples rows with the
global `random` module. Here it uses a seeded RNG, so a scorecard is
reproducible run to run. The sampling only prunes impossible column mappings,
so the seed changes speed, never the verdict.

Three scorers, reported separately:

  * ``executionAccuracy`` — the floor, and the skill's headline metric.
  * ``executes``          — the predicted SQL runs without error. Diagnostic:
    separates schema/syntax failures from wrong-answer failures, which point
    at different fixes when hill-climbing.
  * ``readOnly``          — the predicted SQL is a single read-only statement.
    An NL2SQL skill that emits DROP TABLE is a product failure whether or not
    the rows would have matched. (Execution is sandboxed regardless — see
    ``execute``.)
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from pathlib import Path
from typing import Any, List, Set, Tuple

from eval_harness.rule_scorers import ScoreResult

DB_DIR = Path(__file__).resolve().parent / "golden" / "nl2sql_dbs"

# Wall-clock budget for one query. The test-suite evaluator uses 60 s against
# databases with up to millions of rows; these are small, so a query that runs
# for 10 s is a runaway cross join, not a slow correct answer.
EXEC_TIMEOUT_SECONDS = 10.0


# --- the output contract ---------------------------------------------------
#
# The skill ends with one fenced ```sql block. When several blocks appear, the
# LAST one wins — the same rule as ChessQA's FINAL ANSWER contract (a model
# that drafts, then corrects itself). `<think>` deliberation is stripped first,
# because reasoning models draft SQL there that they later reject.

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_SQL_FENCE = re.compile(r"```[ \t]*sql[ \t]*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_ANY_FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
_FINAL_ANSWER = re.compile(r"FINAL ANSWER:\s*(.+?)(?:\n|$)", re.IGNORECASE)


def extract_sql(text: str) -> tuple[str, bool]:
    """
    Pull the final SQL query out of a skill response.

    Order of preference: the last ```sql fence, then the last unlabeled fence,
    then a single-line ``FINAL ANSWER:`` (the chess contract, accepted so a
    model that follows the house style for other skills still gets scored).
    Returns ``(sql, found)``; a trailing semicolon is dropped so a lone
    statement isn't mistaken for two.
    """
    body = _THINK_BLOCK.sub(" ", text or "")
    for pattern in (_SQL_FENCE, _ANY_FENCE, _FINAL_ANSWER):
        matches = pattern.findall(body)
        if matches:
            sql = matches[-1].strip().strip("`").strip()
            sql = re.sub(r";\s*$", "", sql).strip()
            if sql:
                return sql, True
    return "", False


# --- ported from taoyds/test-suite-sql-eval exec_eval.py (Apache-2.0) ------
#
# Kept as close to the original as the seeded RNG allows, so a verdict here
# means what it means in the published Spider evaluation.

_PERMUTATION_RNG_SEED = 0


def permute_tuple(element: Tuple, perm: Tuple) -> Tuple:
    assert len(element) == len(perm)
    return tuple([element[i] for i in perm])


def unorder_row(row: Tuple) -> Tuple:
    return tuple(sorted(row, key=lambda x: str(x) + str(type(x))))


# unorder each row in the table
# [result_1 and result_2 has the same bag of unordered row]
# is a necessary condition of
# [result_1 and result_2 are equivalent in denotation]
def quick_rej(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    s1 = [unorder_row(row) for row in result1]
    s2 = [unorder_row(row) for row in result2]
    if order_matters:
        return s1 == s2
    else:
        return set(s1) == set(s2)


# return whether two bag of relations are equivalent
def multiset_eq(l1: List, l2: List) -> bool:
    if len(l1) != len(l2):
        return False
    d = defaultdict(int)
    for e in l1:
        d[e] = d[e] + 1
    for e in l2:
        d[e] = d[e] - 1
        if d[e] < 0:
            return False
    return True


def get_constraint_permutation(tab1_sets_by_columns: List[Set], result2: List[Tuple]):
    num_cols = len(result2[0])
    perm_constraints = [{i for i in range(num_cols)} for _ in range(num_cols)]
    if num_cols <= 3:
        return product(*perm_constraints)

    # we sample 20 rows and constrain the space of permutations
    rng = random.Random(_PERMUTATION_RNG_SEED)
    for _ in range(20):
        random_tab2_row = rng.choice(result2)

        for tab1_col in range(num_cols):
            for tab2_col in set(perm_constraints[tab1_col]):
                if random_tab2_row[tab2_col] not in tab1_sets_by_columns[tab1_col]:
                    perm_constraints[tab1_col].remove(tab2_col)
    return product(*perm_constraints)


# check whether two denotations are correct
def result_eq(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    if len(result1) == 0 and len(result2) == 0:
        return True

    # if length is not the same, then they are definitely different bag of rows
    if len(result1) != len(result2):
        return False

    num_cols = len(result1[0])

    # if the results do not have the same number of columns, they are different
    if len(result2[0]) != num_cols:
        return False

    # unorder each row and compare whether the denotation is the same
    # this can already find most pair of denotations that are different
    if not quick_rej(result1, result2, order_matters):
        return False

    # find a permutation of column order and a permutation of row order,
    # s.t. result_1 is the same as result_2
    tab1_sets_by_columns = [{row[i] for row in result1} for i in range(num_cols)]

    for perm in get_constraint_permutation(tab1_sets_by_columns, result2):
        if len(perm) != len(set(perm)):
            continue
        if num_cols == 1:
            result2_perm = result2
        else:
            result2_perm = [permute_tuple(element, perm) for element in result2]
        if order_matters:
            if result1 == result2_perm:
                return True
        else:
            # in fact the first condition must hold if the second condition holds
            # but the first is way more efficient implementation-wise
            # and we use it to quickly reject impossible candidates
            if set(result1) == set(result2_perm) and multiset_eq(result1, result2_perm):
                return True
    return False


def replace_cur_year(query: str) -> str:
    return re.sub(r"YEAR\s*\(\s*CURDATE\s*\(\s*\)\s*\)\s*", "2020", query, flags=re.IGNORECASE)


# postprocess the model predictions to avoid execution errors
# e.g. removing spaces between ">" and "="
def postprocess(query: str) -> str:
    query = query.replace("> =", ">=").replace("< =", "<=").replace("! =", "!=")
    return query


# The original drops every token whose value is `distinct` after an sqlparse
# tokenization. sqlparse keeps quoted strings and identifiers as single tokens,
# so the equivalent without the dependency is: remove the bare word DISTINCT
# outside quotes, leaving the surrounding whitespace exactly as it was.
_QUOTED = re.compile(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\])")
_DISTINCT_WORD = re.compile(r"\bdistinct\b", re.IGNORECASE)


def remove_distinct(s: str) -> str:
    parts = _QUOTED.split(s)
    # split() with one capture group alternates: unquoted, quoted, unquoted, ...
    return "".join(p if i % 2 else _DISTINCT_WORD.sub("", p) for i, p in enumerate(parts))


# --- sandboxed execution ---------------------------------------------------

# sqlite3 authorizer action codes a read-only SELECT needs. Everything else —
# INSERT/UPDATE/DELETE, DDL, PRAGMA, ATTACH, transactions — is denied before
# the statement compiles.
_READ_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _read_only_authorizer(action, *_args):
    return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY


@lru_cache(maxsize=None)
def _connect(db_id: str) -> sqlite3.Connection:
    """
    Build the vendored database in memory, once per process.

    The dump is plain SQLite SQL (see build_nl2sql_golden.py), loaded into a
    private in-memory database — nothing a query does can touch the file on
    disk, and ``query_only`` plus the authorizer refuse writes anyway.
    """
    path = DB_DIR / f"{db_id}.sql"
    if not path.is_file():
        raise FileNotFoundError(f"no vendored database for db_id {db_id!r} at {path}")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(path.read_text(encoding="utf-8"))
    conn.execute("PRAGMA query_only = ON")
    # Same as the test-suite evaluator: tolerate stray bytes in TEXT columns.
    conn.text_factory = lambda b: b.decode(errors="ignore")
    return conn


@dataclass
class ExecResult:
    ok: bool
    rows: list[tuple] | None = None
    error: str | None = None
    timed_out: bool = False
    # True when the row cap stopped the fetch early (more rows existed).
    truncated: bool = False


def execute(db_id: str, sql: str, *, max_rows: int | None = None,
            timeout_s: float = EXEC_TIMEOUT_SECONDS) -> ExecResult:
    """
    Run one statement against the vendored database, read-only and time-boxed.

    [max_rows] caps the fetch: the scorer passes ``len(gold) + 1``, because a
    prediction with more rows than gold can never match, and an unbounded
    cross join should cost a comparison, not the harness's memory.
    """
    conn = _connect(db_id)
    deadline = time.monotonic() + timeout_s
    timed_out = False

    def _progress() -> int:
        nonlocal timed_out
        if time.monotonic() > deadline:
            timed_out = True
            return 1  # non-zero aborts the statement
        return 0

    conn.set_authorizer(_read_only_authorizer)
    conn.set_progress_handler(_progress, 10_000)
    try:
        cur = conn.execute(replace_cur_year(sql))
        if max_rows is None:
            rows = cur.fetchall()
            truncated = False
        else:
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows] if truncated else rows
        cur.close()
        return ExecResult(ok=True, rows=[tuple(r) for r in rows], truncated=truncated)
    except Exception as e:  # noqa: BLE001 — any failure is a verdict, not a crash
        if timed_out:
            return ExecResult(ok=False, error=f"timed out after {timeout_s:g}s", timed_out=True)
        return ExecResult(ok=False, error=f"{type(e).__name__}: {e}")
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)


def result_fingerprint(rows: list[tuple]) -> str:
    """Order-insensitive hash of a result table. Pins each gold result in the
    golden set, so an accidental edit to a vendored database fails a test
    instead of silently changing what "correct" means."""
    canon = sorted(repr(r) for r in rows)
    return hashlib.sha256(json.dumps(canon).encode()).hexdigest()


def execute_gold(case: dict) -> ExecResult:
    """Execute a case's gold SQL exactly the way the EX scorer does."""
    g_str = remove_distinct(postprocess(case["goldSql"]))
    return execute(case["input"]["db_id"], g_str)


# --- read-only gate --------------------------------------------------------

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# `replace(...)` is an everyday string function, so REPLACE only counts in its
# statement form (`REPLACE INTO`); `INSERT OR REPLACE` is caught by INSERT.
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|pragma|vacuum|reindex|analyze)\b"
    r"|\breplace\s+into\b",
    re.IGNORECASE,
)


def read_only_violation(sql: str) -> str | None:
    """Why ``sql`` is not a single read-only query, or None if it is."""
    code = _QUOTED.sub("''", sql)  # literals and quoted identifiers can't be keywords
    code = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", code)).strip()
    if re.search(r";\s*\S", code):
        return "more than one statement"
    first = re.match(r"\(*\s*(\w+)", code)
    if not first or first.group(1).lower() not in ("select", "with", "values"):
        return f"starts with {first.group(1) if first else 'nothing'!r}, not SELECT/WITH"
    m = _WRITE_KEYWORDS.search(code)
    if m:
        return f"contains write/admin keyword {m.group(0).upper()!r}"
    return None


# --- evaluation ------------------------------------------------------------


@dataclass
class Evaluation:
    """Everything the three scorers need, computed once per output."""

    sql: str
    found: bool
    gold: ExecResult | None = None
    pred: ExecResult | None = None
    match: bool = False
    order_matters: bool = False


def evaluate(output: str, case: dict) -> Evaluation:
    sql, found = extract_sql(output)
    ev = Evaluation(sql=sql, found=found)
    if not found:
        return ev
    p_str = remove_distinct(postprocess(sql))
    g_str = remove_distinct(postprocess(case["goldSql"]))
    # if there is order by in query, then we assume order of the rows matter
    ev.order_matters = "order by" in g_str.lower()
    ev.gold = execute(case["input"]["db_id"], g_str)
    if not ev.gold.ok:
        return ev
    ev.pred = execute(case["input"]["db_id"], p_str, max_rows=len(ev.gold.rows) + 1)
    if ev.pred.ok:
        ev.match = result_eq(ev.gold.rows, ev.pred.rows, order_matters=ev.order_matters)
    return ev


def _ex_result(ev: Evaluation) -> ScoreResult:
    if not ev.found:
        return ScoreResult("executionAccuracy", False, "no SQL found — expected a final ```sql block")
    if ev.gold is None or not ev.gold.ok:
        # A gold query that fails is a fixture bug — fail loudly, never pass.
        return ScoreResult("executionAccuracy", False, f"fixture error: gold SQL failed: {ev.gold.error if ev.gold else '?'}")
    if not ev.pred.ok:
        return ScoreResult("executionAccuracy", False, f"prediction did not execute: {ev.pred.error}")
    if ev.match:
        return ScoreResult("executionAccuracy", True, f"result matches gold ({len(ev.gold.rows)} rows)")
    got = f"{len(ev.pred.rows)}+" if ev.pred.truncated else str(len(ev.pred.rows))
    shape = f"{got} rows vs gold {len(ev.gold.rows)}"
    if ev.pred.rows and ev.gold.rows and len(ev.pred.rows[0]) != len(ev.gold.rows[0]):
        shape += f"; {len(ev.pred.rows[0])} columns vs gold {len(ev.gold.rows[0])}"
    order = " (row order checked: gold has ORDER BY)" if ev.order_matters else ""
    return ScoreResult("executionAccuracy", False, f"result differs from gold — {shape}{order}")


def _executes_result(ev: Evaluation) -> ScoreResult:
    if not ev.found:
        return ScoreResult("executes", False, "no SQL found")
    if ev.pred is None:
        return ScoreResult("executes", False, "not run — gold SQL failed (fixture error)")
    if ev.pred.ok:
        return ScoreResult("executes", True, "prediction executed")
    return ScoreResult("executes", False, ev.pred.error or "execution failed")


def _read_only_result(ev: Evaluation) -> ScoreResult:
    if not ev.found:
        return ScoreResult("readOnly", True, "no SQL emitted (executionAccuracy scores the miss)")
    why = read_only_violation(ev.sql)
    if why:
        return ScoreResult("readOnly", False, f"not a single read-only query: {why}")
    return ScoreResult("readOnly", True, "single read-only query")


def score_execution_accuracy(output: str, case: dict) -> ScoreResult:
    return _ex_result(evaluate(output, case))


def score_executes(output: str, case: dict) -> ScoreResult:
    return _executes_result(evaluate(output, case))


def score_read_only(output: str, case: dict) -> ScoreResult:
    return _read_only_result(evaluate(output, case))


def score_for_case(output: str, case: dict) -> list[ScoreResult]:
    """All three scorers from one execution. Shape matches chess_scorers.score_for_case."""
    ev = evaluate(output, case)
    return [_read_only_result(ev), _executes_result(ev), _ex_result(ev)]


# --- the skill's input -----------------------------------------------------
#
# The golden set stores only the question and db_id. The schema the model sees
# is rendered here, from the same vendored database the queries run on, so the
# model is always shown exactly what is executable. Rendering is code, not
# data, on purpose: the schema representation is a hill-climbing lever (see
# SAMPLE_ROWS_ENV), and keeping it out of the golden file keeps the benchmark
# frozen while the representation changes.

# Rows of example data to append under each CREATE TABLE ("CREATE TABLE +
# SELECT 3", Rajkumar et al. 2022). 0 = schema only, the default.
SAMPLE_ROWS_ENV = "FERRY_NL2SQL_SAMPLE_ROWS"


def sample_rows_setting() -> int:
    try:
        return max(int(os.environ.get(SAMPLE_ROWS_ENV, "0") or 0), 0)
    except ValueError:
        return 0


def render_schema(db_id: str, sample_rows: int = 0) -> str:
    """The database's CREATE TABLE statements, optionally with example rows."""
    conn = _connect(db_id)
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
    ).fetchall()
    blocks = []
    for name, ddl in tables:
        block = ddl.strip() + ";"
        if sample_rows > 0:
            cur = conn.execute(f'SELECT * FROM "{name}" LIMIT {int(sample_rows)}')
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            lines = [" | ".join(cols)] + [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows]
            block += f"\n/*\n{len(rows)} example rows from {name}:\n" + "\n".join(lines) + "\n*/"
        blocks.append(block)
    return "\n\n".join(blocks)


def build_input(case: dict) -> dict[str, Any]:
    """The JSON object the nl2sql skill receives for one golden case."""
    db_id = case["input"]["db_id"]
    return {
        "question": case["input"]["question"],
        "db_id": db_id,
        "schema": render_schema(db_id, sample_rows=sample_rows_setting()),
    }


def case_meta(case: dict) -> dict[str, Any]:
    """Per-case fields recorded on each scorecard row, for breakdowns and for
    telling apart runs made with different input representations."""
    return {
        "hardness": case.get("hardness"),
        "split": case.get("split"),
        "db_id": case.get("input", {}).get("db_id"),
        "sampleRows": sample_rows_setting(),
    }
