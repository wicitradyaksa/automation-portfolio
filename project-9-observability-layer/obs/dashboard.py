"""Grafana dashboard, generated from the same source of truth as the metrics.

Same argument as ``alerts.py``: a dashboard JSON exported from the Grafana UI
is 900 lines of machine-written noise, it is unreviewable in a diff, and it
silently references metrics that no longer exist. Generating it means the test
suite can assert that **every panel query references a metric the collector
actually emits**, which is the failure that otherwise shows up as an empty
panel nobody notices for a month.

Panel order is deliberate and is the triage order:

1. Is anything stale? (the alert that rate-based monitoring cannot produce)
2. Is anything failing?
3. Is anything slow?
4. Is anything retrying? (the early warning)
5. Is anything succeeding without doing work?
"""

from __future__ import annotations

import json
import pathlib

DASHBOARD_PATH = pathlib.Path(__file__).resolve().parent.parent / "grafana" / "dashboard.json"

DATASOURCE = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}


def _panel(panel_id, title, description, targets, grid, unit="short", panel_type="timeseries", extra=None):
    panel = {
        "id": panel_id,
        "type": panel_type,
        "title": title,
        "description": description,
        "datasource": DATASOURCE,
        "gridPos": grid,
        "targets": [
            {"expr": expr, "legendFormat": legend, "refId": chr(65 + i), "datasource": DATASOURCE}
            for i, (expr, legend) in enumerate(targets)
        ],
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
    }
    if extra:
        panel.update(extra)
    return panel


PANELS = [
    _panel(
        1,
        "Minutes since last success",
        (
            "The panel to look at first. A scheduled workflow that stopped firing shows up "
            "here and NOWHERE else -- zero runs means zero failures, so every rate-based "
            "panel stays green while nothing happens."
        ),
        [("(time() - n8n_workflow_last_success_timestamp_seconds) / 60", "{{workflow}}")],
        {"h": 8, "w": 12, "x": 0, "y": 0},
        unit="m",
        panel_type="stat",
        extra={
            "options": {"colorMode": "background", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"]}},
            "fieldConfig": {
                "defaults": {
                    "unit": "m",
                    "thresholds": {
                        "mode": "absolute",
                        # 30m amber, 90m red. Matches WorkflowStoppedRunning so
                        # the dashboard and the pager cannot disagree.
                        "steps": [
                            {"color": "green", "value": None},
                            {"color": "orange", "value": 30},
                            {"color": "red", "value": 90},
                        ],
                    },
                },
                "overrides": [],
            },
        },
    ),
    _panel(
        2,
        "Run rate by status",
        "Executions per minute, split by terminal status. The shape of the error series matters more than its height.",
        [
            ('sum by (status) (rate(n8n_workflow_runs_total[5m]) * 60)', "{{status}}"),
        ],
        {"h": 8, "w": 12, "x": 12, "y": 0},
        unit="/min",
    ),
    _panel(
        3,
        "Error ratio by workflow",
        (
            "clamp_min in the denominator stops a zero-run workflow dividing by zero and "
            "flapping between a value and 'no data'."
        ),
        [
            (
                'sum by (workflow) (rate(n8n_workflow_runs_total{status="error"}[15m])) '
                "/ clamp_min(sum by (workflow) (rate(n8n_workflow_runs_total[15m])), 0.0001)",
                "{{workflow}}",
            )
        ],
        {"h": 8, "w": 12, "x": 0, "y": 8},
        unit="percentunit",
    ),
    _panel(
        4,
        "Duration p50 / p95 / p99",
        (
            "Buckets are summed before histogram_quantile. Doing it the other way round "
            "averages quantiles, which produces a plausible number that is wrong."
        ),
        [
            ("histogram_quantile(0.50, sum by (le) (rate(n8n_workflow_duration_seconds_bucket[10m])))", "p50"),
            ("histogram_quantile(0.95, sum by (le) (rate(n8n_workflow_duration_seconds_bucket[10m])))", "p95"),
            ("histogram_quantile(0.99, sum by (le) (rate(n8n_workflow_duration_seconds_bucket[10m])))", "p99"),
        ],
        {"h": 8, "w": 12, "x": 12, "y": 8},
        unit="s",
    ),
    _panel(
        5,
        "Retry rate -- the early warning",
        "Usually moves 20-30 minutes before the error ratio does. Worth watching, not worth paging on.",
        [("sum by (workflow) (rate(n8n_workflow_retries_total[10m]) * 60)", "{{workflow}}")],
        {"h": 8, "w": 12, "x": 0, "y": 16},
        unit="/min",
    ),
    _panel(
        6,
        "Items processed vs runs",
        (
            "Succeeding and doing work are different claims. A flat item count under a healthy "
            "run count means the source went empty and nobody noticed."
        ),
        [
            ("sum by (workflow) (rate(n8n_workflow_items_processed_total[15m]) * 60)", "items/min {{workflow}}"),
            ('sum by (workflow) (rate(n8n_workflow_runs_total{status="success"}[15m]) * 60)', "runs/min {{workflow}}"),
        ],
        {"h": 8, "w": 12, "x": 12, "y": 16},
        unit="/min",
    ),
    _panel(
        7,
        "Failures by node",
        "Which node inside the workflow broke. Skips the first step of triage entirely.",
        [("topk(10, sum by (workflow, node) (increase(n8n_workflow_node_failures_total[6h])))", "{{workflow}} / {{node}}")],
        {"h": 8, "w": 24, "x": 0, "y": 24},
        panel_type="barchart",
    ),
]


def build() -> dict:
    return {
        "title": "n8n Estate -- Workflow Observability",
        "uid": "n8n-estate-obs",
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "tags": ["n8n", "automation", "portfolio"],
        "templating": {
            "list": [
                {
                    "name": "DS_PROMETHEUS",
                    "type": "datasource",
                    "query": "prometheus",
                    "current": {},
                    "hide": 0,
                }
            ]
        },
        "annotations": {"list": []},
        "panels": PANELS,
    }


def all_exprs() -> list[str]:
    return [target["expr"] for panel in PANELS for target in panel["targets"]]


def main() -> int:
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print("wrote " + str(DASHBOARD_PATH) + " (" + str(len(PANELS)) + " panels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
