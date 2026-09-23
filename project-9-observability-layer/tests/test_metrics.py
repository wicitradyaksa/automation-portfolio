"""Exposition format correctness.

Every assertion here corresponds to a rule in the Prometheus text format spec.
Getting one wrong produces a metrics endpoint that either fails to scrape with
a confusing parse error, or -- worse -- scrapes successfully and returns
numbers that are quietly wrong.
"""

import math
import threading
import unittest

from obs.metrics import (
    DEFAULT_BUCKETS,
    MAX_SERIES_PER_METRIC,
    Registry,
    escape_label_value,
    format_value,
    render_labels,
)


class TestCounter(unittest.TestCase):
    def test_increments_and_renders(self):
        reg = Registry()
        runs = reg.counter("jobs_total", "Jobs run.", ["workflow", "status"])
        runs.inc(workflow="etl", status="success")
        runs.inc(2, workflow="etl", status="success")
        out = reg.render()
        self.assertIn("# HELP jobs_total Jobs run.", out)
        self.assertIn("# TYPE jobs_total counter", out)
        self.assertIn('jobs_total{status="success",workflow="etl"} 3', out)

    def test_counters_cannot_decrease(self):
        """rate() assumes monotonicity; a decrement is read as a counter reset
        and produces a spike that is entirely fictional."""
        reg = Registry()
        counter = reg.counter("c_total", "x", ["a"])
        with self.assertRaises(ValueError):
            counter.inc(-1, a="x")

    def test_label_mismatch_is_loud(self):
        """Emitting the same metric with different labels in two code paths
        creates a second series that looks like the first and silently breaks
        every query that sums across them."""
        reg = Registry()
        counter = reg.counter("c_total", "x", ["a", "b"])
        with self.assertRaises(ValueError):
            counter.inc(a="1")
        with self.assertRaises(ValueError):
            counter.inc(a="1", b="2", c="3")

    def test_re_registering_the_same_shape_is_idempotent(self):
        reg = Registry()
        first = reg.counter("c_total", "x", ["a"])
        second = reg.counter("c_total", "x", ["a"])
        self.assertIs(first, second)

    def test_re_registering_a_different_shape_raises(self):
        reg = Registry()
        reg.counter("c_total", "x", ["a"])
        with self.assertRaises(ValueError):
            reg.gauge("c_total", "x", ["a"])
        with self.assertRaises(ValueError):
            reg.counter("c_total", "x", ["a", "b"])

    def test_cardinality_ceiling(self):
        """An unbounded label -- an execution id, a correlation id -- creates
        one series per run and eventually kills the Prometheus instance.
        Refusing loudly beats dying quietly."""
        reg = Registry()
        counter = reg.counter("c_total", "x", ["id"])
        with self.assertRaises(ValueError) as ctx:
            for i in range(MAX_SERIES_PER_METRIC + 5):
                counter.inc(id=str(i))
        self.assertIn("ids belong in logs", str(ctx.exception))

    def test_concurrent_increments_do_not_lose_counts(self):
        reg = Registry()
        counter = reg.counter("c_total", "x", ["a"])
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait(timeout=5)
            for _ in range(500):
                counter.inc(a="x")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        # CPython's GIL makes dict get/set atomic enough for this pattern; the
        # test exists so that a future refactor to a non-atomic read-modify-
        # write is caught rather than assumed safe.
        self.assertEqual(counter.get(a="x"), 4000)


class TestGauge(unittest.TestCase):
    def test_set_inc_dec(self):
        reg = Registry()
        gauge = reg.gauge("queue_depth", "Depth.", ["queue"])
        gauge.set(10, queue="render")
        gauge.dec(3, queue="render")
        self.assertEqual(gauge.get(queue="render"), 7)
        self.assertIn('queue_depth{queue="render"} 7', reg.render())

    def test_gauges_may_go_negative(self):
        reg = Registry()
        gauge = reg.gauge("g", "x", [])
        gauge.set(-5)
        self.assertIn("g -5", reg.render())


class TestHistogram(unittest.TestCase):
    def setUp(self):
        self.reg = Registry()
        self.hist = self.reg.histogram("d_seconds", "Duration.", ["workflow"], buckets=(1.0, 5.0, 10.0))
        for value in (0.5, 2.0, 7.0, 30.0):
            self.hist.observe(value, workflow="etl")

    def test_buckets_are_cumulative(self):
        """An observation falls in its bucket AND every bucket above it.
        Non-cumulative buckets make histogram_quantile return nonsense that
        still looks like a number."""
        out = self.reg.render()
        self.assertIn('d_seconds_bucket{le="1",workflow="etl"} 1', out)
        self.assertIn('d_seconds_bucket{le="5",workflow="etl"} 2', out)
        self.assertIn('d_seconds_bucket{le="10",workflow="etl"} 3', out)

    def test_plus_inf_is_present_and_equals_count(self):
        """A histogram without +Inf is silently unusable in PromQL."""
        out = self.reg.render()
        self.assertIn('d_seconds_bucket{le="+Inf",workflow="etl"} 4', out)
        self.assertIn('d_seconds_count{workflow="etl"} 4', out)

    def test_sum_and_count_are_emitted(self):
        out = self.reg.render()
        self.assertIn('d_seconds_sum{workflow="etl"} 39.5', out)
        self.assertEqual(self.hist.count(workflow="etl"), 4)
        self.assertEqual(self.hist.sum(workflow="etl"), 39.5)

    def test_bucket_boundary_is_inclusive(self):
        """le means 'less than or equal'. Off-by-one here shifts every
        quantile by one bucket."""
        reg = Registry()
        hist = reg.histogram("h", "x", [], buckets=(1.0, 2.0))
        hist.observe(1.0)
        self.assertIn('h_bucket{le="1"} 1', reg.render())

    def test_unsorted_buckets_are_rejected(self):
        reg = Registry()
        with self.assertRaises(ValueError):
            reg.histogram("h", "x", [], buckets=(5.0, 1.0))

    def test_duplicate_buckets_are_rejected(self):
        reg = Registry()
        with self.assertRaises(ValueError):
            reg.histogram("h", "x", [], buckets=(1.0, 1.0, 5.0))

    def test_default_buckets_cover_this_estate(self):
        """Defaults topping out at 10s would put every render in +Inf, which
        tells you nothing about the workload this actually monitors."""
        self.assertGreaterEqual(max(DEFAULT_BUCKETS), 900.0)
        self.assertLessEqual(min(DEFAULT_BUCKETS), 0.1)


class TestEscaping(unittest.TestCase):
    def test_backslash_is_escaped_before_quote(self):
        """Order matters: escaping the quote first double-escapes the
        backslash and produces output Prometheus refuses to parse."""
        self.assertEqual(escape_label_value('a\\b"c'), 'a\\\\b\\"c')

    def test_newlines_escaped(self):
        self.assertEqual(escape_label_value("a\nb"), "a\\nb")

    def test_labels_render_sorted_and_quoted(self):
        self.assertEqual(render_labels({"b": 2, "a": "x"}), '{a="x",b="2"}')

    def test_no_labels_renders_nothing(self):
        self.assertEqual(render_labels({}), "")

    def test_a_label_value_with_a_quote_survives_round_trip(self):
        reg = Registry()
        counter = reg.counter("c_total", "x", ["name"])
        counter.inc(name='He said "hi"')
        self.assertIn('c_total{name="He said \\"hi\\""} 1', reg.render())


class TestValueFormatting(unittest.TestCase):
    def test_special_floats_use_prometheus_spelling(self):
        """Python prints 'inf' and 'nan'; Prometheus requires '+Inf'/'NaN'."""
        self.assertEqual(format_value(math.inf), "+Inf")
        self.assertEqual(format_value(-math.inf), "-Inf")
        self.assertEqual(format_value(math.nan), "NaN")

    def test_integral_floats_render_without_a_decimal_point(self):
        self.assertEqual(format_value(3.0), "3")
        self.assertEqual(format_value(3.5), "3.5")

    def test_large_timestamps_keep_full_precision(self):
        """A unix timestamp rendered in scientific notation loses seconds,
        and this metric is used for an age comparison."""
        rendered = format_value(1772000000.0)
        self.assertEqual(rendered, "1772000000")
        self.assertNotIn("e", rendered)


class TestNameValidation(unittest.TestCase):
    def test_invalid_names_rejected(self):
        reg = Registry()
        for bad in ("1abc", "has-dash", "has space", "", "a.b"):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    reg.counter(bad, "x", [])


class TestRenderShape(unittest.TestCase):
    def test_help_and_type_precede_every_metric(self):
        reg = Registry()
        reg.counter("a_total", "A.", []).inc()
        reg.gauge("b", "B.", []).set(1)
        lines = reg.render().strip().split("\n")
        self.assertEqual(lines[0], "# HELP a_total A.")
        self.assertEqual(lines[1], "# TYPE a_total counter")

    def test_output_ends_with_a_newline(self):
        """Prometheus rejects a body whose last line is not terminated."""
        reg = Registry()
        reg.counter("a_total", "A.", []).inc()
        self.assertTrue(reg.render().endswith("\n"))

    def test_help_text_newlines_are_flattened(self):
        reg = Registry()
        reg.counter("a_total", "line one\nline two", []).inc()
        out = reg.render()
        self.assertIn("# HELP a_total line one line two", out)
        self.assertEqual(out.count("# HELP"), 1)


if __name__ == "__main__":
    unittest.main()
