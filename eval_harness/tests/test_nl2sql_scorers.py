"""
Hermetic tests for the nl2sql skill's eval: the execution-accuracy scorer, its
sandbox, the vendored golden set's integrity, and the vendoring script's
parsers. No network, no API keys — the databases are vendored SQL text.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import build_nl2sql_golden as build
from eval_harness import nl2sql_controls, nl2sql_scorers as ns

GOLDEN = json.loads(ns.DB_DIR.parent.joinpath("nl2sql_golden.json").read_text())


def _case(sql: str, db_id: str = "pets_1", question: str = "q") -> dict:
    return {"id": "t", "input": {"question": question, "db_id": db_id}, "goldSql": sql}


def _out(sql: str) -> str:
    return f"Reasoning.\n\n```sql\n{sql}\n```"


# --- extract_sql (the output contract) -------------------------------------


def test_extract_sql_from_sql_fence():
    assert ns.extract_sql("x\n```sql\nSELECT 1\n```") == ("SELECT 1", True)


def test_extract_sql_last_block_wins():
    text = "```sql\nSELECT 1\n```\nActually:\n```sql\nSELECT 2\n```"
    assert ns.extract_sql(text)[0] == "SELECT 2"


def test_extract_sql_strips_trailing_semicolon_and_case_of_fence_label():
    assert ns.extract_sql("```SQL\nSELECT a FROM t;\n```")[0] == "SELECT a FROM t"


def test_extract_sql_falls_back_to_unlabeled_fence_then_final_answer():
    assert ns.extract_sql("```\nSELECT 3\n```")[0] == "SELECT 3"
    assert ns.extract_sql("FINAL ANSWER: SELECT 4")[0] == "SELECT 4"


def test_extract_sql_ignores_drafts_inside_think():
    text = "<think>```sql\nSELECT draft\n```</think>\n```sql\nSELECT final\n```"
    assert ns.extract_sql(text)[0] == "SELECT final"
    assert ns.extract_sql("<think>```sql\nSELECT draft\n```</think> no answer") == ("", False)


def test_extract_sql_missing():
    assert ns.extract_sql("I think the answer is SELECT 1") == ("", False)
    assert ns.extract_sql("") == ("", False)


# --- the ported Spider comparison ------------------------------------------


def test_result_eq_ignores_column_order():
    assert ns.result_eq([(1, "a"), (2, "b")], [("a", 1), ("b", 2)], order_matters=False)


def test_result_eq_row_order_only_matters_when_asked():
    a, b = [(1,), (2,)], [(2,), (1,)]
    assert ns.result_eq(a, b, order_matters=False)
    assert not ns.result_eq(a, b, order_matters=True)


def test_result_eq_is_bag_semantics():
    # Same set of rows, different multiplicities: not equal.
    assert not ns.result_eq([(1,), (1,), (2,)], [(1,), (2,), (2,)], order_matters=False)


def test_result_eq_shape_mismatches():
    assert ns.result_eq([], [], order_matters=False)
    assert not ns.result_eq([(1,)], [], order_matters=False)
    assert not ns.result_eq([(1,)], [(1, 2)], order_matters=False)


def test_result_eq_wide_rows_are_deterministic():
    # >3 columns exercises the sampled-permutation path (seeded here).
    gold = [(i, i * 2, f"n{i}", i % 3, i * 1.5) for i in range(30)]
    pred = [(r[4], r[0], r[2], r[3], r[1]) for r in reversed(gold)]
    assert all(ns.result_eq(gold, pred, order_matters=False) for _ in range(5))


def test_remove_distinct_matches_evaluator_and_spares_literals():
    assert ns.remove_distinct("SELECT DISTINCT a FROM t") == "SELECT  a FROM t"
    assert ns.remove_distinct("SELECT count(distinct a) FROM t") == "SELECT count( a) FROM t"
    assert ns.remove_distinct("SELECT a FROM t WHERE b = 'distinct'") == "SELECT a FROM t WHERE b = 'distinct'"


def test_postprocess_rejoins_split_operators():
    assert ns.postprocess("a > = 1 AND b ! = 2") == "a >= 1 AND b != 2"


# --- read-only gate ----------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM pets",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SELECT replace(name, 'a', 'b') FROM pets",
        "SELECT 'DROP TABLE pets' AS s",
        'SELECT "update" FROM t',
        "(SELECT 1) UNION (SELECT 2)",
    ],
)
def test_read_only_accepts_queries(sql):
    assert ns.read_only_violation(sql) is None


@pytest.mark.parametrize(
    "sql, why",
    [
        ("DROP TABLE pets", "starts with"),
        ("DELETE FROM pets", "starts with"),
        ("SELECT 1; DROP TABLE pets", "more than one statement"),
        ("WITH x AS (SELECT 1) DELETE FROM pets", "DELETE"),
        ("PRAGMA table_info(pets)", "starts with"),
        ("SELECT 1 -- harmless\n; ATTACH 'x' AS y", "more than one statement"),
    ],
)
def test_read_only_rejects_writes(sql, why):
    assert why in (ns.read_only_violation(sql) or "")


def test_score_read_only_does_not_double_penalize_a_missing_answer():
    r = ns.score_read_only("no sql here", _case("SELECT 1"))
    assert r.passed and "executionAccuracy scores the miss" in r.reason


# --- sandboxed execution -----------------------------------------------------


def test_execute_denies_writes_and_leaves_data_intact():
    before = ns.execute("pets_1", "SELECT count(*) FROM pets").rows
    for sql in ("DELETE FROM pets", "DROP TABLE pets", "UPDATE pets SET weight = 0", "PRAGMA writable_schema = 1"):
        r = ns.execute("pets_1", sql)
        assert not r.ok, sql
    assert ns.execute("pets_1", "SELECT count(*) FROM pets").rows == before


def test_execute_rejects_multiple_statements():
    assert not ns.execute("pets_1", "SELECT 1; SELECT 2").ok


def test_execute_times_out_a_runaway_query():
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    r = ns.execute("pets_1", sql, timeout_s=0.2)
    assert not r.ok and r.timed_out and "timed out" in r.error


def test_execute_caps_rows_for_a_cross_join():
    r = ns.execute("world_1", "SELECT * FROM city, country", max_rows=5)
    assert r.ok and len(r.rows) == 5 and r.truncated


def test_unknown_db_id_is_a_loud_error():
    with pytest.raises(FileNotFoundError):
        ns.execute("no_such_db", "SELECT 1")


# --- scoring end to end on vendored data -------------------------------------


def test_gold_query_scores_full_marks():
    case = GOLDEN[0]
    results = ns.score_for_case(_out(case["goldSql"]), case)
    assert [r.key for r in results] == ["readOnly", "executes", "executionAccuracy"]
    assert all(r.passed for r in results)


def test_equivalent_rewrite_passes():
    case = _case("SELECT count(*) FROM pets WHERE weight > 10")
    rewrite = "SELECT sum(CASE WHEN weight > 10 THEN 1 ELSE 0 END) FROM pets"
    assert ns.score_execution_accuracy(_out(rewrite), case).passed


def test_permuted_columns_pass_but_extra_column_fails():
    case = _case("SELECT PetType, weight FROM pets")
    assert ns.score_execution_accuracy(_out("SELECT weight, PetType FROM pets"), case).passed
    r = ns.score_execution_accuracy(_out("SELECT PetID, PetType, weight FROM pets"), case)
    assert not r.passed and "3 columns vs gold 2" in r.reason


def test_order_matters_only_when_gold_orders():
    ordered = _case("SELECT PetID FROM pets ORDER BY PetID DESC")
    assert not ns.score_execution_accuracy(_out("SELECT PetID FROM pets ORDER BY PetID"), ordered).passed
    unordered = _case("SELECT PetID FROM pets")
    assert ns.score_execution_accuracy(_out("SELECT PetID FROM pets ORDER BY PetID DESC"), unordered).passed


def test_wrong_and_broken_predictions_fail_with_distinct_reasons():
    case = _case("SELECT count(*) FROM pets WHERE weight > 10")
    wrong = ns.score_for_case(_out("SELECT count(*) FROM pets WHERE weight < 10"), case)
    assert wrong[1].passed and not wrong[2].passed and "differs from gold" in wrong[2].reason
    broken = ns.score_for_case(_out("SELECT count(*) FROM pet_table"), case)
    assert not broken[1].passed and "no such table" in broken[1].reason
    assert not broken[2].passed and "did not execute" in broken[2].reason
    missing = ns.score_for_case("I'm not sure.", case)
    assert not missing[2].passed and "no SQL found" in missing[2].reason


def test_failing_gold_is_a_fixture_error_not_a_pass():
    r = ns.score_execution_accuracy(_out("SELECT 1"), _case("SELECT nope FROM nowhere"))
    assert not r.passed and r.reason.startswith("fixture error")


# --- the vendored golden set -------------------------------------------------


def test_golden_shape_is_stratified_and_split_by_database():
    assert len(GOLDEN) == 200
    assert len({c["id"] for c in GOLDEN}) == 200
    buckets = Counter((c["split"], c["hardness"]) for c in GOLDEN)
    assert set(buckets.values()) == {25} and len(buckets) == 8
    dbs = {split: {c["input"]["db_id"] for c in GOLDEN if c["split"] == split} for split in ("hillclimb", "heldout")}
    assert dbs["hillclimb"] and dbs["heldout"] and not (dbs["hillclimb"] & dbs["heldout"])


def test_every_golden_case_has_a_vendored_database_and_provenance():
    for c in GOLDEN:
        assert (ns.DB_DIR / f"{c['input']['db_id']}.sql").is_file(), c["id"]
        assert c["provenance"]["license"] == "CC BY-SA 4.0"


def test_gold_results_are_pinned():
    """Every gold query still returns exactly the result frozen at build time.
    Fails if a vendored database is edited, or if a SQLite upgrade changes a
    result — either way, "correct" would have silently moved."""
    for c in GOLDEN:
        r = ns.execute_gold(c)
        assert r.ok, (c["id"], r.error)
        assert len(r.rows) == c["goldRowCount"], c["id"]
        assert ns.result_fingerprint(r.rows) == c["goldResultSha256"], c["id"]
        assert build.degenerate(r.rows) is None, c["id"]


def test_gold_results_do_not_depend_on_storage_order():
    """A gold answer that changes when rows are stored in reverse (ties under
    ORDER BY, a bare GROUP BY column) would fail correct predictions by luck."""
    rev = {}
    for c in GOLDEN:
        db_id = c["input"]["db_id"]
        if db_id not in rev:
            rev[db_id] = build.reversed_copy(ns._connect(db_id))
        assert not build.order_dependent(c, rev[db_id]), c["id"]


# --- the skill's input -------------------------------------------------------


def test_build_input_renders_schema_from_vendored_db(monkeypatch):
    monkeypatch.delenv(ns.SAMPLE_ROWS_ENV, raising=False)
    payload = ns.build_input(_case("SELECT 1", question="How many pets?"))
    assert payload["question"] == "How many pets?" and payload["db_id"] == "pets_1"
    assert 'CREATE TABLE "pets"' in payload["schema"] and "example rows" not in payload["schema"]


def test_sample_rows_setting_adds_example_rows(monkeypatch):
    monkeypatch.setenv(ns.SAMPLE_ROWS_ENV, "2")
    schema = ns.build_input(_case("SELECT 1"))["schema"]
    assert "2 example rows from pets:" in schema and "PetID | PetType" in schema
    assert ns.case_meta({"input": {"db_id": "pets_1"}, "hardness": "easy", "split": "heldout"})["sampleRows"] == 2


def test_sample_rows_setting_ignores_garbage(monkeypatch):
    monkeypatch.setenv(ns.SAMPLE_ROWS_ENV, "lots")
    assert ns.sample_rows_setting() == 0


# --- controls ---------------------------------------------------------------


def test_controls_identity_is_perfect_and_chance_floor_is_low():
    identity, constant, mutants, pooled = nl2sql_controls.run_controls(GOLDEN)
    assert identity.passed == identity.applicable == len(GOLDEN)
    assert constant.passed / constant.applicable < 0.05
    # DISTINCT is stripped by the official evaluator, so this mutant can never fail.
    distinct = next(m for m in mutants if "DISTINCT" in m.name)
    assert distinct.passed == distinct.executes > 0
    assert pooled.executes > 100 and pooled.passed / pooled.executes < 0.15


def test_min_max_mutant_is_actually_wrong():
    # Swapping both would only permute the columns, which EX rightly accepts.
    assert nl2sql_controls.swap_min_max("SELECT max(a), min(a) FROM t") == "SELECT min(a), min(a) FROM t"


def test_mutations_leave_string_literals_alone():
    sql = "SELECT name FROM t WHERE note = 'max(a) > 1 AND b' AND x > 1"
    assert "'max(a) > 1 AND b'" in nl2sql_controls.flip_comparison(sql)
    assert "'max(a) > 1 AND b'" in nl2sql_controls.and_to_or(sql)


# --- the vendoring script's parsers ----------------------------------------


def test_parse_values_handles_mysqldump_literals():
    rows = build.parse_values(r"(1,'O\'Brien','a\\b',NULL,-2.5,'line\nbreak'),(2,'',NULL,0,3e2,'x')")
    assert rows == [(1, "O'Brien", "a\\b", None, -2.5, "line\nbreak"), (2, "", None, 0, 300.0, "x")]


def test_parse_values_refuses_to_guess():
    with pytest.raises(ValueError):
        build.parse_values("(1,_binary 'x')")


def test_convert_dump_replays_schema_keys_and_rows(tmp_path):
    dump = tmp_path / "d.sql"
    dump.write_text(
        "CREATE DATABASE IF NOT EXISTS `shop`;\n"
        "USE `shop`;\n"
        "CREATE TABLE `item` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `name` text COLLATE utf8mb4_general_ci,\n"
        "  `price` decimal(19,4) DEFAULT NULL,\n"
        "  `shop_id` int DEFAULT NULL,\n"
        "  PRIMARY KEY (`id`,`name`(255)),\n"
        "  KEY `item_fk` (`shop_id`),\n"
        "  CONSTRAINT `item_fk` FOREIGN KEY (`shop_id`) REFERENCES `shop` (`id`)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"
        "INSERT INTO `item` VALUES (1,'pen',1.5000,7),(2,'ink',NULL,7);\n"
    )
    conn = build.convert_dump(dump)["shop"]
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'item'").fetchone()[0]
    assert "COLLATE" not in ddl and "AUTO_INCREMENT" not in ddl
    assert 'PRIMARY KEY ("id", "name")' in ddl and 'REFERENCES "shop" ("id")' in ddl
    assert conn.execute("SELECT * FROM item ORDER BY id").fetchall() == [(1, "pen", 1.5, 7), (2, "ink", None, 7)]
    # The emitted dump is plain SQLite and reloads cleanly.
    text = build.sqlite_dump_text("shop", conn)
    fresh = sqlite3.connect(":memory:")
    fresh.executescript(text)
    assert fresh.execute("SELECT count(*) FROM item").fetchone() == (2,)


@pytest.mark.parametrize(
    "rows, degenerate",
    [([], True), ([(0,)], True), ([(None,)], True), ([(None, None)], True), ([(1,)], False), ([(0,), (1,)], False)],
)
def test_degenerate_gold_results(rows, degenerate):
    assert (build.degenerate(rows) is not None) == degenerate
