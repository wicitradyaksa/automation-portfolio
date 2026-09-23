"""Compute the before/after numbers from the raw logs.

Why this is a script and not a paragraph
----------------------------------------
Every number in this project's README is produced by running this file against
the CSVs in ``data/``. Nothing is typed in by hand.

That is the whole point. "Saved 12 hours a week" in a portfolio is a claim a
reader has to take on trust, and the honest ones and the invented ones look
identical. A number you can regenerate from the data is a different kind of
statement -- and being able to say "here is the script, here are the rows,
disagree with my method if you like" is worth more than a bigger number.

It also forces a discipline: every figure here has a confidence interval or a
stated caveat, because a point estimate from 60 observations pretending to be
precise is its own kind of dishonesty.

Usage:
    py scripts/analyse_impact.py
    py scripts/analyse_impact.py --json
    py scripts/analyse_impact.py --markdown
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import statistics
from dataclasses import asdict, dataclass

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

# What it cost to build, in hours. Measured by the same timesheet the manual
# runs were timed against, not estimated afterwards -- an estimated build cost
# is always too low, and it is the denominator of the payback figure.
BUILD_HOURS = 34.0

# Fully-loaded hourly cost of the person who was doing this by hand.
# Australian dollars, a mid-range operations wage plus on-costs. Stated as an
# assumption rather than hidden in the arithmetic, because a reader with a
# different number can substitute it.
HOURLY_COST_AUD = 42.0


@dataclass
class Run:
    date: str
    batch_size: int
    minutes: float
    errors: int
    rework_minutes: float
    note: str = ""

    @property
    def total_minutes(self) -> float:
        """Handling plus rework. Rework is part of the cost of the process.

        Excluding it would flatter the automation, because most of the manual
        process's rework came from mistakes the automation cannot make.
        """
        return self.minutes + self.rework_minutes


def load(path: pathlib.Path) -> list[Run]:
    runs = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            runs.append(
                Run(
                    date=row["date"],
                    batch_size=int(row["batch_size"]),
                    minutes=float(row["minutes"]),
                    errors=int(row["errors"]),
                    rework_minutes=float(row["rework_minutes"]),
                    note=row.get("note", ""),
                )
            )
    if not runs:
        raise ValueError("no rows in " + str(path))
    return runs


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile.

    Deliberately not interpolated. With ~60 observations, interpolation
    invents precision the sample does not support, and nearest-rank always
    returns a value that was actually observed -- which matters when someone
    asks "which run was that".
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for a proportion, Wilson score.

    Not the normal approximation (p +/- z*sqrt(p(1-p)/n)). At the error rates
    here the automated arm has very few errors, and the normal approximation
    produces intervals that extend below zero -- a negative error rate, which
    is visibly wrong and quietly discredits everything next to it. Wilson
    stays inside [0,1] and is well-behaved for small counts.
    """
    if trials == 0:
        return (0.0, 0.0)
    phat = successes / trials
    denominator = 1 + z**2 / trials
    centre = (phat + z**2 / (2 * trials)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / trials + z**2 / (4 * trials**2)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class ArmSummary:
    label: str
    runs: int
    items: int
    median_minutes: float
    mean_minutes: float
    p95_minutes: float
    minutes_per_item: float
    total_hours: float
    errors: int
    error_rate: float
    error_rate_low: float
    error_rate_high: float
    rework_hours: float


def summarise(runs: list[Run], label: str) -> ArmSummary:
    totals = [r.total_minutes for r in runs]
    items = sum(r.batch_size for r in runs)
    errors = sum(r.errors for r in runs)
    low, high = wilson_interval(errors, items)
    return ArmSummary(
        label=label,
        runs=len(runs),
        items=items,
        median_minutes=round(statistics.median(totals), 1),
        mean_minutes=round(statistics.fmean(totals), 1),
        p95_minutes=round(percentile(totals, 95), 1),
        # Per item, not per run. Batch sizes differ between the two periods,
        # so comparing per-run times alone would compare different amounts of
        # work and report the difference as an improvement.
        minutes_per_item=round(sum(totals) / items, 3),
        total_hours=round(sum(totals) / 60, 1),
        errors=errors,
        error_rate=round(errors / items, 4),
        error_rate_low=round(low, 4),
        error_rate_high=round(high, 4),
        rework_hours=round(sum(r.rework_minutes for r in runs) / 60, 1),
    )


def compare(before: ArmSummary, after: ArmSummary) -> dict:
    """Everything derived, normalised per item, with payback."""
    # Weekly volume from the *after* period, because that is the volume the
    # saving applies to going forward. Using the before period's volume would
    # overstate it if throughput grew.
    runs_per_week = 5.0
    items_per_week = (after.items / after.runs) * runs_per_week

    minutes_saved_per_item = before.minutes_per_item - after.minutes_per_item

    # Rounded once, here, and every downstream figure derived from the rounded
    # value. Deriving the annual number from the unrounded one instead makes
    # the document internally inconsistent -- the published 7.4 h/week times 46
    # would not equal the published annual total, and a reader checking the
    # arithmetic finds a discrepancy they cannot explain. In a case study whose
    # entire argument is "these numbers are checkable", that is fatal.
    hours_saved_per_week = round(minutes_saved_per_item * items_per_week / 60, 1)

    payback_weeks = BUILD_HOURS / hours_saved_per_week if hours_saved_per_week > 0 else float("inf")

    error_reduction = (
        (before.error_rate - after.error_rate) / before.error_rate if before.error_rate else 0.0
    )

    return {
        "minutes_saved_per_item": round(minutes_saved_per_item, 3),
        "items_per_week": round(items_per_week, 1),
        "hours_saved_per_week": hours_saved_per_week,
        "hours_saved_per_year": round(hours_saved_per_week * 46, 1),  # 46, not 52 -- see README
        "build_hours": BUILD_HOURS,
        "payback_weeks": round(payback_weeks, 1),
        "annual_value_aud": round(hours_saved_per_week * 46 * HOURLY_COST_AUD, 0),
        "hourly_cost_aud": HOURLY_COST_AUD,
        "speedup_factor": round(before.minutes_per_item / after.minutes_per_item, 1)
        if after.minutes_per_item
        else None,
        "error_rate_before": before.error_rate,
        "error_rate_after": after.error_rate,
        "error_reduction_pct": round(error_reduction * 100, 1),
        "rework_hours_saved_per_period": round(before.rework_hours - after.rework_hours, 1),
        "p95_minutes_before": before.p95_minutes,
        "p95_minutes_after": after.p95_minutes,
        "tail_ratio_before": round(before.p95_minutes / before.median_minutes, 2)
        if before.median_minutes
        else None,
        "tail_ratio_after": round(after.p95_minutes / after.median_minutes, 2)
        if after.median_minutes
        else None,
    }


def analyse() -> dict:
    before = summarise(load(DATA / "before_manual.csv"), "manual")
    after = summarise(load(DATA / "after_automated.csv"), "automated")
    return {
        "before": asdict(before),
        "after": asdict(after),
        "comparison": compare(before, after),
    }


def render_text(result: dict) -> str:
    before, after, comp = result["before"], result["after"], result["comparison"]
    lines = [
        "",
        "Invoice intake: manual vs automated",
        "=" * 66,
        "",
        "%-28s %14s %14s" % ("", "MANUAL", "AUTOMATED"),
        "-" * 66,
        "%-28s %14d %14d" % ("Runs observed", before["runs"], after["runs"]),
        "%-28s %14d %14d" % ("Invoices processed", before["items"], after["items"]),
        "%-28s %14.1f %14.1f" % ("Median minutes / run", before["median_minutes"], after["median_minutes"]),
        "%-28s %14.1f %14.1f" % ("p95 minutes / run", before["p95_minutes"], after["p95_minutes"]),
        "%-28s %14.2f %14.2f" % ("Minutes / invoice", before["minutes_per_item"], after["minutes_per_item"]),
        "%-28s %14d %14d" % ("Errors", before["errors"], after["errors"]),
        "%-28s %13.2f%% %13.2f%%" % ("Error rate", before["error_rate"] * 100, after["error_rate"] * 100),
        "%-28s %14s %14s"
        % (
            "  95% CI",
            "%.2f-%.2f%%" % (before["error_rate_low"] * 100, before["error_rate_high"] * 100),
            "%.2f-%.2f%%" % (after["error_rate_low"] * 100, after["error_rate_high"] * 100),
        ),
        "%-28s %14.1f %14.1f" % ("Rework hours", before["rework_hours"], after["rework_hours"]),
        "",
        "-" * 66,
        "Impact",
        "-" * 66,
        "  Speed up                  %.1fx per invoice" % comp["speedup_factor"],
        "  Hours saved per week      %.1f" % comp["hours_saved_per_week"],
        "  Hours saved per year      %.1f  (46 working weeks)" % comp["hours_saved_per_year"],
        "  Build cost                %.1f hours" % comp["build_hours"],
        "  Payback                   %.1f weeks" % comp["payback_weeks"],
        "  Annual value              A$%s  (at A$%.0f/hr)"
        % ("{:,.0f}".format(comp["annual_value_aud"]), comp["hourly_cost_aud"]),
        "  Error rate reduction      %.1f%%" % comp["error_reduction_pct"],
        "  Tail (p95/median)         %.2f -> %.2f" % (comp["tail_ratio_before"], comp["tail_ratio_after"]),
        "",
    ]
    return "\n".join(lines)


def render_markdown(result: dict) -> str:
    before, after, comp = result["before"], result["after"], result["comparison"]
    return "\n".join(
        [
            "| Measure | Manual | Automated | Change |",
            "|---|---|---|---|",
            "| Runs observed | %d | %d | - |" % (before["runs"], after["runs"]),
            "| Invoices processed | %d | %d | - |" % (before["items"], after["items"]),
            "| Median minutes per run | %.1f | %.1f | -%.0f%% |"
            % (
                before["median_minutes"],
                after["median_minutes"],
                (1 - after["median_minutes"] / before["median_minutes"]) * 100,
            ),
            "| p95 minutes per run | %.1f | %.1f | -%.0f%% |"
            % (
                before["p95_minutes"],
                after["p95_minutes"],
                (1 - after["p95_minutes"] / before["p95_minutes"]) * 100,
            ),
            "| Minutes per invoice | %.2f | %.2f | **%.1fx faster** |"
            % (before["minutes_per_item"], after["minutes_per_item"], comp["speedup_factor"]),
            "| Error rate | %.2f%% | %.2f%% | -%.1f%% |"
            % (before["error_rate"] * 100, after["error_rate"] * 100, comp["error_reduction_pct"]),
            "| 95%% CI on error rate | %.2f-%.2f%% | %.2f-%.2f%% | - |"
            % (
                before["error_rate_low"] * 100,
                before["error_rate_high"] * 100,
                after["error_rate_low"] * 100,
                after["error_rate_high"] * 100,
            ),
            "| Rework hours over the period | %.1f | %.1f | -%.1f |"
            % (before["rework_hours"], after["rework_hours"], comp["rework_hours_saved_per_period"]),
        ]
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compute the before/after numbers from data/")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--markdown", action="store_true", help="the table used in README.md")
    args = parser.parse_args(argv)

    result = analyse()
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.markdown:
        print(render_markdown(result))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
