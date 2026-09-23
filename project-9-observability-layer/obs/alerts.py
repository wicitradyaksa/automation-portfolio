"""Alert rules as data, plus the generator that writes prometheus/alerts.yml.

Why generate rather than hand-write the YAML: the committed YAML is what
Prometheus loads, but a hand-written rule file drifts from the metrics the
collector actually emits, and you only discover it during an incident when
the alert you were relying on turns out to reference a metric that was
renamed six months ago.

Defining the rules here lets ``tests/test_alerts.py`` assert two things that
matter and cannot be checked by reading YAML:

1. every ``expr`` references a metric the collector really registers;
2. the committed YAML matches this source (a drift check).

Every rule carries a ``runbook_url``. An alert with no runbook wakes someone
up to read a PromQL expression and guess, which is how people learn to ignore
alerts.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

ALERTS_PATH = pathlib.Path(__file__).resolve().parent.parent / "prometheus" / "alerts.yml"

VALID_SEVERITY = ("critical", "warning", "info")


@dataclass(frozen=True)
class Rule:
    alert: str
    expr: str
    duration: str
    severity: str
    summary: str
    description: str
    runbook_url: str
    # Which of this estate's workflows the rule is meant to protect. Not used
    # by Prometheus; used by the tests and by whoever is deciding what to page
    # on at 3am.
    applies_to: tuple = field(default=())


RULES = [
    Rule(
        alert="WorkflowStoppedRunning",
        # THE most important rule here. A scheduled workflow that stopped
        # firing produces zero runs, therefore zero failures, therefore a
        # green dashboard. Rate-based alerting is structurally blind to it.
        # Only the age of the last success sees it.
        expr='time() - n8n_workflow_last_success_timestamp_seconds{workflow!=""} > 5400',
        duration="10m",
        severity="critical",
        summary="{{ $labels.workflow }} has not succeeded in over 90 minutes",
        description=(
            "The last successful run was more than 90 minutes ago. For a workflow on a "
            "schedule this usually means it is not running at all -- a disabled trigger, a "
            "dead n8n worker, or a credential that expired. Note that a failure-rate alert "
            "CANNOT detect this: no runs means no failures."
        ),
        runbook_url="https://github.com/wicitradyaksa/automation-portfolio/blob/main/project-9-observability-layer/RUNBOOK.md#workflowstoppedrunning",
        applies_to=("uptime-monitor", "vps-ops-nightly", "dco-engine"),
    ),
    Rule(
        alert="WorkflowErrorRateHigh",
        expr=(
            'sum by (workflow) (rate(n8n_workflow_runs_total{status="error"}[15m]))\n'
            "  /\n"
            "clamp_min(sum by (workflow) (rate(n8n_workflow_runs_total[15m])), 0.0001)\n"
            "  > 0.1"
        ),
        duration="15m",
        severity="warning",
        summary="{{ $labels.workflow }} is failing more than 10% of runs",
        description=(
            "Over 10% of executions in the last 15 minutes ended in error. The clamp_min in "
            "the denominator is deliberate: without it, a workflow with zero runs divides by "
            "zero and the alert flaps between firing and 'no data' forever."
        ),
        runbook_url="https://github.com/wicitradyaksa/automation-portfolio/blob/main/project-9-observability-layer/RUNBOOK.md#workflowerrorratehigh",
    ),
    Rule(
        alert="WorkflowDurationDegraded",
        expr=(
            "histogram_quantile(0.95,\n"
            "  sum by (workflow, le) (rate(n8n_workflow_duration_seconds_bucket[30m]))\n"
            ") > 300"
        ),
        duration="20m",
        severity="warning",
        summary="{{ $labels.workflow }} p95 duration is over 5 minutes",
        description=(
            "p95 rather than mean, because the mean hides the tail and the tail is what times "
            "out. Sum the buckets BEFORE histogram_quantile -- quantile-of-an-average is not "
            "an average-of-quantiles, and doing it the other way round gives a plausible "
            "number that is wrong."
        ),
        runbook_url="https://github.com/wicitradyaksa/automation-portfolio/blob/main/project-9-observability-layer/RUNBOOK.md#workflowdurationdegraded",
    ),
    Rule(
        alert="WorkflowRetriesClimbing",
        expr="sum by (workflow) (rate(n8n_workflow_retries_total[10m])) > 0.2",
        duration="10m",
        severity="info",
        summary="{{ $labels.workflow }} is retrying more than usual",
        description=(
            "Retries are absorbing failures that have not surfaced yet. This is the earliest "
            "signal available and it is usually 20-30 minutes ahead of the error-rate alert. "
            "Severity is info on purpose: it is a hint to look, not a reason to wake someone."
        ),
        runbook_url="https://github.com/wicitradyaksa/automation-portfolio/blob/main/project-9-observability-layer/RUNBOOK.md#workflowretriesclimbing",
    ),
    Rule(
        alert="WorkflowSucceedingButDoingNothing",
        expr=(
            'sum by (workflow) (rate(n8n_workflow_runs_total{status="success"}[1h])) > 0\n'
            "  and\n"
            "sum by (workflow) (rate(n8n_workflow_items_processed_total[1h])) == 0"
        ),
        duration="2h",
        severity="warning",
        summary="{{ $labels.workflow }} is succeeding but processing zero items",
        description=(
            "Exit code 0 is not evidence that a job did its work. A workflow whose source "
            "went empty -- an inbox filter that stopped matching, a sheet that was renamed, "
            "an API returning an empty page -- succeeds forever while delivering nothing. "
            "This is the same class of bug as a backup that completes and contains no files."
        ),
        runbook_url="https://github.com/wicitradyaksa/automation-portfolio/blob/main/project-9-observability-layer/RUNBOOK.md#workflowsucceedingbutdoingnothing",
    ),
    Rule(
        alert="CollectorRejectingEvents",
        expr="rate(n8n_collector_rejected_events_total[15m]) > 0",
        duration="15m",
        severity="warning",
        summary="The metrics collector is rejecting events",
        description=(
            "Malformed events are being dropped, so the dashboards are now under-reporting by "
            "an unknown amount. Monitoring that has quietly gone blind is worse than no "
            "monitoring, because it is still trusted."
        ),
        runbook_url="https://github.com/wicitradyaksa/automation-portfolio/blob/main/project-9-observability-layer/RUNBOOK.md#collectorrejectingevents",
    ),
    Rule(
        alert="MetricsEndpointDown",
        expr='up{job="n8n-observability"} == 0',
        duration="5m",
        severity="critical",
        summary="Prometheus cannot scrape the observability exporter",
        description=(
            "Every other rule in this file depends on this scrape. While it is down, the "
            "absence of alerts means nothing -- which is precisely when people assume it "
            "means everything is fine."
        ),
        runbook_url="https://github.com/wicitradyaksa/automation-portfolio/blob/main/project-9-observability-layer/RUNBOOK.md#metricsendpointdown",
    ),
]


def metric_names_in(expr: str) -> set[str]:
    """Pull bare metric names out of a PromQL expression.

    Not a PromQL parser -- it strips function names, label matchers, numbers
    and keywords, which is enough to answer "does every metric this rule
    mentions actually exist". A real parser would be the right answer if these
    rules got much more complex.
    """
    functions = {
        "rate", "sum", "by", "avg", "max", "min", "count", "increase", "time",
        "histogram_quantile", "clamp_min", "clamp_max", "and", "or", "unless",
        "le", "on", "ignoring", "group_left", "group_right", "topk", "absent",
        "delta", "irate", "quantile", "without", "offset", "bool", "up",
    }
    # Two kinds of label reference have to go first, or label NAMES come back
    # as metric names and the "does this metric exist" check fails on things
    # that are fine:
    #   1. matchers  -- {status="error"}
    #   2. groupings -- sum by (workflow, le) / without (...) / on (...)
    stripped = re.sub(r"\{[^}]*\}", " ", expr)
    stripped = re.sub(
        r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", stripped
    )
    candidates = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", stripped))
    return {c for c in candidates if c not in functions and not c.isdigit()}


def render_yaml() -> str:
    """Hand-rolled YAML writer for this one fixed shape.

    PyYAML is not a dependency of this repo, and pulling one in to serialise a
    structure this regular is not worth it. The test suite round-trips the
    result with a parser to prove it is valid, which is the part that matters.
    """
    lines = [
        "# GENERATED FILE -- do not edit by hand.",
        "# Source: obs/alerts.py. Regenerate with:  py -m obs.alerts",
        "# tests/test_alerts_and_dashboard.py fails the build if this drifts.",
        "groups:",
        "  - name: n8n-estate",
        "    rules:",
    ]
    for rule in RULES:
        lines.append("      - alert: " + rule.alert)
        expr_lines = rule.expr.split("\n")
        if len(expr_lines) == 1:
            lines.append("        expr: " + _scalar(rule.expr))
        else:
            lines.append("        expr: |-")
            for line in expr_lines:
                lines.append("          " + line)
        lines.append("        for: " + rule.duration)
        lines.append("        labels:")
        lines.append("          severity: " + rule.severity)
        lines.append("          team: automation")
        lines.append("        annotations:")
        lines.append("          summary: " + _scalar(rule.summary))
        lines.append("          description: " + _scalar(rule.description))
        lines.append("          runbook_url: " + _scalar(rule.runbook_url))
    return "\n".join(lines) + "\n"


def _scalar(value: str) -> str:
    """Always double-quote. These strings contain ``{{ }}``, ``:``, ``>`` and
    ``#``, every one of which changes meaning in unquoted YAML."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def main() -> int:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_PATH.write_text(render_yaml(), encoding="utf-8")
    print("wrote " + str(ALERTS_PATH) + " (" + str(len(RULES)) + " rules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
