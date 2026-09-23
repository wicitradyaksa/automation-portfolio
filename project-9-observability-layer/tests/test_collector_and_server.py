"""Collector event handling and the HTTP exporter, end to end over a socket."""

import io
import json
import unittest
import urllib.error
import urllib.request

from obs.collector import Collector, Event
from obs.logging_setup import configure
from obs.metrics import Registry
from obs.server import build, serve


def collector(clock=None):
    registry = Registry()
    return registry, Collector(registry=registry, clock=clock or (lambda: 1_772_000_000.0))


class TestEventParsing(unittest.TestCase):
    def test_minimal_valid_event(self):
        event = Event.from_json({"workflow": "etl", "status": "success", "duration_seconds": 1.5})
        self.assertEqual(event.workflow, "etl")
        self.assertEqual(event.duration_seconds, 1.5)

    def test_camelCase_aliases_accepted(self):
        """n8n is inconsistent about this and a rejected event is a lost one."""
        event = Event.from_json({"workflowName": "etl", "status": "error", "durationSeconds": 2})
        self.assertEqual(event.workflow, "etl")

    def test_unknown_status_is_rejected_not_guessed(self):
        """Bucketing an unknown status as success is how a dashboard ends up
        lying about the thing it exists to report."""
        with self.assertRaises(ValueError):
            Event.from_json({"workflow": "etl", "status": "probably fine", "duration_seconds": 1})

    def test_missing_workflow_is_rejected(self):
        with self.assertRaises(ValueError):
            Event.from_json({"status": "success", "duration_seconds": 1})

    def test_absurd_workflow_name_is_rejected(self):
        """It would become a metric label, and labels are forever."""
        with self.assertRaises(ValueError):
            Event.from_json({"workflow": "x" * 500, "status": "success", "duration_seconds": 1})

    def test_negative_duration_is_rejected(self):
        """A non-monotonic clock must not poison a histogram."""
        with self.assertRaises(ValueError):
            Event.from_json({"workflow": "etl", "status": "success", "duration_seconds": -5})

    def test_non_numeric_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            Event.from_json({"workflow": "etl", "status": "success", "duration_seconds": "quick"})


class TestCollector(unittest.TestCase):
    def test_success_updates_runs_duration_and_last_success(self):
        registry, coll = collector()
        coll.record(Event("etl", "success", 2.0, items=10))
        self.assertEqual(coll.runs.get(workflow="etl", status="success"), 1)
        self.assertEqual(coll.duration.count(workflow="etl"), 1)
        self.assertEqual(coll.last_success.get(workflow="etl"), 1_772_000_000.0)
        self.assertEqual(coll.items.get(workflow="etl"), 10)

    def test_failure_does_not_move_last_success(self):
        """If it did, the staleness alert could never fire."""
        registry, coll = collector()
        coll.record(Event("etl", "success", 1.0))
        first = coll.last_success.get(workflow="etl")
        coll.clock = lambda: 1_772_099_999.0
        coll.record(Event("etl", "error", 1.0))
        self.assertEqual(coll.last_success.get(workflow="etl"), first)

    def test_failure_with_a_node_is_attributed(self):
        registry, coll = collector()
        coll.record(Event("etl", "error", 1.0, node="Postgres"))
        self.assertEqual(coll.failing_node.get(workflow="etl", node="Postgres"), 1)

    def test_retries_accumulate(self):
        registry, coll = collector()
        coll.record(Event("etl", "success", 1.0, retries=2))
        coll.record(Event("etl", "success", 1.0, retries=3))
        self.assertEqual(coll.retries.get(workflow="etl"), 5)

    def test_seed_creates_series_for_workflows_that_never_ran(self):
        """Without this, `time() - last_success` returns nothing rather than a
        large number, so the alert for 'it never started' never fires. This is
        the most common blind spot in a hand-rolled metrics layer."""
        registry, coll = collector()
        coll.seed(["never-run"])
        out = registry.render()
        self.assertIn('n8n_workflow_last_success_timestamp_seconds{workflow="never-run"} 0', out)
        self.assertIn('n8n_workflow_runs_total{status="error",workflow="never-run"} 0', out)

    def test_seed_does_not_clobber_a_real_last_success(self):
        registry, coll = collector()
        coll.record(Event("etl", "success", 1.0))
        coll.seed(["etl"])
        self.assertEqual(coll.last_success.get(workflow="etl"), 1_772_000_000.0)

    def test_malformed_events_are_counted_not_raised(self):
        """The collector must stay up when the estate is not -- but silent
        drops are how you end up trusting a blind dashboard."""
        registry, coll = collector()
        self.assertIsNone(coll.record_json({"nonsense": True}))
        self.assertEqual(coll.rejected, 1)
        self.assertGreater(coll.rejected_events.get(reason="malformed"), 0)

    def test_finished_at_is_preferred_over_receive_time(self):
        """A batched or delayed report should record when the run finished,
        not when the exporter happened to hear about it."""
        registry, coll = collector()
        coll.record(Event("etl", "success", 1.0, finished_at=1_771_000_000.0))
        self.assertEqual(coll.last_success.get(workflow="etl"), 1_771_000_000.0)


class TestExporterOverHttp(unittest.TestCase):
    """Real sockets. A handler that works when called directly and 500s over
    HTTP is a class of bug only an end-to-end test finds."""

    @classmethod
    def setUpClass(cls):
        registry, coll = build()
        cls.registry, cls.collector = registry, coll
        cls.httpd = serve(0, registry=registry, collector=coll, block=False)
        cls.base = "http://127.0.0.1:" + str(cls.httpd.server_address[1])
        # Capture the exporter's own logs rather than letting them scribble
        # over the test output -- and so the propagation test below can assert
        # on what was actually written, not just on the response body.
        cls.logs = io.StringIO()
        configure(service="n8n-observability", stream=cls.logs)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, path, payload, headers=None):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=body, method="POST",
            headers=dict({"Content-Type": "application/json"}, **(headers or {})),
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read().decode()

    def test_healthz(self):
        status, _, body = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_metrics_content_type_is_the_one_prometheus_expects(self):
        """Serving JSON here fails the scrape with a parse error rather than a
        connection error, which is far harder to notice."""
        status, content_type, _ = self.get("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", content_type)
        self.assertIn("version=0.0.4", content_type)

    def test_posting_an_event_moves_the_metric(self):
        before = self.collector.runs.get(workflow="lead-intake-crm-sync", status="success")
        status, body = self.post(
            "/events",
            {"workflow": "lead-intake-crm-sync", "status": "success", "duration_seconds": 1.25, "items": 3},
        )
        self.assertEqual(status, 202)
        self.assertTrue(body["ok"])
        after = self.collector.runs.get(workflow="lead-intake-crm-sync", status="success")
        self.assertEqual(after, before + 1)

    def test_correlation_id_is_adopted_from_the_header(self):
        """The whole point: the id from the calling workflow appears on this
        hop's logs, so the trace joins across the boundary.

        Asserting on the log line rather than only on the response body is
        what makes this a test of propagation rather than of echoing.
        """
        cid = "cid-00112233445566aa"
        _, body = self.post(
            "/events",
            {"workflow": "etl", "status": "success", "duration_seconds": 1},
            headers={"X-Correlation-Id": cid},
        )
        self.assertEqual(body["correlation_id"], cid)

        written = [
            json.loads(line)
            for line in self.logs.getvalue().strip().split("\n")
            if line.strip().startswith("{")
        ]
        matching = [e for e in written if e.get("correlation_id") == cid]
        self.assertTrue(matching, "the caller's id never reached this hop's logs")
        self.assertEqual(matching[-1]["workflow"], "etl")

    def test_a_forged_correlation_id_is_replaced(self):
        _, body = self.post(
            "/events",
            {"workflow": "etl", "status": "success", "duration_seconds": 1},
            headers={"X-Correlation-Id": 'evil"} injected{x="'},
        )
        self.assertNotIn("injected", body["correlation_id"])

    def test_invalid_json_returns_400_not_500(self):
        req = urllib.request.Request(
            self.base + "/events", data=b"{not json", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_malformed_event_returns_400_so_n8n_stops_retrying(self):
        """A 5xx would make n8n retry a permanently-broken event forever."""
        status, _ = self.post("/events", {"workflow": "etl", "status": "maybe"})
        self.assertEqual(status, 400)

    def test_unknown_paths_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/admin", timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_metrics_output_is_well_formed(self):
        self.post("/events", {"workflow": "etl", "status": "success", "duration_seconds": 3.0, "items": 1})
        _, _, body = self.get("/metrics")
        self.assertIn("# TYPE n8n_workflow_duration_seconds histogram", body)
        self.assertIn('n8n_workflow_duration_seconds_bucket{le="+Inf",workflow="etl"}', body)
        self.assertTrue(body.endswith("\n"))
        for line in body.strip().split("\n"):
            self.assertTrue(line.startswith("#") or " " in line, "malformed line: " + line)

    def test_demo_endpoint_produces_scrapeable_output(self):
        status, _, body = self.get("/demo")
        self.assertEqual(status, 200)
        self.assertGreater(json.loads(body)["recorded"], 0)
        _, _, metrics = self.get("/metrics")
        self.assertIn("n8n_workflow_runs_total", metrics)


if __name__ == "__main__":
    unittest.main()
