"""
Tests for the runner (run_scorecard.py): per-case exception isolation, provider
ordering, and incremental writes. These are hermetic — no live ferry binary, no
API keys — by monkeypatching run_one so it returns canned CaseResults or raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

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

    def flaky_run_one(case, provider, mode):
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
    monkeypatch.setattr(run_scorecard, "run_one", lambda c, p, m: _stub_result(c["id"], p or "unknown"))
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
