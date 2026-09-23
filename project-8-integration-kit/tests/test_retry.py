"""Retry policy: what gets retried, how long we wait, when we stop.

The sleeper is injected, so these tests assert the exact backoff *schedule*
in microseconds of wall time rather than actually waiting for it.
"""

import email.utils
import random
import unittest

from integration_kit.errors import AuthError, PermanentError, RateLimited, RetryableError
from integration_kit.http import Client, Response, RetryPolicy, parse_retry_after
from tests.fakes import FakeTransport


def client(max_attempts=4, jitter=False):
    transport = FakeTransport()
    slept: list[float] = []
    c = Client(
        transport=transport,
        policy=RetryPolicy(max_attempts=max_attempts, base_delay=1.0, max_delay=30.0, jitter=jitter),
        sleeper=slept.append,
        rng=random.Random(7),
    )
    return c, transport, slept


class TestRetryAfterParsing(unittest.TestCase):
    def test_delta_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)

    def test_http_date(self):
        """The date form is what a CDN in front of an API sends. A client that
        ignores it walks straight back into the rate limit."""
        now = 1_772_000_000.0
        future = email.utils.formatdate(now + 90, usegmt=True)
        self.assertAlmostEqual(parse_retry_after(future, now=now), 90.0, delta=1.0)

    def test_past_date_clamps_to_zero(self):
        now = 1_772_000_000.0
        past = email.utils.formatdate(now - 500, usegmt=True)
        self.assertEqual(parse_retry_after(past, now=now), 0.0)

    def test_missing_and_garbage(self):
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after(""))
        self.assertIsNone(parse_retry_after("soon-ish"))


class TestRetryBehaviour(unittest.TestCase):
    def test_success_does_not_sleep(self):
        c, t, slept = client()
        t.script(Response(200, {}, b"{}"))
        self.assertEqual(c.request("GET", "https://x/y").status, 200)
        self.assertEqual(slept, [])

    def test_429_honours_retry_after_over_backoff(self):
        """Backoff would be 1s. The server said 7. We wait 7."""
        c, t, slept = client()
        t.script(
            Response(429, {"Retry-After": "7"}, b"{}"),
            Response(200, {}, b"{}"),
        )
        self.assertEqual(c.request("GET", "https://x/y").status, 200)
        self.assertEqual(slept, [7.0])

    def test_retry_after_is_capped(self):
        """A server asking for an hour should not park a worker for an hour."""
        c, t, slept = client()
        c.policy.max_retry_after = 60.0
        t.script(Response(503, {"Retry-After": "3600"}, b"{}"), Response(200, {}, b"{}"))
        c.request("GET", "https://x/y")
        self.assertEqual(slept, [60.0])

    def test_exponential_backoff_without_retry_after(self):
        c, t, slept = client()
        t.script(
            Response(500, {}, b"{}"),
            Response(500, {}, b"{}"),
            Response(500, {}, b"{}"),
            Response(200, {}, b"{}"),
        )
        c.request("GET", "https://x/y")
        self.assertEqual(slept, [1.0, 2.0, 4.0])

    def test_backoff_is_capped_at_max_delay(self):
        c, t, slept = client(max_attempts=8)
        c.policy.max_delay = 5.0
        t.script(*([Response(503, {}, b"{}")] * 7), Response(200, {}, b"{}"))
        c.request("GET", "https://x/y")
        self.assertEqual(slept, [1.0, 2.0, 4.0, 5.0, 5.0, 5.0, 5.0])

    def test_full_jitter_stays_within_the_cap(self):
        """Jitter must never exceed the cap it is jittering within."""
        policy = RetryPolicy(base_delay=1.0, max_delay=30.0, jitter=True)
        rng = random.Random(1)
        for attempt in range(1, 8):
            cap = min(30.0, 1.0 * 2 ** (attempt - 1))
            for _ in range(200):
                self.assertTrue(0.0 <= policy.delay_for(attempt, None, rng) <= cap)

    def test_exhausting_attempts_raises_rate_limited(self):
        c, t, slept = client(max_attempts=3)
        t.script(*([Response(429, {"Retry-After": "1"}, b"{}")] * 3))
        with self.assertRaises(RateLimited):
            c.request("GET", "https://x/y")
        self.assertEqual(len(slept), 2, "should sleep between attempts, not after the last")

    def test_401_is_never_retried(self):
        c, t, slept = client()
        t.script(Response(401, {}, b'{"error":"bad token"}'))
        with self.assertRaises(AuthError):
            c.request("GET", "https://x/y")
        self.assertEqual(slept, [])
        self.assertEqual(len(t.calls), 1)

    def test_400_is_never_retried(self):
        c, t, _ = client()
        t.script(Response(400, {}, b'{"error":"bad request"}'))
        with self.assertRaises(PermanentError):
            c.request("GET", "https://x/y")
        self.assertEqual(len(t.calls), 1)

    def test_post_is_not_retried_by_default(self):
        """A timed-out POST may already have succeeded server-side."""
        c, t, _ = client()
        t.script(Response(503, {}, b"{}"))
        with self.assertRaises(PermanentError):
            c.request("POST", "https://x/y", body=b"{}")
        self.assertEqual(len(t.calls), 1)

    def test_post_is_retried_when_caller_asserts_idempotency(self):
        c, t, slept = client()
        t.script(Response(503, {}, b"{}"), Response(200, {}, b"{}"))
        resp = c.request("POST", "https://x/y", body=b"{}", idempotent=True)
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(t.calls), 2)

    def test_transport_level_error_is_retried(self):
        class Flaky(FakeTransport):
            def __init__(self):
                super().__init__()
                self.n = 0

            def send(self, method, url, *, headers, body=None):
                self.n += 1
                if self.n == 1:
                    raise RetryableError("connection reset")
                return Response(200, {}, b"{}")

        slept: list[float] = []
        c = Client(transport=Flaky(), policy=RetryPolicy(base_delay=1.0, jitter=False), sleeper=slept.append)
        self.assertEqual(c.request("GET", "https://x/y").status, 200)
        self.assertEqual(slept, [1.0])

    def test_attempts_are_recorded_for_observability(self):
        c, t, _ = client()
        t.script(Response(500, {}, b"{}"), Response(200, {}, b"{}"))
        c.request("GET", "https://x/y")
        self.assertEqual([a.status for a in c.attempts], [500, 200])


class TestHeaderLookup(unittest.TestCase):
    def test_header_lookup_is_case_insensitive(self):
        resp = Response(429, {"retry-AFTER": "5"}, b"")
        self.assertEqual(resp.header("Retry-After"), "5")


if __name__ == "__main__":
    unittest.main()
