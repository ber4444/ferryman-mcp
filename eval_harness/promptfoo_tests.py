"""
promptfoo test generator: emit one promptfoo test per golden case.

Referenced from promptfooconfig.yaml as a Python dynamic-test generator:

    tests:
      - path: file://eval_harness/promptfoo_tests.py:generate_tests

promptfoo calls `generate_tests(options)` once and expects an (or a list of)
`AtomicTestCase` dict. Each test's `vars` is the golden case's input plus its
`expectedClaims` (so the assertion bridge in `promptfoo_assert.py` can run the
exact scorers that apply to that case). The single assertion is a `python`
assert that delegates to `eval_harness.rule_scorers.score_all` — this does not
reimplement any scorer.

Cases whose `expectedClaims` are all `false` still emit a test: the only scorer
that can still fire is the process gate (it runs when present+true), so an
all-false case typically has no applicable scorers and trivially passes — which
is correct.
"""
from __future__ import annotations

import json
from pathlib import Path

_GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "golden_set.json"

# The single shared assertion object. Inline rather than duplicated 48 times.
_ASSERT = {
    "type": "python",
    "value": "file://eval_harness/promptfoo_assert.py:get_assert",
}


def generate_tests(options: dict | None = None) -> list[dict]:
    """Read the golden set and emit one promptfoo test per case."""
    del options  # unused; promptfoo passes caller config, we don't need it
    cases = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    tests: list[dict] = []
    for case in cases:
        expected_claims = case.get("expectedClaims", {})
        true_count = sum(1 for v in expected_claims.values() if v is True)
        tests.append(
            {
                "description": (
                    f"{case['id']}: {case['input']['company']} — "
                    f"{case['input']['role']} ({true_count} true claim(s))"
                ),
                "vars": {
                    "company": case["input"]["company"],
                    "role": case["input"]["role"],
                    # JSON-encode so the dict survives YAML/JSON round-trips
                    # through promptfoo's var templating as a single token.
                    "expected_claims": json.dumps(expected_claims, separators=(",", ":")),
                },
                # Generous threshold: the skill is answering from parametric
                # knowledge under promptfoo (no fetch tool), so allow long answers.
                "options": {"timeoutMs": 60000},
                "assert": [_ASSERT],
            }
        )
    return tests


if __name__ == "__main__":
    # Manual sanity check: `python -m eval_harness.promptfoo_tests` prints the
    # generated test objects as JSON. Useful before running promptfoo eval.
    import sys

    json.dump(generate_tests(), sys.stdout, indent=2)
    sys.stdout.write("\n")
