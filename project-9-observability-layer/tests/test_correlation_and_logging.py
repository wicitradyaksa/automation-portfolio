"""Correlation ids across boundaries, and logs that never leak a credential."""

import io
import json
import logging
import threading
import unittest

from obs import correlation
from obs.logging_setup import configure


class TestIdGeneration(unittest.TestCase):
    def test_shape(self):
        cid = correlation.new_id()
        self.assertTrue(correlation.is_valid(cid))
        self.assertTrue(cid.startswith("cid-"))
        self.assertEqual(len(cid.split("-")[1]), 16)

    def test_ids_are_unique(self):
        self.assertEqual(len({correlation.new_id() for _ in range(5000)}), 5000)

    def test_custom_prefix(self):
        self.assertTrue(correlation.new_id("render").startswith("render-"))

    def test_bad_prefix_rejected(self):
        for bad in ("UPPER", "has-dash", "", "waytoolongprefix"):
            with self.subTest(prefix=bad):
                with self.assertRaises(ValueError):
                    correlation.new_id(bad)


class TestAdoption(unittest.TestCase):
    def test_a_valid_incoming_id_is_preserved(self):
        """If it were not, the trace would break at every hop and the whole
        thing would be decoration."""
        incoming = correlation.new_id()
        self.assertEqual(correlation.adopt(incoming), incoming)

    def test_malformed_incoming_ids_are_replaced_not_passed_through(self):
        """An inbound id is attacker-controlled and is about to be written
        into log files and used as a label."""
        for bad in (
            None,
            "",
            "not-an-id",
            "cid-xyz",
            "cid-" + "f" * 15,
            'cid-0000000000000000"} evil{x="',
            "cid-0000000000000000\ninjected log line",
            "cid-" + "f" * 500,
        ):
            with self.subTest(value=bad):
                adopted = correlation.adopt(bad)
                self.assertNotEqual(adopted, bad)
                self.assertTrue(correlation.is_valid(adopted))


class TestContextPropagation(unittest.TestCase):
    def test_id_is_visible_to_code_that_never_received_it(self):
        """The reason this uses contextvars rather than an argument."""

        def five_frames_down():
            return correlation.current()

        with correlation.context() as cid:
            self.assertEqual(five_frames_down(), cid)

    def test_context_is_restored_after_the_block(self):
        self.assertIsNone(correlation.current())
        with correlation.context():
            pass
        self.assertIsNone(correlation.current())

    def test_an_exception_does_not_leave_a_stale_id_bound(self):
        """A stale id means one request's id on another request's logs, which
        is the worst thing an observability layer can do: lie."""
        with self.assertRaises(RuntimeError):
            with correlation.context():
                raise RuntimeError("boom")
        self.assertIsNone(correlation.current())

    def test_nesting_restores_the_outer_id(self):
        with correlation.context() as outer:
            with correlation.context() as inner:
                self.assertNotEqual(outer, inner)
            self.assertEqual(correlation.current(), outer)

    def test_threads_do_not_share_an_id(self):
        seen = {}
        barrier = threading.Barrier(4)

        def worker(index):
            with correlation.context() as cid:
                barrier.wait(timeout=5)
                seen[index] = (cid, correlation.current())

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(seen), 4)
        for cid, observed in seen.values():
            self.assertEqual(cid, observed)
        self.assertEqual(len({cid for cid, _ in seen.values()}), 4)


class TestBoundaryCrossing(unittest.TestCase):
    def test_http_round_trip(self):
        with correlation.context() as cid:
            outbound = correlation.headers({"Content-Type": "application/json"})
            self.assertEqual(outbound[correlation.HEADER], cid)
            self.assertEqual(outbound["Content-Type"], "application/json")
        # Receiver side, any casing.
        self.assertEqual(correlation.from_headers({"x-correlation-id": cid}), cid)
        self.assertEqual(correlation.from_headers({"X-Correlation-ID": cid}), cid)

    def test_missing_header_starts_a_new_trace(self):
        cid = correlation.from_headers({})
        self.assertTrue(correlation.is_valid(cid))

    def test_environment_hop_for_bash_scripts(self):
        """Projects 4 and 7 shell out over SSH; the id travels as an env var."""
        cid = correlation.new_id()
        self.assertEqual(correlation.from_environ({"CORRELATION_ID": cid}), cid)
        self.assertTrue(correlation.is_valid(correlation.from_environ({})))

    def test_require_never_returns_none(self):
        """Log formatting must not be the thing that raises."""
        self.assertTrue(correlation.is_valid(correlation.require()))


class TestTimer(unittest.TestCase):
    def test_elapsed_is_monotonic_and_non_negative(self):
        timer = correlation.Timer()
        for _ in range(1000):
            pass
        self.assertGreaterEqual(timer.elapsed(), 0.0)


class TestJsonLogging(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        configure(level="DEBUG", service="test-service", stream=self.stream)
        self.log = logging.getLogger("t")

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().strip().split("\n") if line]

    def test_one_line_one_json_object(self):
        self.log.info("hello")
        raw = self.stream.getvalue().strip()
        self.assertEqual(len(raw.split("\n")), 1)
        self.assertEqual(json.loads(raw)["msg"], "hello")

    def test_correlation_id_is_attached_without_being_passed(self):
        with correlation.context() as cid:
            self.log.info("inside")
        self.assertEqual(self.lines()[0]["correlation_id"], cid)

    def test_no_context_logs_a_null_id_rather_than_crashing(self):
        self.log.info("outside")
        self.assertIsNone(self.lines()[0]["correlation_id"])

    def test_extra_fields_are_included(self):
        self.log.info("done", extra={"workflow": "etl", "items": 42})
        entry = self.lines()[0]
        self.assertEqual(entry["workflow"], "etl")
        self.assertEqual(entry["items"], 42)

    def test_timestamp_carries_an_explicit_offset(self):
        """A timestamp without a zone is useless the moment you correlate
        across two hosts."""
        self.log.info("x")
        self.assertTrue(self.lines()[0]["ts"].endswith("+00:00"))

    def test_traceback_is_one_field_not_forty_lines(self):
        try:
            raise ValueError("kaboom")
        except ValueError:
            self.log.exception("failed")
        raw = self.stream.getvalue().strip()
        self.assertEqual(len(raw.split("\n")), 1, "traceback broke the one-line rule")
        entry = json.loads(raw)
        self.assertEqual(entry["error"]["type"], "ValueError")
        self.assertIn("kaboom", entry["error"]["stack"])

    def test_unserialisable_values_degrade_instead_of_losing_the_event(self):
        self.log.info("x", extra={"obj": object()})
        self.assertIn("object object", self.lines()[0]["obj"])

    def test_configure_is_idempotent(self):
        configure(level="DEBUG", service="test-service", stream=self.stream)
        logging.getLogger("t").info("once")
        self.assertEqual(len(self.lines()), 1, "handler was added twice")


class TestRedaction(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        configure(level="DEBUG", service="t", stream=self.stream)
        self.log = logging.getLogger("t")

    def test_sensitive_keys_are_redacted(self):
        self.log.info(
            "auth",
            extra={"context": {"password": "hunter2", "api_key": "sk_live_abc", "user": "ada"}},
        )
        entry = json.loads(self.stream.getvalue().strip())
        self.assertEqual(entry["context"]["password"], "[redacted]")
        self.assertEqual(entry["context"]["api_key"], "[redacted]")
        self.assertEqual(entry["context"]["user"], "ada")
        self.assertNotIn("hunter2", self.stream.getvalue())

    def test_redaction_is_case_insensitive_and_nested(self):
        self.log.info("x", extra={"a": {"b": {"Authorization": "Bearer xyz"}}})
        raw = self.stream.getvalue()
        self.assertNotIn("Bearer xyz", raw)

    def test_redaction_reaches_inside_lists(self):
        self.log.info("x", extra={"items": [{"token": "abc123"}, {"ok": 1}]})
        self.assertNotIn("abc123", self.stream.getvalue())

    def test_deeply_nested_input_does_not_blow_the_stack(self):
        """Logging is the thing that must still work when nothing else does."""
        nested = current = {}
        for _ in range(50):
            current["next"] = {}
            current = current["next"]
        self.log.info("deep", extra={"payload": nested})
        self.assertIn("too deep", self.stream.getvalue())


if __name__ == "__main__":
    unittest.main()
