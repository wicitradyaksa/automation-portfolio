"""Before/after business impact estimates for the five featured workflows.

    py scripts/impact_estimates.py            print the table
    py scripts/impact_estimates.py --write    regenerate docs/impact-estimates.md

Every figure the CV and portfolio site quote comes from here. Each workflow's assumptions
are written out as named numbers, so a reader who thinks one is wrong can change it
and rerun. Labels:
  estimate   - modelled from the stated assumptions for a typical small team
  benchmark  - from the Project 10 before/after model (method real, dataset synthetic)
  real       - an observed result from employment
"""
import sys
from pathlib import Path

RATE = 42          # A$ per staff hour, fully loaded (same rate as Project 10)
WEEKS = 46         # working weeks a year: leave and holidays excluded (same as Project 10)

WORKFLOWS = [
    {
        "key": "leads", "name": "Lead Capture & CRM Sync", "project": 1, "label": "estimate",
        "assumptions": "60 leads/week from several forms. By hand, each takes ~5 min to find in an inbox, check "
                       "for duplicates, type into the CRM and reply. Automated, only the ~5% invalid submissions "
                       "need a 2-minute look.",
        "manual_h": 60 * 5 / 60, "auto_h": 60 * 0.05 * 2 / 60,
        "before": "Leads copied from several inboxes by hand; replies go out hours later; the same person "
                  "entered twice",
        "after": "Every lead captured, de-duplicated and answered in under a minute; sales pinged instantly",
        "errors": "Duplicate CRM contacts eliminated; invalid emails rejected before they reach the CRM",
        "privacy": "Lead data goes straight from your form to your CRM on your own server, not through a "
                   "third-party automation service",
    },
    {
        "key": "invoices", "name": "Invoice Intake & Approval", "project": 2, "label": "benchmark",
        "assumptions": "About 40 invoices per run, 5 runs a week. Figures come from the Project 10 model: "
                       "2.21 min per invoice by hand vs 0.43 min automated, rework included, failed "
                       "automated runs costed at full manual rate, build cost 34 h.",
        "manual_h": None, "auto_h": None, "saved_h_week": 7.4, "annual_value": 14297, "payback_weeks": 4.6,
        "before": "Every PDF opened, six fields typed, totals checked and approvals routed from memory: "
                  "~90 minutes a day",
        "after": "Invoices read, checked and routed automatically; people only see the ~5% that need judgement",
        "errors": "Error rate 1.56% to 0.63%; unreadable invoices go to a person instead of being guessed",
        "privacy": "Invoices are parsed on your own server, with no upload to an external OCR service; every "
                   "decision is logged in a sheet you own",
    },
    {
        "key": "creative", "name": "Ad Creative Production (AI + video)", "project": "4-5", "label": "estimate",
        "assumptions": "One creative concept a week. By hand: ~3 h exporting 12 video variants plus ~2 h "
                       "generating, cropping and exporting images for 4 placements. Automated: ~30 min "
                       "reviewing the output.",
        "manual_h": 5.0, "auto_h": 0.5,
        "before": "An afternoon per concept exporting variants one by one; broken files sometimes go live and "
                  "waste ad spend",
        "after": "One spreadsheet row produces every image and video variant overnight, each checked before release",
        "errors": "0 unverified files reach the ad account: every render is checked for length, size and dimensions",
        "privacy": "Prompts and creative strategy stay on your own GPU box; metadata that would leak the prompt "
                   "is stripped",
    },
    {
        "key": "ads", "name": "Ad Spend Optimisation (DCO)", "project": 6, "label": "estimate + real",
        "assumptions": "Manual review of ad and affiliate dashboards takes ~45 min a day, 5 days a week. "
                       "Automated: ~15 min a week reading the report. The 60% figure is a real result from "
                       "Ecomobi, where the same playbook was run with rule-based automation.",
        "manual_h": 45 * 5 / 60, "auto_h": 15 / 60,
        "before": "Daily dashboard checks, and gut-feel decisions that scale budget into ads that only look like winners",
        "after": "Spend and revenue joined every 6 hours; budget moves only when the result is statistically real, "
                 "capped at +20%",
        "errors": "No budget changes on noise; losing ads paused automatically and replacements queued",
        "privacy": "Revenue and spend data stay in your own systems; every decision is logged with its reason",
        "real_result": "Wasted ad spend cut by 60% (Ecomobi, $4K/month budget, 180% average ROI)",
    },
    {
        "key": "ops", "name": "Server Ops & Nightly Data Pipeline", "project": "3-7", "label": "estimate",
        "assumptions": "Manual daily server, backup and data-load checks take ~20 min a day, plus ~4 hung-service "
                       "incidents a month at ~45 min each to notice, log in, restart and verify. Automated: "
                       "~10 min a week reading the digest.",
        "manual_h": 20 * 5 / 60 + 4 * 45 / 60 * 12 / 52, "auto_h": 10 / 60,
        "before": "Someone checks servers and backups by hand, and finds out a backup was empty on the day they need it",
        "after": "Nightly checks, verified backups and self-healing restarts; people are paged only when a fix fails",
        "errors": "0 empty backups counted as good; bad data loads quarantined instead of reaching dashboards",
        "privacy": "Runs entirely on your own infrastructure; database credentials never appear in logs",
        "downtime": "Hung-service downtime ~45 min to ~6 min per incident (5-minute detection + automatic restart)",
    },
]


def compute(w):
    if w.get("saved_h_week") is None:
        w["saved_h_week"] = round(w["manual_h"] - w["auto_h"], 1)
    w["saved_h_year"] = round(w["saved_h_week"] * WEEKS)
    w.setdefault("annual_value", round(w["saved_h_week"] * WEEKS * RATE))
    return w


def totals(ws):
    return sum(w["saved_h_year"] for w in ws), sum(w["annual_value"] for w in ws)


def markdown(ws):
    hours, value = totals(ws)
    out = ["# Business Impact Estimates", "",
           "Generated by [`scripts/impact_estimates.py`](../scripts/impact_estimates.py). Do not edit by hand.", "",
           f"Rate: **A${RATE}/hour** fully loaded, **{WEEKS} working weeks** a year (the same accounting as "
           "[Project 10](../project-10-migration-case-study)). Each figure is labelled:", "",
           "- **estimate**: modelled from the assumptions below for a typical small team. Change them and rerun.",
           "- **benchmark**: from the Project 10 model. The method is real, but the dataset is synthetic until real timings are collected.",
           "- **real**: an observed result from employment.", "",
           "| Workflow | Hours saved / week | Hours saved / year | Value / year | Label |",
           "|---|---|---|---|---|"]
    for w in ws:
        out.append(f"| {w['name']} | {w['saved_h_week']} | {w['saved_h_year']} | A${w['annual_value']:,} | {w['label']} |")
    out += [f"| **All five** | | **{hours:,}** | **A${value:,}** | |", "",
            "## Before and after", ""]
    for w in ws:
        out += [f"### {w['name']} (project {w['project']})", "",
                f"- **Before:** {w['before']}.",
                f"- **After:** {w['after']}.",
                f"- **Fewer errors:** {w['errors']}.",
                f"- **Data privacy & control:** {w['privacy']}."]
        for extra in ("real_result", "downtime"):
            if w.get(extra):
                out.append(f"- **{'Real result' if extra == 'real_result' else 'Downtime'}:** {w[extra]}.")
        if w.get("payback_weeks"):
            out.append(f"- **Payback:** {w['payback_weeks']} weeks on the build.")
        out += [f"- **Assumptions:** {w['assumptions']}", ""]
    out += ["## What these numbers are not", "",
            "They are not measurements from a client deployment. They show the method for sizing an "
            "automation before building it. In a real engagement, the first week is spent timing the current "
            "process so these assumptions are replaced with that business's own numbers.", ""]
    return "\n".join(out)


def main():
    ws = [compute(dict(w)) for w in WORKFLOWS]
    if "--write" in sys.argv:
        path = Path(__file__).resolve().parent.parent / "docs" / "impact-estimates.md"
        path.write_text(markdown(ws), encoding="utf-8")
        print("wrote", path)
    for w in ws:
        print(f"{w['name']:40} {w['saved_h_week']:>5} h/wk {w['saved_h_year']:>5} h/yr  A${w['annual_value']:>7,}  {w['label']}")
    hours, value = totals(ws)
    print(f"{'All five':40} {'':>11} {hours:>5} h/yr  A${value:>7,}")


if __name__ == "__main__":
    main()
