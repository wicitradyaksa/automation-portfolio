"""A minimal Prometheus registry and exposition-format renderer.

Why not ``prometheus_client``? For a portfolio piece, writing the exposition
format by hand is the point: it demonstrates that I know what a histogram
actually is (cumulative buckets, ``_sum``, ``_count``, a mandatory ``+Inf``),
rather than that I can call a library. In production, use the library.

The format is documented at
https://prometheus.io/docs/instrumenting/exposition_formats/ and the rules
that matter are enforced in ``tests/test_metrics.py``.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

# Buckets in seconds. Chosen for THIS estate rather than copied from a default:
# an n8n webhook workflow finishes in well under a second, a render is tens of
# seconds to minutes, and a nightly ETL is minutes. Defaults topping out at 10s
# would put every render in +Inf, which tells you nothing.
DEFAULT_BUCKETS = (0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0, 900.0)

# Cardinality guard. Every distinct label combination is a separate time
# series stored forever. Putting an execution id or a correlation id in a
# label is the classic way to kill a Prometheus instance -- one series per
# run, unbounded. So ids go in LOGS; labels stay low-cardinality, and this
# registry refuses to grow past a ceiling rather than dying quietly.
MAX_SERIES_PER_METRIC = 2000

VALID_NAME = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:"


def _check_name(name: str) -> str:
    if not name or name[0].isdigit():
        raise ValueError("invalid metric name: " + repr(name))
    for char in name:
        if char not in VALID_NAME:
            raise ValueError("invalid character " + repr(char) + " in metric name " + repr(name))
    return name


def escape_label_value(value: str) -> str:
    """Backslash, double quote and newline must be escaped, in that order.

    Escaping the quote before the backslash double-escapes the backslash and
    silently corrupts the output. It is a one-line bug that produces a metrics
    endpoint Prometheus refuses to scrape, with an error that points at the
    wrong line.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_labels(labels: dict) -> str:
    if not labels:
        return ""
    # Sorted so the output is deterministic. Not required by Prometheus, but
    # it makes the golden-file test possible and diffs readable.
    inner = ",".join(
        key + '="' + escape_label_value(str(value)) + '"' for key, value in sorted(labels.items())
    )
    return "{" + inner + "}"


def format_value(value: float) -> str:
    """Prometheus wants ``+Inf``/``-Inf``/``NaN``, not Python's ``inf``/``nan``."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


@dataclass
class _Metric:
    name: str
    help: str
    type: str
    labelnames: tuple
    values: dict = field(default_factory=dict)

    def _key(self, labels: dict) -> tuple:
        missing = set(self.labelnames) - set(labels)
        extra = set(labels) - set(self.labelnames)
        if missing or extra:
            # Loud, not lenient. A metric emitted with the wrong labels in one
            # code path creates a second series that looks like the first and
            # silently breaks every query that sums across them.
            raise ValueError(
                self.name + ": label mismatch (missing=" + str(sorted(missing))
                + ", unexpected=" + str(sorted(extra)) + ")"
            )
        return tuple(str(labels[name]) for name in self.labelnames)

    def _guard(self):
        if len(self.values) >= MAX_SERIES_PER_METRIC:
            raise ValueError(
                self.name + ": refusing to create more than " + str(MAX_SERIES_PER_METRIC)
                + " series. A label is unbounded -- ids belong in logs, not labels."
            )


class Counter(_Metric):
    """Monotonically increasing. Only ever goes up, or resets to 0 on restart.

    ``rate()`` in PromQL handles the reset; that is why a counter must never
    be decremented, and why ``inc`` rejects a negative amount.
    """

    def inc(self, amount: float = 1.0, **labels):
        if amount < 0:
            raise ValueError("counters cannot decrease; use a Gauge")
        key = self._key(labels)
        if key not in self.values:
            self._guard()
        self.values[key] = self.values.get(key, 0.0) + amount

    def get(self, **labels) -> float:
        return self.values.get(self._key(labels), 0.0)


class Gauge(_Metric):
    """A value that goes up and down. Queue depth, last-success timestamp."""

    def set(self, value: float, **labels):
        key = self._key(labels)
        if key not in self.values:
            self._guard()
        self.values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels):
        key = self._key(labels)
        if key not in self.values:
            self._guard()
        self.values[key] = self.values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels):
        self.inc(-amount, **labels)

    def get(self, **labels) -> float:
        return self.values.get(self._key(labels), 0.0)


@dataclass
class Histogram(_Metric):
    buckets: tuple = DEFAULT_BUCKETS

    def __post_init__(self):
        if list(self.buckets) != sorted(self.buckets):
            raise ValueError("histogram buckets must be sorted ascending")
        if len(set(self.buckets)) != len(self.buckets):
            raise ValueError("histogram buckets must be unique")

    def observe(self, value: float, **labels):
        key = self._key(labels)
        if key not in self.values:
            self._guard()
            self.values[key] = {"buckets": [0] * len(self.buckets), "sum": 0.0, "count": 0}
        entry = self.values[key]
        entry["sum"] += value
        entry["count"] += 1
        for index, upper in enumerate(self.buckets):
            # Cumulative: an observation falls into its bucket AND every
            # bucket above it. Getting this wrong makes histogram_quantile
            # return nonsense that still looks like a number.
            if value <= upper:
                entry["buckets"][index] += 1

    def count(self, **labels) -> int:
        entry = self.values.get(self._key(labels))
        return entry["count"] if entry else 0

    def sum(self, **labels) -> float:
        entry = self.values.get(self._key(labels))
        return entry["sum"] if entry else 0.0


class Registry:
    def __init__(self):
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def counter(self, name, help, labelnames=()) -> Counter:
        return self._register(Counter(_check_name(name), help, "counter", tuple(labelnames)))

    def gauge(self, name, help, labelnames=()) -> Gauge:
        return self._register(Gauge(_check_name(name), help, "gauge", tuple(labelnames)))

    def histogram(self, name, help, labelnames=(), buckets=DEFAULT_BUCKETS) -> Histogram:
        return self._register(
            Histogram(_check_name(name), help, "histogram", tuple(labelnames), buckets=tuple(buckets))
        )

    def _register(self, metric):
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                if existing.type != metric.type or existing.labelnames != metric.labelnames:
                    raise ValueError(metric.name + " is already registered with a different shape")
                return existing
            self._metrics[metric.name] = metric
            return metric

    def names(self) -> list[str]:
        return sorted(self._metrics)

    def render(self) -> str:
        """Prometheus text exposition format, version 0.0.4."""
        lines: list[str] = []
        with self._lock:
            metrics = sorted(self._metrics.values(), key=lambda m: m.name)

        for metric in metrics:
            lines.append("# HELP " + metric.name + " " + metric.help.replace("\n", " "))
            lines.append("# TYPE " + metric.name + " " + metric.type)

            if isinstance(metric, Histogram):
                for key, entry in sorted(metric.values.items()):
                    labels = dict(zip(metric.labelnames, key))
                    for index, upper in enumerate(metric.buckets):
                        bucket_labels = dict(labels, le=format_value(upper))
                        lines.append(
                            metric.name + "_bucket" + render_labels(bucket_labels)
                            + " " + str(entry["buckets"][index])
                        )
                    # +Inf is mandatory and must equal _count. A histogram
                    # without it is silently unusable in PromQL.
                    lines.append(
                        metric.name + "_bucket" + render_labels(dict(labels, le="+Inf"))
                        + " " + str(entry["count"])
                    )
                    lines.append(metric.name + "_sum" + render_labels(labels) + " " + format_value(entry["sum"]))
                    lines.append(metric.name + "_count" + render_labels(labels) + " " + str(entry["count"]))
            else:
                for key, value in sorted(metric.values.items()):
                    labels = dict(zip(metric.labelnames, key))
                    lines.append(metric.name + render_labels(labels) + " " + format_value(value))

        return "\n".join(lines) + "\n"
