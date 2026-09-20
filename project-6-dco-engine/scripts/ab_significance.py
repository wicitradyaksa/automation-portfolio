#!/usr/bin/env python3
"""
ab_significance.py — decide scale / pause / hold for creative variants.

Called by the n8n "Run Significance Test" node. Reads a JSON array of variant
metrics on --variants (or stdin), compares each challenger against the control
with a two-proportion z-test, applies a Bonferroni correction for the number of
comparisons, and emits a decision per variant on stdout as JSON.

The point of this file: without it the workflow would "optimise" on whichever
variant happened to get lucky in a 3-day window. Most of what looks like a
winning creative at low volume is sampling noise, and a pipeline that scales
noise burns budget faster than doing nothing.

Usage:
  ab_significance.py --variants '[{...}]' [--metric cvr] [--alpha 0.05]
  cat variants.json | ab_significance.py --metric ctr

Stdout (always valid JSON, even on a handled error):
  {"ok": true, "control": "...", "alphaAdjusted": 0.0125, "variants": [...]}
"""
from __future__ import annotations

import argparse
import json
import math
import sys

# Decision thresholds. Deliberately conservative — in paid media the cost of
# scaling a false winner is real money, the cost of holding one more cycle is a
# few hours of missed upside.
MIN_LIFT_TO_SCALE = 0.10   # +10% relative lift before we touch budget
MAX_LOSS_TO_PAUSE = -0.15  # -15% relative before we kill a variant
ROAS_FLOOR_MULTIPLIER = 0.7  # pause anything under 70% of target ROAS regardless of p


def erf_cdf(z: float) -> float:
    """Standard normal CDF via math.erf — avoids a scipy dependency on the VPS."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_proportion_z(succ_a: int, n_a: int, succ_b: int, n_b: int) -> tuple[float, float]:
    """Return (z, two-tailed p) comparing rate B against rate A."""
    if n_a <= 0 or n_b <= 0:
        return 0.0, 1.0
    p_a, p_b = succ_a / n_a, succ_b / n_b
    pooled = (succ_a + succ_b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 0.0, 1.0
    z = (p_b - p_a) / se
    p = 2 * (1 - erf_cdf(abs(z)))
    return z, p


def numerator_denominator(v: dict, metric: str) -> tuple[int, int]:
    """Map a metric name onto the success/trial counts its rate is built from."""
    if metric == "cvr":
        return int(v.get("conversions", 0)), int(v.get("clicks", 0))
    if metric == "ctr":
        return int(v.get("clicks", 0)), int(v.get("impressions", 0))
    raise ValueError(f"unsupported metric: {metric!r} (use cvr or ctr)")


def pick_control(variants: list[dict]) -> dict:
    """Explicit control wins; otherwise the highest-volume variant is the incumbent."""
    for v in variants:
        if v.get("isControl") or str(v.get("variantId", "")).endswith("_control"):
            return v
    return max(variants, key=lambda v: int(v.get("impressions", 0)))


def decide(v: dict, lift: float, p_value: float, alpha: float) -> tuple[str, str]:
    """Return (decision, reason). Order matters — the ROAS floor overrides statistics."""
    roas = float(v.get("roas") or 0)
    target = float(v.get("targetRoas") or 0)

    if target and roas < target * ROAS_FLOOR_MULTIPLIER:
        return "pause", f"roas {roas:.2f} below hard floor {target * ROAS_FLOOR_MULTIPLIER:.2f}"

    if p_value >= alpha:
        return "hold", f"p={p_value:.4f} not significant at alpha={alpha:.4f}"

    if lift >= MIN_LIFT_TO_SCALE:
        return "scale", f"significant +{lift * 100:.1f}% lift (p={p_value:.4f})"

    if lift <= MAX_LOSS_TO_PAUSE:
        return "pause", f"significant {lift * 100:.1f}% loss (p={p_value:.4f})"

    return "hold", f"significant but only {lift * 100:+.1f}% — inside the no-action band"


def main() -> int:
    ap = argparse.ArgumentParser(description="Creative A/B significance + scale/pause decisions")
    ap.add_argument("--variants", help="JSON array of variant metrics; omit to read stdin")
    ap.add_argument("--metric", default="cvr", choices=["cvr", "ctr"])
    ap.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args()

    raw = args.variants if args.variants else sys.stdin.read()
    try:
        variants = json.loads(raw)
    except json.JSONDecodeError as e:
        # Always emit JSON — the n8n Code node downstream parses stdout, and a
        # bare traceback there produces a much worse error message than this.
        print(json.dumps({"ok": False, "error": f"input was not valid JSON: {e}", "variants": []}))
        return 1

    if not isinstance(variants, list) or len(variants) < 2:
        print(json.dumps({"ok": False, "error": "need at least 2 variants to compare", "variants": []}))
        return 1

    control = pick_control(variants)
    challengers = [v for v in variants if v is not control]

    # Bonferroni: testing 6 variants at alpha=0.05 gives a ~26% chance of at
    # least one false positive. Dividing the threshold by the number of tests
    # holds the family-wise error rate at 5%.
    alpha_adj = args.alpha / max(len(challengers), 1)

    try:
        c_succ, c_n = numerator_denominator(control, args.metric)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e), "variants": []}))
        return 1

    c_rate = (c_succ / c_n) if c_n else 0.0
    out = []

    for v in challengers:
        succ, n = numerator_denominator(v, args.metric)
        rate = (succ / n) if n else 0.0
        z, p = two_proportion_z(c_succ, c_n, succ, n)
        lift = ((rate - c_rate) / c_rate) if c_rate else 0.0
        decision, reason = decide(v, lift, p, alpha_adj)

        out.append({
            "variantId": v.get("variantId"),
            "adId": v.get("adIds", [None])[0] if v.get("adIds") else v.get("adId"),
            "adSetId": v.get("adSetId"),
            "campaignId": v.get("campaignId"),
            "metric": args.metric,
            "rate": round(rate, 6),
            "controlRate": round(c_rate, 6),
            "lift": round(lift, 4),
            "zScore": round(z, 4),
            "pValue": round(p, 6),
            "significant": p < alpha_adj,
            "decision": decision,
            "reason": reason,
            # carried through so the logging node downstream has one flat record
            "ctr": v.get("ctr"), "cvr": v.get("cvr"), "cpa": v.get("cpa"),
            "roas": v.get("roas"), "epc": v.get("epc"),
            "targetRoas": v.get("targetRoas"),
            "currentBudget": v.get("currentBudget", 0),
        })

    print(json.dumps({
        "ok": True,
        "control": control.get("variantId"),
        "metric": args.metric,
        "alpha": args.alpha,
        "alphaAdjusted": round(alpha_adj, 6),
        "comparisons": len(challengers),
        "variants": out,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
