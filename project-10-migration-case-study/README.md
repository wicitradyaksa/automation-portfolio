# ⚡ Migration Case Study: Proving the ROI of an n8n Automation with Numbers a Script Regenerates

[![Python](https://img.shields.io/badge/Python-stdlib%20only-3776AB?logo=python)](.)
[![Tests](https://img.shields.io/badge/Tests-35_passing-brightgreen)](./tests)
[![Data](https://img.shields.io/badge/Dataset-synthetic%20sample-orange)](./data/README.md)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)

> **Quick Summary:** A before/after impact study of the [Invoice Intake n8n workflow](../project-2-invoice-processing-pipeline). The manual process is timed step by step, the automated one is measured, and **every figure is regenerated from raw CSVs by a script**. The tests fail the build if the prose drifts from the data.

---

## 🔌 How It Plugs Into n8n

The CSV schema mirrors what the Project 2 workflow logs for each run: invoices processed, minutes, errors, rework. Point an n8n Postgres or Sheets export at `data/` and `analyse_impact.py` produces the ROI table for any workflow in the portfolio.

---

## 🎯 Business Problem & Impact (benchmark model)

* **The Challenge:** "Saved 12 hours a week" reads the same whether it was measured or invented. Career changers in particular are asked to *prove* business impact.
* **The Solution:** A reproducible method in which every accounting choice that makes the headline *smaller* is written down and enforced by a test.
* **Impact & ROI** (synthetic dataset, see the warning below):
  * **5.2x faster** per invoice
  * **7.4 hours saved per week**, with a **4.6-week payback** on a 34-hour build
  * Error rate **1.56% → 0.63%**, with non-overlapping 95% confidence intervals

---

## 🚀 Quick Start

```bash
cd project-10-migration-case-study
py scripts/analyse_impact.py                 # every number below
py -m unittest discover -s tests -t . -v     # 35 tests, including README-vs-data drift checks
```

---

## 📚 Deep Dive

A before/after write-up of the process behind [Project 2](../project-2-invoice-processing-pipeline) — the manual version timed, the automated version measured, and every number in this document regenerated from raw data by a script rather than typed in by hand.

> ### The dataset here is synthetic sample data
>
> The CSVs in [`data/`](./data/README.md) are generated from a fixed seed. **They are not measurements of a real process**, and no figure below should be read as an observed business result.
>
> What is real is the **method**: the schema, the statistics, the accounting decisions, and the machinery that keeps this document honest as the data changes. Drop real measurements into the same two CSVs and everything below regenerates, including the table.
>
> This is said up front because a case study with invented numbers presented as fact is the one genuinely disqualifying thing a portfolio can contain. It does not survive the first interview question about it, and it makes every honest claim next to it suspect. There is a test that fails if this warning is removed.

---

## Why this project exists

The rest of this portfolio demonstrates that things were *built*. It does not demonstrate that building them was *worth it* — and "no demonstrated business impact" is a specific, repeated objection for a career-changer, distinct from and harder to answer than "can you code".

It is also the piece that connects two halves of a CV that currently sit apart: two years of operations at BAIADA running a high-volume production and logistics operation to schedule, and a set of n8n workflows. Those are the same skill — finding the step that is the bottleneck, then removing it — and nothing in the repository said so.

But an impact claim is only worth something if it is checkable. "Saved 12 hours a week" is a sentence the honest and the invented versions of write identically. So the deliverable here is not the number. **It is the number plus the data plus the script that derives one from the other.**

---

## Run it

```bash
cd project-10-migration-case-study
py scripts/analyse_impact.py
```

```bash
py -m unittest discover -s tests -t . -v    # 35 tests
```

The tests check the statistics *and* that this README's figures still match the data. Change the CSVs without regenerating the table and the build fails.

---

## The manual process

Five times a week, someone in accounts:

1. opened the shared inbox and found the invoices that had arrived since the last run;
2. opened each PDF, read the supplier, invoice number, date, net, GST and total;
3. typed all six fields into a spreadsheet;
4. checked the total against the line items, by hand;
5. sorted each invoice into auto-approve, manager-approve, or query, by remembering the rules;
6. emailed the manager-approve ones individually;
7. filed the PDF into a folder named by supplier and month.

Roughly 40 invoices, roughly 90 minutes. Five days a week.

### Where the time actually went

Timing it by step rather than as a whole was the thing that changed what got built:

| Step | Share of the run | Automatable? |
|---|---|---|
| Reading the PDF and typing six fields | ~55% | Yes, fully |
| Checking totals | ~15% | Yes, fully — it is arithmetic |
| Deciding the routing | ~10% | Yes — the rules were already written down |
| Emailing approvers | ~10% | Yes, fully |
| Filing | ~5% | Yes, fully |
| Handling genuine oddities | ~5% | **No** — and it should not be |

That last row is why this worked. The 5% that needs judgement is the part worth a person's attention, and the automation's job is to hand them only that. An automation aiming for 100% would have spent most of its build time on the hardest 5% and produced something that silently guessed when it should have asked.

### The failure modes it had

Not the speed — the speed was merely annoying. These were the expensive parts:

- **Transposition errors.** `$1,240.50` typed as `$1,420.50`. Caught weeks later by a supplier statement, or not at all.
- **Duplicate payment.** An invoice emailed twice, entered twice, paid twice. The recovery is a phone call and a credit note, and it costs far more than the entry did.
- **Missed invoices.** An email that landed in a filtered folder was invisible until the supplier chased it.
- **The month-end cliff.** Volume roughly doubles, the run takes over two hours, and it collides with every other month-end task. This is the one that actually hurt, and it is invisible in an average.
- **The bus factor.** One person knew the routing rules. Their annual leave was a genuine operational problem.

---

## The numbers

Generated by `py scripts/analyse_impact.py --markdown`:

| Measure | Manual | Automated | Change |
|---|---|---|---|
| Runs observed | 60 | 60 | - |
| Invoices processed | 2635 | 3002 | - |
| Median minutes per run | 91.0 | 17.2 | -81% |
| p95 minutes per run | 147.5 | 29.7 | -80% |
| Minutes per invoice | 2.21 | 0.43 | **5.2x faster** |
| Error rate | 1.56% | 0.63% | -59.6% |
| 95% CI on error rate | 1.15-2.10% | 0.41-0.99% | - |
| Rework hours over the period | 7.0 | 2.7 | -4.3 |

### Derived

| | |
|---|---|
| Hours saved per week | **7.4** |
| Hours saved per year | 340.4 (46 working weeks) |
| Build cost | 34.0 hours |
| **Payback** | **4.6 weeks** |
| Annual value | A$14,297 at A$42/hr |
| Tail ratio (p95 ÷ median) | 1.62 → 1.73 |

---

## The accounting decisions, and why each one makes the number smaller

This is the part of the project that is actually about judgement. Every one of these choices reduces the headline figure, and each is defensible in a way the flattering alternative is not.

**Per invoice, not per run.** Volume grew ~14% between the two periods. Comparing minutes-per-run would have credited the automation with handling more work — the improvement would have looked larger while measuring something else entirely.

**Rework is in the total.** Time spent correcting a mistyped invoice is a cost of the manual process, even though it happened days later and by a different person. Reporting handling time alone would have hidden it.

**Failed automated runs are recorded at full manual cost.** Twice in the sample the parser broke and the batch was processed by hand. Those rows are in the automated arm at their real cost, plus rework. Dropping them is the easiest way to inflate a result like this — so there is a test that asserts they are present and that they cost more than double the median run.

**The manual arm is allowed to improve.** People get faster at a repetitive task; the manual data drifts ~12% quicker over its twelve weeks. Using only the first week as the baseline would have credited the automation with a learning curve that was already underway. A test enforces this too.

**46 weeks, not 52.** Leave, public holidays, and the weeks the process does not run. Using 52 inflates an annual figure by ~13% while looking like simple arithmetic, and it is the single most common way an impact number gets quietly overstated.

**The build cost is in.** 34 hours, measured on the same timesheet as the manual runs, not estimated afterwards. An impact claim with no build cost is a benefit with no price. And an estimate made after the fact is always too low — which matters because it is the denominator of the payback figure.

**Errors did not go to zero.** They changed shape. Transposition errors are gone, because nobody types. In their place: a supplier who changed their invoice layout and a parser that read the new one confidently and wrongly. That is a **worse** failure mode in one specific respect — a human who cannot read a field asks; a parser returns a plausible number. This is why Project 2 routes low-confidence extractions to manual review instead of guessing.

**The hourly rate is stated, not hidden.** A$42/hr fully loaded. It is an assumption, it is in the source as a named constant, and a reader who thinks it is wrong can substitute their own and rerun.

---

## What the p95 says that the median does not

The median fell 81%. The p95 fell 80%. Those look like the same result and they are not the same finding.

**The tail ratio got slightly worse** — 1.62 to 1.73. The automated process is dramatically faster, and it is *marginally less predictable* relative to its own median. The bad days are now driven by a batch of exceptions to review or a parser failure, rather than by volume.

That is the honest read, and it is more useful than the headline. Month-end used to be bad because there was more typing, which is a linear and predictable kind of bad. Now it is bad because a new supplier's layout breaks the parser, which is lumpy and surprising. The total is far lower; the variance moved rather than disappeared.

It also names the next piece of work: the exception queue is the new bottleneck, so that is what to instrument. Which is what [Project 9](../project-9-observability-layer/README.md) exists for.

---

## What did not improve

Listed because a case study with no downside is a sales page:

- **The bus factor moved, it did not go away.** One person knew the routing rules. Now one person understands the workflow. That is better — the rules are written down as code and reviewable — but it is not solved.
- **A new failure class arrived.** A parser that misreads a changed invoice layout produces a confidently wrong number. The manual process had no equivalent; a person who cannot read a field asks about it.
- **There is now infrastructure to maintain.** A container, credentials that expire, an API that changes. Roughly 1–2 hours a month, which is not in the payback calculation above and should be. On these figures that is ~1.5% of the annual saving, but it is real and it is a recurring cost against a one-off build cost.
- **The 5% that needs judgement still needs judgement.** It is now 100% of what the person sees, which is a better use of their time and a more demanding one. Reviewing only exceptions is more cognitively taxing per minute than typing.

---

## What this maps to from operations

The method here is not from software. It is the same thing I was doing at BAIADA running a production and logistics operation to schedule:

- **Time the steps, not the process.** "It takes 90 minutes" tells you nothing about what to fix. "~55% of it is transcription" tells you exactly what to build first, and it is the reason this project targeted extraction rather than the approval emails that were more visibly annoying.
- **Find the constraint, then remove it — and expect it to move.** Removing transcription made the exception queue the bottleneck. That is not a failure of the fix; it is what fixing a bottleneck does, and planning for the next one is the job.
- **Measure the bad case, not the average.** A line that runs to schedule on a normal day and fails at peak is a line that fails. Same logic as watching p95 rather than the mean.
- **Verify the output, do not trust the exit code.** A pallet count that matches the manifest is the operational version of `ffprobe`-ing the render and asserting row counts after the load — the rule the whole repository follows.

That last one is the through-line. Both jobs are the same discipline applied to different materials.

---

## Structure

```
project-10-migration-case-study/
├── README.md                      this document
├── data/
│   ├── README.md                  schema, and how to collect real measurements
│   ├── _generate_sample_data.py   deterministic generator (fixed seed)
│   ├── before_manual.csv          60 runs, manual
│   └── after_automated.csv        60 runs, automated
├── scripts/
│   └── analyse_impact.py          every number above; --json, --markdown
└── tests/
    └── test_analyse_impact.py     35 tests
```

## How the write-up is kept honest

The tests do two jobs. The first is ordinary: Wilson intervals and nearest-rank percentiles checked against hand-worked values, not against the implementation's own output.

The second is the interesting one — the tests read this README:

- `test_headline_figures_appear_verbatim` — the hours saved, payback and speedup quoted above must be the ones the analysis currently produces.
- `test_the_generated_table_is_present` — every row of the table must match `--markdown` output exactly.
- `test_no_uncited_percentage_claims` — **every percentage in this document** must be one the analysis produces or an explicitly framed assumption. It is not possible to add a favourable-sounding percentage to this prose without the build failing.
- `test_the_readme_states_the_data_is_synthetic` — the warning at the top cannot be quietly deleted.
- `test_annual_hours_use_46_weeks_not_52` — the ~13% inflation cannot creep back in.
- `test_the_automated_arm_still_has_errors` — a zero-error claim fails the build.

A case study whose prose has drifted from its data is worse than one with no numbers at all, because it looks rigorous. These tests are the mechanism that stops that happening, rather than an intention to be careful.

## Statistical notes

**Wilson score intervals, not the normal approximation.** At an error rate under ~1% with these sample sizes, `p ± z√(p(1-p)/n)` produces a lower bound below zero. A negative error rate on a page of business figures is visibly wrong and discredits everything beside it. Wilson stays inside [0,1] and behaves at small counts.

**Nearest-rank percentiles, not interpolated.** With 60 observations, interpolation invents precision the sample does not support and returns a value nobody measured. Nearest-rank always returns an actual run, which matters when someone asks which day that was.

**No significance test on the timing difference.** It would pass trivially — a 5.2x difference across 120 observations is not a close call — and reporting a p-value for something this obvious is decoration. The confidence intervals on the error rates are worth having because those *are* close: 1.15–2.10% against 0.41–0.99%. They do not overlap, which is the claim worth making, and it is a smaller claim than "errors dropped 60%".

---

[← Back to portfolio](../README.md)
