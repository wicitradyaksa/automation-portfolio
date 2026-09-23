"""Transport seam + the retry loop that sits on top of it.

Two ideas here, deliberately kept separate:

* :class:`Transport` is the *only* thing in the package that knows what a
  socket is. Everything else takes a Transport. That is what lets the test
  suite drive real production code paths instead of mocking out the logic
  actually under test.
* :func:`Client.request` is the policy: what is worth retrying, how long to
  wait, and when to give up. It never sleeps by itself -- it calls the
  injected ``sleeper``, so tests assert on the *schedule* without spending the
  wall-clock time.
"""

from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .errors import AuthError, PermanentError, RateLimited, RetryableError

# 408 Request Timeout, 425 Too Early, 429 Too Many Requests, and the 5xx band
# we are willing to call transient.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Only methods with no side effect on the server get retried automatically.
# POST is excluded on purpose: a POST that timed out may well have succeeded,
# and replaying it creates exactly the duplicate an idempotency key exists to
# prevent. Callers who *do* hold an idempotency key pass idempotent=True.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str, default: str | None = None) -> str | None:
        """Case-insensitive header lookup.

        HTTP header names are not case sensitive and half the APIs in the wild
        disagree about the casing, so never index ``headers`` directly.
        """
        lowered = {k.lower(): v for k, v in self.headers.items()}
        return lowered.get(name.lower(), default)


class Transport:
    """Interface. Implementations must NOT raise for non-2xx -- return it."""

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> Response:
        raise NotImplementedError


class UrllibTransport(Transport):
    """The real one. stdlib only, so this project has no install step."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def send(self, method, url, *, headers, body=None):
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Response(resp.status, dict(resp.headers), resp.read())
        except urllib.error.HTTPError as exc:
            # An HTTPError *is* a response. Treating it as one keeps the retry
            # policy in a single place instead of split across two branches.
            return Response(exc.code, dict(exc.headers or {}), exc.read())
        except urllib.error.URLError as exc:
            raise RetryableError("transport error: " + str(exc.reason)) from exc


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """``Retry-After`` is either delta-seconds or an HTTP-date (RFC 9110).

    Real APIs send both, sometimes for the same endpoint: a CDN in front of an
    API sends the date form while the origin sends seconds. A client that only
    handles the integer form silently falls back to its own backoff and walks
    straight back into the limit.
    """
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    now = time.time() if now is None else now
    return max(0.0, parsed.timestamp() - now)


@dataclass
class RetryPolicy:
    """Capped exponential backoff with full jitter.

    Full jitter (``sleep = random(0, cap)``) rather than equal jitter because
    when a shared dependency recovers, every client that queued up during the
    outage retries at the same instant. Spreading uniformly across the window
    is the cheapest way to not be the thundering herd that knocks it back over.
    """

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: bool = True
    # Cap on how long we will obey a server's Retry-After before giving up,
    # rather than parking a worker for an hour because a header said so.
    max_retry_after: float = 120.0

    def delay_for(self, attempt: int, retry_after: float | None, rng: random.Random) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_retry_after)
        cap = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return rng.uniform(0.0, cap) if self.jitter else cap


@dataclass
class Attempt:
    """Recorded for observability, and for tests to assert a schedule on."""

    number: int
    status: int | None
    slept: float
    reason: str


@dataclass
class Client:
    """A Transport, a retry policy, and a record of what actually happened."""

    transport: Transport
    policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleeper: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)
    attempts: list[Attempt] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        idempotent: bool | None = None,
    ) -> Response:
        method = method.upper()
        if idempotent is None:
            idempotent = method in SAFE_METHODS
        headers = dict(headers or {})

        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                resp = self.transport.send(method, url, headers=headers, body=body)
            except RetryableError as exc:
                last_error = exc
                if not idempotent or attempt == self.policy.max_attempts:
                    raise
                delay = self.policy.delay_for(attempt, exc.retry_after, self.rng)
                self.attempts.append(Attempt(attempt, None, delay, str(exc)))
                self.sleeper(delay)
                continue

            if resp.status < 400:
                self.attempts.append(Attempt(attempt, resp.status, 0.0, "ok"))
                return resp

            if resp.status in (401, 403):
                # Never retried here. If the token merely expired, the OAuth
                # layer above catches AuthError, refreshes once, and replays.
                raise AuthError(
                    str(resp.status) + " from " + url,
                    status=resp.status,
                    body=resp.body.decode("utf-8", "replace")[:500],
                )

            if resp.status not in RETRYABLE_STATUS or not idempotent:
                raise PermanentError(
                    str(resp.status) + " from " + url,
                    status=resp.status,
                    body=resp.body.decode("utf-8", "replace")[:500],
                )

            retry_after = parse_retry_after(resp.header("Retry-After"))
            err_cls = RateLimited if resp.status == 429 else RetryableError
            last_error = err_cls(
                str(resp.status) + " from " + url,
                retry_after=retry_after,
                status=resp.status,
            )

            if attempt == self.policy.max_attempts:
                raise last_error

            delay = self.policy.delay_for(attempt, retry_after, self.rng)
            self.attempts.append(
                Attempt(attempt, resp.status, delay, "retry in %.2fs" % delay)
            )
            self.sleeper(delay)

        raise last_error or PermanentError("exhausted retries for " + url)
