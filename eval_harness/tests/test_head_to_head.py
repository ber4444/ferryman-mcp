"""
Tests for the engine head-to-head and the engine plumbing it depends on. Hermetic:
run_one / subprocess / urlopen are stubbed — no ferry binary, no keys, no network.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import head_to_head
from eval_harness import invoke as invoke_mod
from eval_harness import run_scorecard
from eval_harness.run_scorecard import CaseResult


def _row(case_id, provider, engine, passed, *, error=None, tool_calls=None, output="answer", latency=1000):
    scores = [{"key": f"claim{i}", "passed": p, "reason": ""} for i, p in enumerate(passed)]
    return CaseResult(
        id=case_id,
        input={"company": f"Co-{case_id}", "role": "Mobile"},
        provider=provider,
        model=f"{provider}-model",
        output=output,
        rule_scores=scores,
        error=error,
        latency_ms=latency,
        estimated_cost_usd=0.001,
        engine=engine,
        tool_calls=tool_calls if tool_calls is not None else ["fetch.fetch"],
    )


# --- sign test ---------------------------------------------------------------


def test_sign_test_no_disagreements_is_p_one():
    assert head_to_head.sign_test_p(0, 0) == 1.0


def test_sign_test_balanced_is_p_one():
    assert head_to_head.sign_test_p(5, 5) == pytest.approx(1.0)


def test_sign_test_matches_exact_binomial():
    # 8 wins, 1 loss: 2 * P(X <= 1 | n=9, p=.5) = 2 * (1 + 9) / 512
    assert head_to_head.sign_test_p(8, 1) == pytest.approx(20 / 512)
    assert head_to_head.sign_test_p(1, 8) == pytest.approx(20 / 512)


# --- paired comparison -------------------------------------------------------


def test_paired_counts_wins_losses_ties_by_provider_and_case():
    base = [
        _row("c1", "p", "ferryman", [True, False]),
        _row("c2", "p", "ferryman", [True, True]),
        _row("c3", "p", "ferryman", [True, True]),
    ]
    koog = [
        _row("c1", "p", "koog", [True, True]),  # koog better
        _row("c2", "p", "koog", [True, False]),  # ferryman better
        _row("c3", "p", "koog", [True, True]),  # tie
    ]
    p = head_to_head.paired(base, koog)
    assert (p["pairs"], p["challengerWins"], p["baselineWins"], p["ties"]) == (3, 1, 1, 1)
    assert p["meanDiff"] == pytest.approx(0.0)
    assert [d["case"] for d in p["disagreements"]] == ["c1", "c2"]


def test_paired_does_not_cross_providers():
    base = [_row("c1", "a", "ferryman", [True])]
    koog = [_row("c1", "b", "koog", [False])]
    assert head_to_head.paired(base, koog)["pairs"] == 0


def test_paired_flags_an_error_on_one_side_even_at_equal_scores():
    base = [_row("c1", "p", "ferryman", [True])]
    koog = [_row("c1", "p", "koog", [True], error="boom")]
    d = head_to_head.paired(base, koog)["disagreements"]
    assert len(d) == 1


# --- engine summary ----------------------------------------------------------


def test_engine_summary_separates_errors_and_tool_discipline():
    rows = [
        _row("c1", "p", "koog", [True, True], tool_calls=["fetch.fetch"]),
        _row("c2", "p", "koog", [True, False], tool_calls=["fetch.fetch", "fetch.fetch"]),
        _row("c3", "p", "koog", [False], error="timeout", output="", tool_calls=[]),
        _row("c4", "p", "koog", [False], output="Reached tool-call limit (4) without a final answer.",
             tool_calls=["fetch.fetch"] * 4),
    ]
    s = head_to_head.engine_summary(rows)
    assert s["cases"] == 4
    assert s["errors"] == 1
    assert s["rulePassRate"] == pytest.approx((1.0 + 0.5 + 0.0 + 0.0) / 4)
    assert s["rulePassRateAnswered"] == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert s["exactlyOneFetch"] == 1
    assert s["budgetExhausted"] == 1
    assert s["perClaim"]["claim0"] == {"passed": 2, "applicable": 4}
    assert s["errorSamples"] == ["timeout"]


def test_compare_builds_per_provider_and_overall_slices():
    results = {
        "ferryman": [_row("c1", "a", "ferryman", [True]), _row("c1", "b", "ferryman", [False])],
        "koog": [_row("c1", "a", "koog", [True]), _row("c1", "b", "koog", [True])],
    }
    report = head_to_head.compare(results)
    assert report["providers"] == ["a", "b"]
    assert report["slices"]["b"]["paired"]["challengerWins"] == 1
    assert report["slices"]["all"]["paired"]["pairs"] == 2
    md = head_to_head.render_markdown(report)
    assert "Paired comparison" in md and "| b | 1 | 1 | 0 | 0 |" in md


# --- running: interleaving + engine-pinned specs -----------------------------


def test_engine_order_alternates_who_goes_first():
    engines = ("ferryman", "koog")
    assert head_to_head.engine_order(0, engines) == ("ferryman", "koog")
    assert head_to_head.engine_order(1, engines) == ("koog", "ferryman")


def test_run_head_to_head_interleaves_and_pins_each_engine(monkeypatch):
    calls = []

    def fake_run_one(case, provider, mode, spec=None):
        calls.append((provider, case["id"], spec.engine))
        return _row(case["id"], provider, spec.engine, [True])

    monkeypatch.setattr(run_scorecard, "run_one", fake_run_one)
    monkeypatch.setattr(run_scorecard, "_THROTTLE_SECONDS", 0.0)
    golden = [{"id": "c1", "input": {}}, {"id": "c2", "input": {}}]
    spec = run_scorecard.get_skill_spec("company-role-research")

    results = head_to_head.run_head_to_head(golden, providers=["p"], mode="subprocess", spec=spec)

    assert calls == [("p", "c1", "ferryman"), ("p", "c1", "koog"), ("p", "c2", "koog"), ("p", "c2", "ferryman")]
    assert [r.engine for r in results["koog"]] == ["koog", "koog"]


def test_run_head_to_head_isolates_a_failing_case(monkeypatch):
    def flaky(case, provider, mode, spec=None):
        if spec.engine == "koog":
            raise TimeoutError("koog hung")
        return _row(case["id"], provider, spec.engine, [True])

    monkeypatch.setattr(run_scorecard, "run_one", flaky)
    spec = run_scorecard.get_skill_spec("company-role-research")
    results = head_to_head.run_head_to_head([{"id": "c1", "input": {}}], providers=["p"], mode="x", spec=spec)
    assert results["koog"][0].error == "TimeoutError: koog hung"
    assert results["koog"][0].engine == "koog"
    assert results["ferryman"][0].error is None


def test_with_engine_keeps_ferryman_paths_and_suffixes_koog():
    spec = run_scorecard.get_skill_spec("company-role-research")
    assert run_scorecard.with_engine(spec, None) is spec
    assert run_scorecard.with_engine(spec, "ferryman").scorecard_json == spec.scorecard_json
    koog = run_scorecard.with_engine(spec, "koog")
    assert koog.scorecard_json.name == "scorecard-koog.json"
    assert koog.scorecard_md.name == "scorecard-koog.md"
    with pytest.raises(ValueError):
        run_scorecard.with_engine(spec, "langchain")


def test_run_one_passes_engine_to_invoke_only_when_pinned(monkeypatch):
    seen = []

    def fake_invoke(skill_input, **kw):
        seen.append(kw.get("engine", "<absent>"))
        return invoke_mod.InvocationResult(output="x", provider="p", model="m", engine=kw.get("engine"))

    monkeypatch.setattr(invoke_mod, "invoke", fake_invoke)
    case = {"id": "c", "input": {}, "expectedClaims": {}}
    spec = run_scorecard.get_skill_spec("company-role-research")
    run_scorecard.run_one(case, None, "subprocess", spec=spec)
    row = run_scorecard.run_one(case, None, "subprocess", spec=run_scorecard.with_engine(spec, "koog"))
    assert seen == ["<absent>", "koog"]
    assert row.engine == "koog"


def test_saved_rows_round_trip_and_old_rows_default_to_ferryman(tmp_path):
    results = {"ferryman": [_row("c1", "p", "ferryman", [True])], "koog": [_row("c1", "p", "koog", [False])]}
    head_to_head.write_outputs(results, tmp_path)
    loaded = head_to_head.load_saved(tmp_path, ("ferryman", "koog"))
    assert loaded["koog"][0].tool_calls == ["fetch.fetch"]
    assert (tmp_path / "report.md").exists()
    # A scorecard row written before engines existed still loads.
    old = {"id": "c1", "input": {}, "provider": "p", "model": "m", "output": "o"}
    (tmp_path / "old.json").write_text(json.dumps([old]))
    assert run_scorecard.load_results(tmp_path / "old.json")[0].engine == "ferryman"


# --- invoke.py engine plumbing -----------------------------------------------


def test_subprocess_passes_engine_flag_and_accepts_matching_meta(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        meta = {"_meta": {"provider": "p", "model": "m", "engine": "koog", "toolCalls": ["fetch.fetch"]}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(meta) + "\nanswer", stderr="")

    monkeypatch.setattr(invoke_mod.shutil, "which", lambda b: "/bin/ferry")
    monkeypatch.setattr(invoke_mod.subprocess, "run", fake_run)
    result = invoke_mod._invoke_subprocess({"company": "X"}, "skill", None, "ferry", "koog")
    assert seen["cmd"][-2:] == ["--engine", "koog"]
    assert result.ok and result.engine == "koog"
    assert result.tool_calls == ["fetch.fetch"]


def test_subprocess_rejects_a_binary_that_ran_another_engine(monkeypatch):
    def old_binary(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout='{"_meta":{"provider":"p","model":"m"}}\nanswer', stderr="")

    monkeypatch.setattr(invoke_mod.shutil, "which", lambda b: "/bin/ferry")
    monkeypatch.setattr(invoke_mod.subprocess, "run", old_binary)
    result = invoke_mod._invoke_subprocess({"company": "X"}, "skill", None, "ferry", "koog")
    assert not result.ok
    assert "requested" in result.error and result.output == ""


def test_http_sends_engine_and_raises_on_mismatch_so_auto_falls_back(monkeypatch):
    sent = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        sent["payload"] = json.loads(req.data)
        return Resp(json.dumps({"output": "o", "provider": "p", "model": "m", "toolCalls": []}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="requested"):
        invoke_mod._invoke_http({"company": "X"}, "skill", None, "http://x", "koog")
    assert sent["payload"]["engine"] == "koog"


def test_no_engine_requested_keeps_legacy_behaviour(monkeypatch):
    def legacy(cmd, **kw):
        assert "--engine" not in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='{"_meta":{"provider":"p","model":"m"}}\nanswer', stderr="")

    monkeypatch.setattr(invoke_mod.shutil, "which", lambda b: "/bin/ferry")
    monkeypatch.setattr(invoke_mod.subprocess, "run", legacy)
    result = invoke_mod._invoke_subprocess({"company": "X"}, "skill", None, "ferry")
    assert result.ok and result.output == "answer"
