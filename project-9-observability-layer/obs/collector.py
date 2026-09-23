"""Turns n8n execution events into metrics.

This is the piece that makes the claim "I have error handling and
observability" inspectable rather than asserted. n8n knows when a workflow
started, finished, failed and retried; it just does not expose any of that as
metrics you can alert on. This collector receives those events -- from an
``errorTrigger``, from a final HTTP Request node, or by polling the executions
API -- and maintains the series you would actually page on.

The metric set is chosen around what you need at 3am, not what is easy to
count:

* ``..._runs_total`` and ``..._duration_seconds`` -- rate and latency, the
  two halves of "is it working".
* ``..._last_success_timestamp_seconds`` -- the one that catches the worst
  failure, which is a scheduled workflow that stopped running entirely. A
  failure rate cannot detect that: zero runs means zero failures, and every
  dashboard stays green while nothing happens. Alerting on the *age* of the
  last success is the only way to see it.
* ``..._retries_total`` -- rising retries are the early warning that precedes
  an outage, usually by long enough to act.
* ``..._items_processed_total`` -- because "the workflow succeeded" and "the
  workflow did its work" are different claims (see project 7: a backup that
  exits 0 and contains nothing).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .metrics import Registry

# Only these end a run. An unknown status is counted separately rather than
# silently bucketed as a success -- guessing here is how a dashboard ends up
# lying about the thing it exists to report.
TERMINAL = frozenset({"success", "error", "canceled", "crashed"})


@dataclass
class Event:
    """One n8n execution outcome."""

    workflow: str
    status: str
    duration_seconds: float
    correlation_id: str = ""
    retries: int = 0
    items: int = 0
    node: str = ""
    finished_at: float = 0.0

    @classmethod
    def from_json(cls, payload: dict) -> "Event":
        """Parse a webhook body, tolerating n8n's naming.

        Validation is strict on the things used as metric labels and lenient
        on everything else: a bad label creates a bogus time series forever,
        whereas a missing item count is merely a gap.
        """
        workflow = str(payload.get("workflow") or payload.get("workflowName") or "").strip()
        if not workflow:
            raise ValueError("event has no workflow name")
        if len(workflow) > 120:
            raise ValueError("workflow name is implausibly long; refusing to label with it")

        status = str(payload.get("status") or "").strip().lower()
        if status not in TERMINAL:
            raise ValueError("unknown status " + repr(status) + "; expected one of " + str(sorted(TERMINAL)))

        try:
            duration = float(payload.get("duration_seconds", payload.get("durationSeconds", 0.0)))
        except (TypeError, ValueError):
            raise ValueError("duration_seconds is not a number")
        if duration < 0:
            raise ValueError("negative duration; check for a non-monotonic clock")

        return cls(
            workflow=workflow,
            status=status,
            duration_seconds=duration,
            correlation_id=str(payload.get("correlation_id") or payload.get("correlationId") or ""),
            retries=max(0, int(payload.get("retries", 0) or 0)),
            items=max(0, int(payload.get("items", 0) or 0)),
            node=str(payload.get("node") or "")[:120],
            finished_at=float(payload.get("finished_at") or 0.0),
        )


@dataclass
class Collector:
    registry: Registry
    clock: callable = time.time
    prefix: str = "n8n"
    seen: int = 0
    rejected: int = 0
    _last_success: dict = field(default_factory=dict)

    def __post_init__(self):
        p = self.prefix
        self.runs = self.registry.counter(
            p + "_workflow_runs_total",
            "Total workflow executions by terminal status.",
            ["workflow", "status"],
        )
        self.duration = self.registry.histogram(
            p + "_workflow_duration_seconds",
            "Workflow execution wall-clock duration.",
            ["workflow"],
        )
        self.retries = self.registry.counter(
            p + "_workflow_retries_total",
            "Node-level retries attempted. Rising retries precede an outage.",
            ["workflow"],
        )
        self.items = self.registry.counter(
            p + "_workflow_items_processed_total",
            "Items the workflow actually handled. Succeeding and doing nothing are different.",
            ["workflow"],
        )
        self.last_success = self.registry.gauge(
            p + "_workflow_last_success_timestamp_seconds",
            "Unix time of the last successful run. Alert on its AGE, not its value.",
            ["workflow"],
        )
        self.failing_node = self.registry.counter(
            p + "_workflow_node_failures_total",
            "Failures attributed to a specific node, to skip a step in triage.",
            ["workflow", "node"],
        )
        self.rejected_events = self.registry.counter(
            p + "_collector_rejected_events_total",
            "Events the collector refused to record. Should be flat at zero.",
            ["reason"],
        )

    def record(self, event: Event) -> None:
        self.seen += 1
        self.runs.inc(workflow=event.workflow, status=event.status)
        self.duration.observe(event.duration_seconds, workflow=event.workflow)

        if event.retries:
            self.retries.inc(event.retries, workflow=event.workflow)
        if event.items:
            self.items.inc(event.items, workflow=event.workflow)

        if event.status == "success":
            when = event.finished_at or self.clock()
            self.last_success.set(when, workflow=event.workflow)
            self._last_success[event.workflow] = when
        elif event.node:
            self.failing_node.inc(workflow=event.workflow, node=event.node)

    def record_json(self, payload: dict) -> Event | None:
        """Parse and record, counting rejections instead of raising.

        A malformed event must not take the collector down -- the collector is
        the thing that is supposed to still be up when the estate is not. But
        rejections are themselves a metric, because silently dropping events
        is how you end up trusting a dashboard that has been blind for a week.
        """
        try:
            event = Event.from_json(payload)
        except (ValueError, TypeError) as exc:
            self.rejected += 1
            reason = "bad_status" if "status" in str(exc) else "malformed"
            self.rejected_events.inc(reason=reason)
            return None
        self.record(event)
        return event

    def seed(self, workflows) -> None:
        """Create a zero series for every known workflow at startup.

        Without this, a workflow that has never succeeded has no
        ``last_success`` series at all, and ``time() - last_success`` returns
        nothing rather than a large number -- so the alert that is supposed to
        fire when a workflow stops running does not fire when it never started.
        That is a silent blind spot, and it is the most common mistake in a
        hand-rolled metrics layer.
        """
        for workflow in workflows:
            self.runs.inc(0, workflow=workflow, status="success")
            self.runs.inc(0, workflow=workflow, status="error")
            if workflow not in self._last_success:
                self.last_success.set(0, workflow=workflow)
