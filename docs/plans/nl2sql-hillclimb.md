# Plan: Hill-climb the nl2sql skill on execution accuracy

Status: **harness ready, not yet run.** The skill, golden set, scorer, controls
and comparison tooling are built and tested. No provider has been scored yet,
so this file has no results. Fill in the log below from real runs only.

## What gets claimed, and how

The claim this produces has one shape:

> nl2sql execution accuracy on held-out Spider databases went from **X%** to
> **Y%** (n = 100, provider P, paired McNemar p = …).

Three rules keep that sentence honest:

1. **Iterate on `hillclimb`, report `heldout`.** The two splits use disjoint
   databases. Every prompt change is tried and judged on `hillclimb` only.
   `heldout` runs exactly twice per provider: once with the baseline prompt,
   once with the final one. A gain that only shows up on `hillclimb` is
   selection bias, not progress, because you kept the changes that happened to
   help those 100 questions.
2. **Paired comparison, not two headline numbers.** Use `compare_runs.py`. It
   counts which cases flipped and gives an exact McNemar p-value. On 100 cases
   a +4 point move can be 9 fixed and 5 broken, which is noise.
3. **Know the noise floor.** `nl2sql_controls.py` measured how often execution
   accuracy passes a one-edit wrong query on this data: 8/99 on
   `hillclimb` (small databases) and 1/106 on `heldout`. A `hillclimb` gain
   smaller than that floor is not evidence on its own.

## Protocol

```bash
P=hf-llama   # or zai-glm, gemini

# 0. Baseline on both splits, committed SKILL.md as-is.
python eval_harness/run_scorecard.py --skill nl2sql --provider $P --tag baseline

# 1..n. One change per iteration, hill-climb split only.
#    Edit ferryman/skills/nl2sql/SKILL.md (or set FERRY_NL2SQL_SAMPLE_ROWS), then:
python eval_harness/run_scorecard.py --skill nl2sql --provider $P --split hillclimb --tag it1
python eval_harness/compare_runs.py eval_harness/scorecard-nl2sql.baseline.json \
                                    eval_harness/scorecard-nl2sql.it1.json
#    Keep the change if it helps; revert it if it doesn't. Log it either way.

# Final. The held-out run of the final prompt.
python eval_harness/run_scorecard.py --skill nl2sql --provider $P --split heldout --tag final
python eval_harness/compare_runs.py eval_harness/scorecard-nl2sql.baseline.json \
                                    eval_harness/scorecard-nl2sql.final.json
```

`compare_runs.py` pairs cases by id, so comparing the two-split baseline with
a one-split run compares only the shared cases, and it says how many it left
out. Commit every tagged scorecard JSON you cite: the claim is only as good as
the snapshots behind it.

## Candidate changes (hypotheses, not results)

The committed SKILL.md is a standard zero-shot prompt: schema, question, and
output contract, with no Spider-specific advice. That baseline is deliberate.
It is the "basic prompt" format the text-to-SQL literature reports as its
zero-shot baseline, not a weakened one. Each item below is a known failure
mode worth testing one at a time:

| # | Change | Failure it targets |
|---|---|---|
| H1 | "Return only the columns the question asks for, in the order asked. No extra IDs or helper columns." | Extra columns. EX fails any column-count mismatch, and LLMs like to add context columns. |
| H2 | "For 'the most / least / highest', use `ORDER BY … LIMIT 1`." | Superlatives written as `MAX()` subqueries return ties that Spider's gold doesn't. |
| H3 | "Copy string literals exactly as the question writes them; values are case-sensitive." | `'dog'` vs `'Dog'`: wrong rows, or no rows. |
| H4 | `FERRY_NL2SQL_SAMPLE_ROWS=3` (example rows in the schema) | Value-format mismatches ('T'/'F' vs 1/0, year as text). Reported to help in Rajkumar et al. (2022). |
| H5 | A self-check step: "Before answering, check each thing the question asks for appears in SELECT, and nothing else." | Missing or extra outputs. |
| H6 | Few-shot examples drawn **only from `hillclimb` databases** | Format and convention drift. Examples from `heldout` databases would leak into the held-out score. |
| H7 | Execution feedback: give the skill an MCP SQLite tool to run drafts | Syntax and schema errors (the `executes` column). A follow-on: it needs a per-case database server, and it changes what the benchmark measures. |

`executes` vs `executionAccuracy` tells you which kind of change to try.
Failures to execute point at schema linking (H4, H7). Executable but wrong
points at semantics (H1–H3, H5).

## Known limits of this floor

- **Not leaderboard-comparable.** The databases come from InterCode's MySQL
  conversion, which can hold fewer rows than the original Spider `.sqlite`
  files, and the 200 questions are a filtered subset. Numbers here compare
  prompts and providers against each other, not against published Spider EX.
- **Blind to DISTINCT.** Ported as-is from the official evaluator, which strips
  DISTINCT from both queries. `count(DISTINCT x)` vs `count(x)` is invisible
  (16/16 in the controls). A change that adds or drops DISTINCT cannot show up
  here.
- **EX, not test-suite accuracy.** One database per question. The distilled
  test suites (Zhong et al., 2020) that catch coincidental matches are on
  Google Drive and were not reachable when this was built.

## Log

Fill from real runs only. One row per iteration, kept or reverted.

| Iter | Change | Provider | hillclimb EX | Δ vs prev kept | Fixed / broke | p | Kept? |
|---|---|---|---|---|---|---|---|
| 0 | baseline (committed SKILL.md) | | | — | — | — | — |

| Provider | heldout baseline | heldout final | Δ | Fixed / broke | p |
|---|---|---|---|---|---|
| | | | | | |
