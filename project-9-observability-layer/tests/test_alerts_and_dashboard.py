"""The tests that make this project more than a screenshot.

Three properties nobody checks by reading YAML, and all three cause silent
blindness when they break:

1. **Every alert expr references a metric the collector actually emits.**
   A renamed metric leaves a rule that can never fire, and it looks healthy.
2. **The committed YAML/JSON match their Python source.** Hand-editing a
   generated file is how the two diverge.
3. **Every alert carries a severity and a runbook_url.** An alert with no
   runbook wakes someone to read PromQL and guess.
"""

import json
import pathlib
import re
import unittest

from obs import alerts, dashboard
from obs.collector import Collector
from obs.metrics import Registry
from tests import yamlish

ROOT = pathlib.Path(__file__).resolve().parent.parent


def emitted_metric_names() -> set[str]:
    """Everything the collector registers, including derived series names."""
    registry = Registry()
    Collector(registry=registry)
    names = set(registry.names())
    for name in list(names):
        # A histogram exposes three derived series that PromQL refers to
        # directly; a rule mentioning _bucket is mentioning a real metric.
        names.update({name + "_bucket", name + "_sum", name + "_count"})
    # Provided by Prometheus itself, not by us.
    names.add("up")
    return names


class TestAlertsReferenceRealMetrics(unittest.TestCase):
    def test_every_expr_references_only_emitted_metrics(self):
        known = emitted_metric_names()
        for rule in alerts.RULES:
            for name in alerts.metric_names_in(rule.expr):
                with self.subTest(alert=rule.alert, metric=name):
                    self.assertIn(
                        name,
                        known,
                        rule.alert + " references '" + name + "', which the collector never emits",
                    )

    def test_the_extractor_actually_finds_metrics(self):
        """Guard against the previous test passing because the regex matched
        nothing -- a test that cannot fail is worse than no test."""
        found = alerts.metric_names_in(
            'sum by (workflow) (rate(n8n_workflow_runs_total{status="error"}[15m]))'
        )
        self.assertIn("n8n_workflow_runs_total", found)
        self.assertNotIn("workflow", found, "label names must not be read as metrics")
        self.assertNotIn("status", found)
        self.assertNotIn("rate", found)


class TestAlertQuality(unittest.TestCase):
    def test_every_rule_has_a_runbook_url(self):
        for rule in alerts.RULES:
            with self.subTest(alert=rule.alert):
                self.assertTrue(rule.runbook_url.startswith("http"))
                self.assertIn("#" + rule.alert.lower(), rule.runbook_url)

    def test_every_runbook_anchor_exists_in_RUNBOOK_md(self):
        """A runbook_url pointing at an anchor that does not exist is a dead
        link delivered at 3am."""
        text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
        headings = {
            re.sub(r"[^a-z0-9]", "", line.lstrip("# ").lower())
            for line in text.splitlines()
            if line.startswith("#")
        }
        for rule in alerts.RULES:
            with self.subTest(alert=rule.alert):
                self.assertIn(rule.alert.lower(), headings)

    def test_severities_are_valid(self):
        for rule in alerts.RULES:
            with self.subTest(alert=rule.alert):
                self.assertIn(rule.severity, alerts.VALID_SEVERITY)

    def test_every_rule_has_a_for_duration(self):
        """Without one, a single scrape blip pages someone."""
        for rule in alerts.RULES:
            with self.subTest(alert=rule.alert):
                self.assertRegex(rule.duration, r"^\d+[smh]$")

    def test_descriptions_explain_rather_than_restate(self):
        for rule in alerts.RULES:
            with self.subTest(alert=rule.alert):
                self.assertGreater(len(rule.description), 120, rule.alert + " description is a stub")

    def test_alert_names_are_unique(self):
        names = [r.alert for r in alerts.RULES]
        self.assertEqual(len(names), len(set(names)))

    def test_the_staleness_rule_exists_and_uses_last_success(self):
        """The single most important rule here, because no rate-based alert
        can detect a workflow that stopped running."""
        rule = next(r for r in alerts.RULES if r.alert == "WorkflowStoppedRunning")
        self.assertIn("n8n_workflow_last_success_timestamp_seconds", rule.expr)
        self.assertIn("time()", rule.expr)
        self.assertEqual(rule.severity, "critical")

    def test_ratio_rules_guard_against_divide_by_zero(self):
        """Without clamp_min, a workflow with no runs makes the alert flap
        between a value and 'no data' forever."""
        for rule in alerts.RULES:
            if "/" in rule.expr and "rate(" in rule.expr and "histogram_quantile" not in rule.expr:
                with self.subTest(alert=rule.alert):
                    self.assertIn("clamp_min", rule.expr, rule.alert + " divides without a clamp")

    def test_quantile_rules_sum_buckets_before_taking_the_quantile(self):
        """Quantile-of-an-average is not an average-of-quantiles. The wrong
        order gives a plausible number that is wrong."""
        for rule in alerts.RULES:
            if "histogram_quantile" in rule.expr:
                with self.subTest(alert=rule.alert):
                    self.assertRegex(rule.expr, r"histogram_quantile\([^)]*,\s*\n?\s*sum by")
                    self.assertIn("le", rule.expr)


class TestGeneratedYaml(unittest.TestCase):
    def test_committed_file_matches_the_source(self):
        """The drift check. Hand-edit prometheus/alerts.yml and this fails."""
        committed = (ROOT / "prometheus" / "alerts.yml").read_text(encoding="utf-8")
        self.assertEqual(
            committed,
            alerts.render_yaml(),
            "prometheus/alerts.yml is stale -- regenerate with `py -m obs.alerts`",
        )

    def test_generated_yaml_parses(self):
        parsed = yamlish.parse(alerts.render_yaml())
        self.assertEqual(len(parsed["groups"]), 1)
        self.assertEqual(parsed["groups"][0]["name"], "n8n-estate")
        self.assertEqual(len(parsed["groups"][0]["rules"]), len(alerts.RULES))

    def test_parsed_rules_carry_the_required_fields(self):
        parsed = yamlish.parse(alerts.render_yaml())
        for rule in parsed["groups"][0]["rules"]:
            with self.subTest(alert=rule.get("alert")):
                self.assertIn("expr", rule)
                self.assertIn("for", rule)
                self.assertIn("severity", rule["labels"])
                self.assertIn("runbook_url", rule["annotations"])
                self.assertIn("summary", rule["annotations"])

    def test_multiline_expressions_survive_the_round_trip(self):
        parsed = yamlish.parse(alerts.render_yaml())
        by_name = {r["alert"]: r for r in parsed["groups"][0]["rules"]}
        expr = by_name["WorkflowErrorRateHigh"]["expr"]
        self.assertIn("clamp_min", expr)
        self.assertIn("\n", expr)

    def test_templated_summaries_survive_quoting(self):
        """`{{ $labels.workflow }}` contains braces and a dollar sign, both of
        which change meaning in unquoted YAML."""
        parsed = yamlish.parse(alerts.render_yaml())
        by_name = {r["alert"]: r for r in parsed["groups"][0]["rules"]}
        self.assertIn("{{ $labels.workflow }}", by_name["WorkflowStoppedRunning"]["annotations"]["summary"])


class TestDashboard(unittest.TestCase):
    def test_every_panel_expr_references_an_emitted_metric(self):
        """An empty Grafana panel is invisible for months. This is the only
        cheap way to catch one."""
        known = emitted_metric_names()
        for panel in dashboard.PANELS:
            for target in panel["targets"]:
                for name in alerts.metric_names_in(target["expr"]):
                    with self.subTest(panel=panel["title"], metric=name):
                        self.assertIn(name, known, panel["title"] + " queries unknown metric " + name)

    def test_committed_dashboard_matches_the_source(self):
        committed = json.loads((ROOT / "grafana" / "dashboard.json").read_text(encoding="utf-8"))
        self.assertEqual(
            committed,
            dashboard.build(),
            "grafana/dashboard.json is stale -- regenerate with `py -m obs.dashboard`",
        )

    def test_dashboard_is_valid_json_with_the_fields_grafana_needs(self):
        built = dashboard.build()
        for key in ("title", "uid", "schemaVersion", "panels", "templating"):
            self.assertIn(key, built)
        self.assertGreater(len(built["panels"]), 0)

    def test_panel_ids_are_unique(self):
        ids = [panel["id"] for panel in dashboard.PANELS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_panels_do_not_overlap_on_the_grid(self):
        """Overlapping gridPos produces a dashboard that renders as a mess and
        is the most common defect in a hand-written one."""
        occupied = set()
        for panel in dashboard.PANELS:
            pos = panel["gridPos"]
            for x in range(pos["x"], pos["x"] + pos["w"]):
                for y in range(pos["y"], pos["y"] + pos["h"]):
                    with self.subTest(panel=panel["title"], cell=(x, y)):
                        self.assertNotIn((x, y), occupied)
                        occupied.add((x, y))

    def test_panels_fit_the_24_column_grid(self):
        for panel in dashboard.PANELS:
            pos = panel["gridPos"]
            with self.subTest(panel=panel["title"]):
                self.assertLessEqual(pos["x"] + pos["w"], 24)

    def test_every_panel_has_a_description(self):
        """A panel nobody can interpret at 3am is decoration."""
        for panel in dashboard.PANELS:
            with self.subTest(panel=panel["title"]):
                self.assertGreater(len(panel["description"]), 60)

    def test_the_staleness_panel_thresholds_match_the_alert(self):
        """The dashboard and the pager must not disagree about what is bad."""
        panel = next(p for p in dashboard.PANELS if "last success" in p["title"].lower())
        steps = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
        red = next(s["value"] for s in steps if s["color"] == "red")
        rule = next(r for r in alerts.RULES if r.alert == "WorkflowStoppedRunning")
        threshold_seconds = int(re.search(r"> (\d+)", rule.expr).group(1))
        self.assertEqual(red * 60, threshold_seconds)


if __name__ == "__main__":
    unittest.main()
