# Judge rubric — company/role research skill

The judge scores each skill output on four criteria, 1–5 each. A specific rubric
matters: a vague one produces scores that don't reproduce across runs. Each
criterion lists exactly what a 1, 3, and 5 look like.

## Criterion 1 — Specificity to this company and role (1–5)

Is the answer genuinely about *this* company, or generic boilerplate that could
describe any tech employer?

- **5** — Names specifics only this company has: its actual products, named
  engineering leaders or teams, its distinctive tech (e.g. Cloudflare → Workers
  WASM runtime), its public comp philosophy (e.g. PostHog → published formula).
- **3** — Mentions the company by name and gets the general area right, but the
  details (stack, level structure) are generic to the industry.
- **1** — Could be copy-pasted for any company. Reads like a template with the
  company name substituted in.

## Criterion 2 — Source traceability (1–5)

Does every factual claim trace to a cited source, or are assertions floating?

- **5** — Every quantitative claim (comp figures, team sizes, headcount) carries
  a citation or a URL. Qualitative claims name where they came from.
- **3** — Some claims are sourced, some are not. The unsourced ones are plausible
  but unverifiable.
- **1** — No citations. Every claim floats. Precise figures appear with no
  attribution — a fabrication risk signal.

## Criterion 3 — Honesty about missing data (1–5)

When reliable public data does not exist (early-stage startups, private
companies), does the skill say so, or does it invent specifics?

- **5** — Where data is sparse, the skill says so plainly and explains what it
  *could not* verify. The negative case (Acme Holdings) gets a "no reliable info"
  answer.
- **3** — Mostly honest but hedges with vague plausible-sounding figures.
- **1** — Invents specifics for companies with no public data. This is the
  single worst failure mode for this skill.

## Criterion 4 — Tone and structure (1–5)

Is the answer appropriately concise, well-structured, and pitched at a candidate
deciding whether to apply?

- **5** — Scannable (headers/bullets), concise (under ~400 words), ordered by
  what a candidate most needs (comp, remote policy, stack, known signal).
- **3** — Usable but either too long, poorly structured, or buries the lede.
- **1** — Wall of prose, off-topic tangents, or hostile/over-promotional tone.

## Aggregation

The overall judge score is the mean of the four criteria (1.0–5.0). A drop below
3.0 on criterion 3 (honesty) is a hard fail regardless of the mean — inventing
data is the failure mode this harness exists to catch.
