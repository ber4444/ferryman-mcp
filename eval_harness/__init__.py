"""
Ferryman eval harness.

A Python package that scores a ferryman skill against a human-authored golden
set. Two scorer layers:

  * rule_scorers.py — deterministic checks per expectedClaims key (comp figures,
    fabricated-entity check, citation presence).
  * judge_scorer.py — an LLM-as-judge on the subjective criteria a rule can't
    check (specificity, source-traceability, tone).

run_scorecard.py runs the full golden set through invoke.py, applies the
scorers, and writes a scorecard (rule-based by default; --judge adds the
judge layer; --all-providers runs the matrix across configured providers).

See README.md for the tooling-backend choice (promptfoo vs Braintrust).
"""

__version__ = "0.1.0"
