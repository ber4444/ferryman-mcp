"""
Build the nl2sql golden set: a stratified, validated subset of Spider 1.0 dev.

This is a one-time vendoring script, committed so the subset is reproducible
and reviewable — not something the harness runs. It reads three local files
(download them yourself; nothing is fetched here):

  --spider-dev      taoyds/spider  evaluation_examples/examples/dev.json
                    The official questions and gold SQL (1,034 cases).
  --intercode-dump  princeton-nlp/intercode  data/sql/spider/ic_spider_dbs.sql
                    The 20 Spider dev databases as one MySQL dump.
  --intercode-dev   princeton-nlp/intercode  data/sql/spider/ic_spider_dev.json
                    Spider's own `eval_hardness` label for every dev question.

Why InterCode for the databases: the original Spider SQLite files are only
distributed through Google Drive. InterCode (Yang et al., 2023, MIT) publishes
the same databases as a MySQL dump in a plain git repo. Two caveats follow, and
both are recorded in the golden set's provenance and the README:

  1. InterCode's converter imported each database from Spider's `schema.sql`
     when one existed, which can carry fewer rows than the `.sqlite` file —
     and `wta_1` has no rows at all (it is excluded). Gold and predicted SQL
     run on the *same* vendored data, so execution accuracy stays
     self-consistent; it is not comparable to Spider leaderboard numbers.
  2. InterCode resolved some primary/foreign-key type mismatches before
     conversion. Values are otherwise as dumped.

Every gold query is executed through nl2sql_scorers' own path (the scorer's
DISTINCT stripping, the read-only sandbox). A case is kept only if:

  * its gold query succeeds;
  * its result can discriminate: non-empty, and not a single NULL or 0 — a
    result any wrong-but-empty query would also produce;
  * its result does not depend on how rows happen to be stored. The gold is
    re-run on a copy of the database with every table's rows reversed; if the
    answer changes (ties under ORDER BY, SQLite picking an arbitrary row for a
    bare GROUP BY column), a correct prediction could fail by luck.

The split is by **database**, not by question: `hillclimb` cases come from one
set of databases and `heldout` cases from a disjoint set. Prompt edits made
while looking at hillclimb failures can't leak schema-specific fixes into the
held-out score — the same cross-domain design Spider itself uses.

Usage:
    python -I eval_harness/build_nl2sql_golden.py \\
        --spider-dev   <spider>/evaluation_examples/examples/dev.json \\
        --intercode-dump <intercode>/data/sql/spider/ic_spider_dbs.sql \\
        --intercode-dev  <intercode>/data/sql/spider/ic_spider_dev.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval_harness import nl2sql_scorers  # noqa: E402

_HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = _HERE / "golden" / "nl2sql_golden.json"
DEFAULT_DB_DIR = _HERE / "golden" / "nl2sql_dbs"

HARDNESS = ["easy", "medium", "hard", "extra"]

# Database → split. Chosen so each split has enough valid cases in every
# hardness bucket (see the stats this script prints) and neither split is
# dominated by one domain. world_1 (the largest, and the one with the most
# hard/extra questions) anchors the held-out side.
HILLCLIMB_DBS = {
    "car_1",
    "dog_kennels",
    "student_transcripts_tracking",
    "network_1",
    "pets_1",
    "orchestra",
    "course_teach",
    "museum_visit",
    "battle_death",
    "voter_1",
}
HELDOUT_DBS = {
    "world_1",
    "cre_Doc_Template_Mgt",
    "flight_2",
    "tvshow",
    "concert_singer",
    "poker_player",
    "employee_hire_evaluation",
    "singer",
    "real_estate_properties",
}
EXCLUDED_DBS = {"wta_1": "no rows in the InterCode dump"}

PROVENANCE = {
    "source": "Spider 1.0 dev (Yu et al., EMNLP 2018)",
    "license": "CC BY-SA 4.0",
    "url": "https://yale-lily.github.io/spider",
    "questionsAndGoldSql": "taoyds/spider evaluation_examples/examples/dev.json",
    "hardness": "Spider eval_hardness, via InterCode ic_spider_dev.json",
    "database": "InterCode ic_spider_dbs.sql (MySQL), re-imported into SQLite",
}


# --- MySQL dump → SQLite ---------------------------------------------------

_USE = re.compile(r"^USE `([^`]+)`;")
_CREATE = re.compile(r"^CREATE TABLE `([^`]+)` \($")
_END_CREATE = re.compile(r"^\)\s*ENGINE=")
_COLUMN = re.compile(r"^\s*`(?P<name>[^`]+)`\s+(?P<rest>.*?),?\s*$")
# Key column lists can carry MySQL prefix lengths (`col`(255)), hence `.*`.
_PK = re.compile(r"^\s*PRIMARY KEY \((?P<cols>.*)\),?\s*$")
_UNIQUE = re.compile(r"^\s*UNIQUE KEY `[^`]*` \((?P<cols>.*)\),?\s*$")
_FK = re.compile(
    r"^\s*CONSTRAINT `[^`]*` FOREIGN KEY \((?P<cols>[^)]*)\) "
    r"REFERENCES `(?P<ref>[^`]+)` \((?P<refcols>[^)]*)\)"
)
_INSERT = re.compile(r"^INSERT INTO `(?P<table>[^`]+)` VALUES (?P<values>.*);\s*$")
# MySQL-only column modifiers SQLite would either reject or misread.
_MYSQL_ONLY = re.compile(
    r"\s+(COLLATE\s+\w+|CHARACTER SET\s+\w+|AUTO_INCREMENT|ON UPDATE CURRENT_TIMESTAMP)",
    re.IGNORECASE,
)
_ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def _quote_cols(cols: str) -> str:
    # Only the backticked names: drops MySQL key prefix lengths like (255).
    return ", ".join(f'"{c}"' for c in re.findall(r"`([^`]+)`", cols))


def parse_values(s: str) -> list[tuple]:
    """Parse a mysqldump `VALUES (...),(...)` list into Python tuples.

    Handles exactly what mysqldump emits: single-quoted strings with backslash
    escapes, NULL, and bare numbers. Raises on anything else rather than
    guessing — a silently mangled value would change gold results.
    """
    rows: list[tuple] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] in ", ":
            i += 1
            continue
        if s[i] != "(":
            raise ValueError(f"expected '(' at {i}: {s[i:i + 40]!r}")
        i += 1
        row: list = []
        while True:
            c = s[i]
            if c == "'":
                i += 1
                buf = []
                while s[i] != "'":
                    if s[i] == "\\":
                        nxt = s[i + 1]
                        buf.append(_ESCAPES.get(nxt, nxt))
                        i += 2
                    else:
                        buf.append(s[i])
                        i += 1
                i += 1
                row.append("".join(buf))
            elif s.startswith("NULL", i):
                row.append(None)
                i += 4
            else:
                m = re.match(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s[i:])
                if not m:
                    raise ValueError(f"unparseable value at {i}: {s[i:i + 40]!r}")
                tok = m.group(0)
                row.append(float(tok) if (m.group(1) or m.group(2)) else int(tok))
                i += len(tok)
            if s[i] == ",":
                i += 1
            elif s[i] == ")":
                i += 1
                break
            else:
                raise ValueError(f"expected ',' or ')' at {i}: {s[i:i + 40]!r}")
        rows.append(tuple(row))
    return rows


def convert_dump(path: Path) -> dict[str, sqlite3.Connection]:
    """Replay a mysqldump file into one in-memory SQLite database per schema."""
    dbs: dict[str, sqlite3.Connection] = {}
    db = None
    table = None
    columns: list[str] = []
    constraints: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _USE.match(line)
        if m:
            db = m.group(1)
            dbs[db] = sqlite3.connect(":memory:")
            continue
        if table is None:
            m = _CREATE.match(line)
            if m:
                table, columns, constraints = m.group(1), [], []
                continue
            m = _INSERT.match(line)
            if m:
                rows = parse_values(m.group("values"))
                if rows:
                    marks = ", ".join("?" * len(rows[0]))
                    dbs[db].executemany(f'INSERT INTO "{m.group("table")}" VALUES ({marks})', rows)
            continue
        # inside a CREATE TABLE block
        if _END_CREATE.match(line):
            body = ",\n  ".join(columns + constraints)
            dbs[db].execute(f'CREATE TABLE "{table}" (\n  {body}\n)')
            table = None
            continue
        if (m := _PK.match(line)):
            constraints.append(f"PRIMARY KEY ({_quote_cols(m.group('cols'))})")
        elif (m := _FK.match(line)):
            constraints.append(
                f"FOREIGN KEY ({_quote_cols(m.group('cols'))}) "
                f'REFERENCES "{m.group("ref")}" ({_quote_cols(m.group("refcols"))})'
            )
        elif (m := _UNIQUE.match(line)):
            constraints.append(f"UNIQUE ({_quote_cols(m.group('cols'))})")
        elif re.match(r"^\s*(KEY|FULLTEXT KEY|INDEX) ", line):
            continue  # secondary indexes: not schema the model needs, not data
        elif (m := _COLUMN.match(line)):
            rest = _MYSQL_ONLY.sub("", m.group("rest")).strip()
            columns.append(f'"{m.group("name")}" {rest}')
        else:
            raise ValueError(f"unrecognized line inside CREATE TABLE {table!r}: {line!r}")
    for conn in dbs.values():
        conn.commit()
    return dbs


def sqlite_dump_text(db_id: str, conn: sqlite3.Connection) -> str:
    header = (
        f"-- Spider 1.0 dev database `{db_id}` (Yu et al., EMNLP 2018), CC BY-SA 4.0.\n"
        "-- Recovered from InterCode's MySQL conversion (princeton-nlp/intercode,\n"
        "-- data/sql/spider/ic_spider_dbs.sql) and re-imported into SQLite.\n"
        "-- Generated by eval_harness/build_nl2sql_golden.py — do not edit by hand:\n"
        "-- every gold result in nl2sql_golden.json is pinned to this data.\n"
    )
    return header + "\n".join(conn.iterdump()) + "\n"


# --- case selection --------------------------------------------------------


def _norm(s: str) -> str:
    return " ".join(s.split())


def degenerate(rows: list[tuple]) -> str | None:
    """Why a gold result can't discriminate right from wrong, or None."""
    if not rows:
        return "empty result"
    if len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] in (None, 0, 0.0):
        return f"single {rows[0][0]!r} result"
    if all(v is None for r in rows for v in r):
        return "all-NULL result"
    return None


def reversed_copy(conn: sqlite3.Connection) -> sqlite3.Connection:
    """The same database with every table's rows stored in reverse order."""
    rev = sqlite3.connect(":memory:")
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
    ).fetchall()
    for name, ddl in tables:
        rev.execute(ddl)
        rows = conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid DESC').fetchall()
        if rows:
            rev.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(rows[0]))})', rows)
    return rev


def order_dependent(case: dict, rev: sqlite3.Connection) -> bool:
    """Whether the gold result changes when the rows are stored in reverse —
    i.e. the "right answer" is partly an accident of storage order."""
    g_str = nl2sql_scorers.remove_distinct(nl2sql_scorers.postprocess(case["goldSql"]))
    gold = nl2sql_scorers.execute_gold(case)
    flipped = [tuple(r) for r in rev.execute(g_str).fetchall()]
    return not nl2sql_scorers.result_eq(gold.rows, flipped, order_matters="order by" in g_str.lower())


def select_cases(candidates: list[dict], per_bucket: int, seed: int) -> list[dict]:
    """Stratified by (split, hardness); round-robin across databases inside a
    bucket so no single schema dominates it."""
    rng = random.Random(seed)
    chosen = []
    for split in ("hillclimb", "heldout"):
        for hardness in HARDNESS:
            by_db: dict[str, list[dict]] = defaultdict(list)
            for c in candidates:
                if c["split"] == split and c["hardness"] == hardness:
                    by_db[c["input"]["db_id"]].append(c)
            for cases in by_db.values():
                rng.shuffle(cases)
            picked: list[dict] = []
            queues = [by_db[d] for d in sorted(by_db)]
            while len(picked) < per_bucket and any(queues):
                for q in queues:
                    if q and len(picked) < per_bucket:
                        picked.append(q.pop())
            if len(picked) < per_bucket:
                raise SystemExit(f"only {len(picked)} valid {split}/{hardness} cases (need {per_bucket})")
            chosen.extend(sorted(picked, key=lambda c: c["provenance"]["devIndex"]))
    return chosen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spider-dev", type=Path, required=True)
    ap.add_argument("--intercode-dump", type=Path, required=True)
    ap.add_argument("--intercode-dev", type=Path, required=True)
    ap.add_argument("--out-golden", type=Path, default=DEFAULT_GOLDEN)
    ap.add_argument("--out-db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--per-bucket", type=int, default=25, help="cases per (split, hardness) bucket")
    ap.add_argument("--seed", type=int, default=20181031)
    args = ap.parse_args(argv)

    dev = json.loads(args.spider_dev.read_text())
    hardness_of = {}
    for row in json.loads(args.intercode_dev.read_text()):
        hardness_of[(row["db"], _norm(row["query"]), _norm(row["gold"]))] = row["hardness"]

    dbs = convert_dump(args.intercode_dump)
    dumps = {db_id: sqlite_dump_text(db_id, conn) for db_id, conn in dbs.items()}
    unassigned = set(dumps) - HILLCLIMB_DBS - HELDOUT_DBS - set(EXCLUDED_DBS)
    if unassigned:
        raise SystemExit(f"databases with no split assignment: {sorted(unassigned)}")

    rejected: Counter = Counter()
    stats: dict[str, Counter] = defaultdict(Counter)
    candidates = []
    reversed_dbs: dict[str, sqlite3.Connection] = {}
    with tempfile.TemporaryDirectory() as staging:
        # Validate through the scorer's own loader and execution path.
        for db_id, text in dumps.items():
            (Path(staging) / f"{db_id}.sql").write_text(text, encoding="utf-8")
        nl2sql_scorers.DB_DIR = Path(staging)
        nl2sql_scorers._connect.cache_clear()

        for idx, ex in enumerate(dev):
            db_id = ex["db_id"]
            if db_id in EXCLUDED_DBS:
                rejected[f"excluded db ({EXCLUDED_DBS[db_id]})"] += 1
                continue
            hardness = hardness_of.get((db_id, _norm(ex["question"]), _norm(ex["query"])))
            if hardness is None:
                rejected["no hardness label"] += 1
                continue
            case = {
                "id": f"spider_dev_{idx:04d}",
                "input": {"question": ex["question"], "db_id": db_id},
                "goldSql": ex["query"],
                "hardness": hardness,
                "split": "hillclimb" if db_id in HILLCLIMB_DBS else "heldout",
            }
            gold = nl2sql_scorers.execute_gold(case)
            if not gold.ok:
                rejected["gold SQL fails on vendored data"] += 1
                continue
            why = degenerate(gold.rows)
            if why:
                rejected[f"gold {why}"] += 1
                continue
            if db_id not in reversed_dbs:
                reversed_dbs[db_id] = reversed_copy(nl2sql_scorers._connect(db_id))
            if order_dependent(case, reversed_dbs[db_id]):
                rejected["gold depends on row storage order"] += 1
                continue
            case["goldRowCount"] = len(gold.rows)
            case["goldResultSha256"] = nl2sql_scorers.result_fingerprint(gold.rows)
            case["provenance"] = {**PROVENANCE, "devIndex": idx}
            candidates.append(case)
            stats[db_id][hardness] += 1

        nl2sql_scorers._connect.cache_clear()

    print(f"{len(dev)} dev cases → {len(candidates)} valid candidates")
    for reason, n in rejected.most_common():
        print(f"  rejected {n:4d}: {reason}")
    print(f"\n{'db':32s} split      " + " ".join(f"{h:>6s}" for h in HARDNESS))
    for db_id in sorted(stats):
        split = "hillclimb" if db_id in HILLCLIMB_DBS else "heldout"
        print(f"{db_id:32s} {split:10s} " + " ".join(f"{stats[db_id][h]:6d}" for h in HARDNESS))

    chosen = select_cases(candidates, args.per_bucket, args.seed)
    used = sorted({c["input"]["db_id"] for c in chosen})
    args.out_db_dir.mkdir(parents=True, exist_ok=True)
    for stale in args.out_db_dir.glob("*.sql"):
        stale.unlink()
    for db_id in used:
        (args.out_db_dir / f"{db_id}.sql").write_text(dumps[db_id], encoding="utf-8")
    args.out_golden.write_text(json.dumps(chosen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nwrote {len(chosen)} cases → {args.out_golden}")
    print(f"wrote {len(used)} databases → {args.out_db_dir}")
    for split in ("hillclimb", "heldout"):
        c = Counter(x["hardness"] for x in chosen if x["split"] == split)
        dbc = Counter(x["input"]["db_id"] for x in chosen if x["split"] == split)
        print(f"  {split}: {dict(c)} across {len(dbc)} databases {dict(dbc)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
