"""
Tests for the runner (run_scorecard.py): per-case exception isolation, provider
ordering, incremental writes, and token-based cost estimation. These are
hermetic — no live ferry binary, no API keys — by monkeypatching run_one so it
returns canned CaseResults or raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import invoke as invoke_mod
from eval_harness import run_scorecard
from eval_harness.run_scorecard import CaseResult


def _stub_result(case_id: str, provider: str) -> CaseResult:
    return CaseResult(
        id=case_id,
        input={"company": "X", "role": "Y"},
        provider=provider,
        model="stub",
        output="stub output",
    )


# --- per-case exception isolation -------------------------------------------


def test_run_all_continues_when_one_case_raises(monkeypatch):
    """An exception from run_one must not abort the batch — it becomes an error CaseResult."""

    def flaky_run_one(case, provider, mode, spec=None):
        if case["id"] == "case-002":
            raise RuntimeError("simulated timeout")
        return _stub_result(case["id"], provider or "unknown")

    monkeypatch.setattr(run_scorecard, "run_one", flaky_run_one)
    monkeypatch.setattr(run_scorecard, "_THROTTLE_SECONDS", 0.0)

    golden = [{"id": f"case-00{i}"} for i in range(1, 4)]
    results = run_scorecard.run_all(golden, providers=["zai-glm"], mode="subprocess")

    assert len(results) == 3, "all three cases should produce a result"
    assert results[1].error and "simulated timeout" in results[1].error
    assert results[1].output == ""
    assert results[0].error is None and results[2].error is None


# --- provider ordering ------------------------------------------------------


def test_order_providers_puts_llama_first_gemini_last():
    """--all-providers should run hf-llama, then zai-glm, then gemini."""
    providers = run_scorecard.enumerate_providers()  # config order: zai-glm, gemini, hf-llama
    ordered = run_scorecard.order_providers(providers)
    assert ordered == ["hf-llama", "zai-glm", "gemini"]


def test_order_providers_appends_unknowns():
    ordered = run_scorecard.order_providers(["gemini", "zai-glm", "hf-llama", "newco"])
    assert ordered[:3] == ["hf-llama", "zai-glm", "gemini"]
    assert ordered[3] == "newco"  # unknown provider kept, at the end


# --- incremental writes -----------------------------------------------------


def test_run_all_calls_on_provider_done_after_each_provider(monkeypatch):
    """The incremental-save callback fires once per provider with accumulated results."""
    monkeypatch.setattr(run_scorecard, "run_one", lambda c, p, m, spec=None: _stub_result(c["id"], p or "unknown"))
    monkeypatch.setattr(run_scorecard, "_THROTTLE_SECONDS", 0.0)

    golden = [{"id": "case-001"}, {"id": "case-002"}]
    snapshots: list[int] = []

    def on_done(so_far: list[CaseResult]) -> None:
        snapshots.append(len(so_far))

    run_scorecard.run_all(
        golden,
        providers=["hf-llama", "zai-glm", "gemini"],
        mode="subprocess",
        on_provider_done=on_done,
    )

    # After provider 1 (llama): 2 results. After provider 2 (zai): 4. After 3 (gemini): 6.
    assert snapshots == [2, 4, 6]


# --- token-based cost estimation -------------------------------------------


_PRICING = {
    "zai-glm": {
        "inputPricePerMillionTokens": 1.0,
        "outputPricePerMillionTokens": 4.0,
        "dateChecked": "2026-07-15",
    },
}


def _invocation(*, input_tokens=None, output_tokens=None, input_chars=0, output_chars=0, provider="zai-glm"):
    return invoke_mod.InvocationResult(
        output="",
        provider=provider,
        model="m",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_chars=input_chars,
        output_chars=output_chars,
    )


def test_estimate_cost_uses_real_tokens_when_present():
    """Real usage token counts produce an exact cost (no chars/4 estimate)."""
    result = _invocation(input_tokens=1000, output_tokens=500, input_chars=99999, output_chars=99999)
    cost = run_scorecard.estimate_cost(result, _PRICING)
    # 1000 * 1.0/M + 500 * 4.0/M = 0.001 + 0.002 = 0.003
    assert cost == 0.003


def test_estimate_cost_falls_back_to_chars_when_tokens_absent():
    """Without real tokens, cost is the chars/4 estimate."""
    result = _invocation(input_chars=400, output_chars=800)  # -> 100 / 200 tokens
    cost = run_scorecard.estimate_cost(result, _PRICING)
    # 100 * 1.0/M + 200 * 4.0/M = 0.0001 + 0.0008 = 0.0009
    assert cost == 0.0009


def test_estimate_cost_returns_none_when_provider_unpriced():
    """A provider absent from pricing.json has no cost."""
    result = _invocation(provider="unpriced", input_tokens=10, output_tokens=10)
    assert run_scorecard.estimate_cost(result, _PRICING) is None


def test_estimate_cost_real_tokens_override_chars_even_if_both_set():
    """When real tokens are present they win regardless of char counts."""
    real = _invocation(input_tokens=10, output_tokens=10, input_chars=4000, output_chars=4000)
    cost_real = run_scorecard.estimate_cost(real, _PRICING)
    assert cost_real == 0.00005  # 10*1.0/M + 10*4.0/M = 0.00001 + 0.00004


# --- multi-skill harness (--skill / SkillSpec) -----------------------------


def test_get_skill_spec_default_is_company_research():
    """No skill name resolves to company-role-research (backward compat)."""
    spec = run_scorecard.get_skill_spec(None)
    assert spec.name == "company-role-research"
    assert spec.skill_id == "company-role-research"


def test_get_skill_spec_chess_resolves_separate_outputs():
    """The chess spec has its own golden set, scorer, and scorecard paths."""
    spec = run_scorecard.get_skill_spec("chess-opening-coach")
    assert spec.skill_id == "chess-opening-coach"
    assert spec.golden_path.name == "chess_golden.json"
    assert spec.scorecard_md.name == "scorecard-chess.md"
    assert spec.rubric_path is not None and spec.rubric_path.name == "rubric-chess.md"


def test_run_one_passes_skill_id_to_invoke(monkeypatch):
    """run_one must invoke the skill named by the spec, not the hardcoded default."""
    captured: dict[str, str] = {}

    def fake_invoke(skill_input, *, skill="", provider=None, mode="auto", **kw):
        captured["skill"] = skill
        return invoke_mod.InvocationResult(output="FINAL ANSWER: e2e4", provider="x", model="m")

    monkeypatch.setattr(invoke_mod, "invoke", fake_invoke)
    case = {
        "id": "t1",
        "input": {"fen": "..."},
        "correctAnswer": "e2e4",
        "answerFormat": "uci",
    }
    run_scorecard.run_one(case, provider=None, mode="subprocess", spec=run_scorecard.get_skill_spec("chess-opening-coach"))
    assert captured["skill"] == "chess-opening-coach"


# --- judge is skipped on errored cases -------------------------------------


def test_run_all_skips_judge_on_errored_case(monkeypatch):
    """A case that errors must not be sent to the judge (empty output, wasted call)."""

    def flaky_run_one(case, provider, mode, spec=None):
        if case["id"] == "case-err":
            raise RuntimeError("simulated timeout")
        return _stub_result(case["id"], provider or "unknown")

    monkeypatch.setattr(run_scorecard, "run_one", flaky_run_one)
    monkeypatch.setattr(run_scorecard, "_THROTTLE_SECONDS", 0.0)

    # If the judge were called for the errored case, this would raise.
    def boom_judge(case_result, case, spec=None):
        assert case_result.error is None, "judge must never see an errored case"
        return [{"key": "specificity", "passed": True, "reason": "ok"}]

    monkeypatch.setattr(run_scorecard, "_judge_if_available", boom_judge)

    golden = [{"id": "case-ok"}, {"id": "case-err"}]
    results = run_scorecard.run_all(golden, providers=["zai-glm"], mode="subprocess", use_judge=True)

    ok, err = results[0], results[1]
    assert ok.judge_scores and ok.judge_scores[0]["key"] == "specificity"
    # Errored case got a skip marker, not a real judge verdict.
    assert err.error and err.judge_scores == [
        {"key": "_judge", "passed": False, "reason": f"skipped — case errored: {err.error}"}
    ]
