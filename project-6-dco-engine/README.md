# DCO Engine — Creative Performance Loop

**Joins ad spend to affiliate revenue, tests whether a creative is actually winning, and only then touches budget. Losers get paused and automatically queue their own replacement.**

## The problem

Dynamic Creative Optimization gets sold as "the platform figures out which creative wins." In practice, on any campaign spending across a network and monetising through an affiliate offer, two things break that story.

**First, the platform can't see your revenue.** It sees clicks and whatever conversion signal fires back, which on an affiliate offer is delayed, partial, or absent. Optimising to the platform's in-app metric optimises toward clicks that don't pay.

**Second — and this is the expensive one — most "winning" creatives aren't.** A variant with 400 impressions and a 7% CVR against a control's 5% looks like a 40% lift. It isn't; it's noise, and at that sample size it will happily reverse next week. A pipeline that automatically scales budget into that is a machine for converting a random number generator into spend. I've watched enough of this get done by hand on gut feel to know that's the actual failure mode, not "we didn't automate fast enough."

So the interesting part of this workflow isn't the automation. It's the two places it **refuses to act**.

## The solution

```
every 6 hours
   │
   ├──► Fetch Ad Platform Insights   (spend, impressions, clicks — paginated, retry ×4)
   │                                          │
   └──► Fetch Affiliate Conversions  (revenue by sub_id — retry ×4)
                                              │
                    Join Spend + Revenue  ◄───┘   (ad_id ↔ sub_id)
                              ▼
                    Compute Variant Metrics       CTR · CVR · CPA · ROAS · EPC
                              ▼
        ┌──── Enough Data To Decide? ────┐
       no                               yes
        │                                │
 Log Still Learning          Run Significance Test   (Python, two-proportion z-test)
                                         ▼
                                 Parse Test Result
                                         ▼
                                  Route Decision
                        ┌──────────────┼──────────────┐
                     scale            pause          hold
                        │              │              │
          Scale Winner Budget    Pause Losing Ad   (no action)
              (+20% cap)               ▼              │
                        │   Request Replacement ──────┤
                        │      Creative (→ Project 5) │
                        └──────────────┴──────────────┘
                                       ▼
                                  Log Decision → Post Buying Report
```

## Gate 1: the volume floor

`Compute Variant Metrics` sets `hasEnoughData` — at least 1,000 impressions **and** 100 clicks. Below that the variant is logged as `insufficient_data` and nothing happens to it.

This is boring and it's the single highest-value line in the workflow. Noise is not a signal, and a pipeline that acts on it will confidently destroy a campaign faster than a human ever could.

## Gate 2: statistical significance

[`scripts/ab_significance.py`](./scripts/ab_significance.py) runs a two-proportion z-test against the control, with **a Bonferroni correction for the number of variants under test**.

That correction matters more than it sounds. Testing six variants at α = 0.05 gives roughly a 26% chance that at least one of them clears the bar by luck alone. Dividing the threshold by the number of comparisons holds the family-wise error rate where you thought it was.

Run against realistic numbers, here's what it does:

```
control       50,000 imp · 1,500 clicks · 75 conv   (CVR 5.0%)
variant_b     48,000 imp · 1,600 clicks · 112 conv  (CVR 7.0%, +40% lift)
   → p = 0.0194, adjusted α = 0.0167  →  HOLD
```

A 40% lift that *looks* obviously real gets held for another cycle, because with three comparisons the adjusted threshold is 0.0167 and it came in at 0.0194. That is the system working. One more cycle of data either confirms it or reveals it as the variance it probably was.

Meanwhile:

```
variant_c     47,000 imp · 1,400 clicks · 42 conv   (CVR 3.0%, ROAS 0.57)
   → PAUSE — "roas 0.57 below hard floor 0.91"
```

The ROAS floor deliberately **overrides** the statistics. If a variant is burning money at 57% of target, there's no reason to wait for a p-value to agree; it gets paused on economics alone.

Decision bands, all conservative on purpose:

| Condition | Decision |
|---|---|
| ROAS < 70% of target | `pause` (regardless of significance) |
| p ≥ adjusted α | `hold` |
| Significant, lift ≥ +10% | `scale` |
| Significant, lift ≤ −15% | `pause` |
| Significant, lift in between | `hold` — inside the no-action band |

## Scaling is capped at +20%

`Scale Winner Budget` steps the daily budget by 20% per cycle and no more. Doubling a winning ad set overnight resets the platform's learning phase and usually kills the winner you just found. The asymmetry is the point: the cost of scaling too slowly is a few hours of missed upside, the cost of scaling a false winner is real money.

## The loop closes itself

When a variant is paused, the workflow appends a new row to the **same `Creative Briefs` sheet** that the [Generative Creative Factory](../project-5-generative-creative-factory) reads every morning — tagged `requestedBy: dco_engine` with the reason (`paused: cvr 0.03 vs control, p=0.006`).

So: creative gets generated → rendered into placements → tested → losers paused → replacements queued → generated. The pipeline refills itself, and a human's job moves from "export files and check dashboards" to "decide what angles are worth testing."

## Tech stack

- **n8n** — Schedule Trigger, HTTP Request (with pagination + retry), Merge, Code, IF, Switch, Google Sheets, Slack, Error Trigger
- **Python 3 stdlib only** — z-test via `math.erf`, no SciPy, because installing a scientific stack on a small VPS to compute one CDF is not a good trade
- **Ad platform + affiliate network REST APIs** — Bearer and API-key auth, paginated pulls

## Why it's built this way

**Two sources, joined on `sub_id`.** Spend lives in the ad platform, revenue lives in the affiliate network, and the only thing connecting them is the sub-ID passed through the click. Everything downstream depends on that join being right, which is why both fetches retry four times — a partially-paginated pull would silently understate spend and skew every decision that follows.

**Credentials come from the environment**, never from node parameters, because the full node configuration is visible in every execution log.

**The significance test is a file, not a Code node.** It can be run from a terminal against a JSON fixture, which means the riskiest logic in the workflow is the part that's easiest to check.

## What I'd improve with more time

- Bayesian decisions (Beta-Binomial posteriors) instead of frequentist — better suited to sequential peeking, which is what a 6-hour cycle genuinely is, and it reports "84% probability B beats A" instead of a p-value nobody in the room interprets consistently.
- Multi-armed bandit allocation rather than discrete scale/pause, so budget flows continuously toward the posterior best.
- Creative-fatigue detection on the frequency and CTR-decay curve — knowing a winner is *about to* stop working is worth more than knowing a loser already has.
- Holdout groups, so the engine's contribution is measurable rather than assumed.

## Running it

Import [`workflow.json`](./workflow.json) and set:

```bash
ADS_API_URL=...            ADS_API_TOKEN=...
AFFILIATE_API_URL=...      AFFILIATE_API_KEY=...
CREATIVE_SHEET_ID=...
DCO_MIN_IMPRESSIONS=1000   DCO_TARGET_ROAS=1.3   DCO_SCALE_STEP=1.2
```

Ad names must follow `campaign__variantId__placement` so spend rows can be rolled up per variant. The decision script runs standalone:

```bash
python3 scripts/ab_significance.py --metric cvr --alpha 0.05 --variants "$(cat fixtures/variants.json)"
```
