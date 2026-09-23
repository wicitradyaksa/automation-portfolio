"""Correlation ids that survive a workflow boundary.

The problem this solves
-----------------------
Projects 4, 5 and 6 in this portfolio call each other's webhooks. A creative
brief enters the Generative Creative Factory, produces an asset, that asset is
rendered by the Media Render Farm, and the DCO Engine later pauses it and
queues a replacement. That is four workflow executions, in three n8n
workflows, possibly hours apart.

When the DCO Engine pauses a creative that should never have shipped, the
question is "what happened to brief 4412" -- and the honest answer with
per-workflow execution ids is "open four tabs and match timestamps by eye".

A correlation id fixes that, but only if three things hold:

1. It is **generated once**, at the true entry point, and never regenerated.
2. It is **propagated** across every boundary -- HTTP header, queue message,
   database row.
3. It is **attached to every log line and every metric exemplar** without the
   caller having to remember, because the caller will not remember.

Point 3 is why this uses ``contextvars`` rather than passing an id around as
an argument. A ContextVar is per-task and per-thread, so the id set at the top
of a request is visible to code five frames down that has never heard of it.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import secrets
import time

# The header name. W3C ``traceparent`` is the real standard and is what you
# would use if you already had OpenTelemetry; this is the pragmatic version
# for an estate where the hops are n8n webhooks and Bash scripts, which is
# what this portfolio actually has.
HEADER = "X-Correlation-Id"

# Accept only ids we could have produced. An id arriving from outside is
# untrusted input: it lands in log files and metric labels, so an unvalidated
# one is both a log-injection vector and an unbounded-cardinality risk.
#
# \Z, not $. In Python, `$` also matches immediately before a trailing
# newline, so `^...$` would accept "cid-0000000000000000\ninjected log line"
# -- which is precisely the log-injection payload this pattern exists to
# reject. tests/test_correlation_and_logging.py covers that exact string.
VALID = re.compile(r"\A[a-z0-9]{1,12}-[0-9a-f]{16}\Z")

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


def new_id(prefix: str = "cid") -> str:
    """``<prefix>-<16 hex>``. 64 bits of randomness.

    Not a UUID4 because this ends up in log lines a human reads and compares
    by eye, and 36 characters of hyphenated hex is materially worse at that
    job than 20. 64 bits is ample: collision risk is negligible at any volume
    this estate will see, and an id only has to be unique among the ones alive
    at the same time.
    """
    if not re.fullmatch(r"[a-z0-9]{1,12}", prefix):
        raise ValueError("prefix must be 1-12 lowercase alphanumeric characters")
    return prefix + "-" + secrets.token_hex(8)


def is_valid(value: str | None) -> bool:
    return bool(value) and bool(VALID.match(value))


def current() -> str | None:
    return _current.get()


def require() -> str:
    """Current id, or a fresh one. Never returns None.

    Log formatting must never be the thing that raises.
    """
    return _current.get() or new_id()


def adopt(incoming: str | None, *, prefix: str = "cid") -> str:
    """Take the caller's id if it is well-formed, otherwise start a new one.

    This is the function every entry point calls. Rejecting a malformed id
    rather than passing it through is the point: an id from an inbound webhook
    is attacker-controlled, and it is about to be written into logs and used
    as a metric label.
    """
    if is_valid(incoming):
        return incoming
    return new_id(prefix)


@contextlib.contextmanager
def context(correlation_id: str | None = None, *, prefix: str = "cid"):
    """Bind an id for the duration of a block.

    ``contextvars`` tokens are reset in a finally, so a raised exception
    cannot leave a stale id bound for whatever the thread does next -- which
    is how you get one request's id on another request's logs, and there is no
    worse failure mode in an observability layer than lying.
    """
    correlation_id = adopt(correlation_id, prefix=prefix)
    token = _current.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _current.reset(token)


def headers(extra: dict | None = None) -> dict:
    """Outbound headers carrying the current id. Every hop uses this."""
    result = dict(extra or {})
    result[HEADER] = require()
    return result


def from_headers(incoming: dict) -> str:
    """Extract from an inbound request. Case-insensitive, because WSGI, n8n
    and curl all disagree about header casing."""
    lowered = {k.lower(): v for k, v in (incoming or {}).items()}
    return adopt(lowered.get(HEADER.lower()))


def from_environ(env: dict | None = None) -> str:
    """For the Bash hop.

    ``vps_healthcheck.sh`` and ``render_variant.sh`` in projects 4 and 7 are
    shell scripts invoked over SSH. They cannot carry a ContextVar, so the id
    travels as an environment variable and is picked back up here.
    """
    env = os.environ if env is None else env
    return adopt(env.get("CORRELATION_ID"))


class Timer:
    """Wall-clock duration for a span of work.

    Monotonic, not ``time.time()``. A clock adjustment mid-render should not
    produce a negative duration in a histogram, and NTP stepping the clock is
    exactly the kind of thing that happens on a VPS under load.
    """

    def __init__(self):
        self.started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started
