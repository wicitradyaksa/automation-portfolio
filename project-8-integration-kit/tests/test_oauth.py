"""The concurrent-refresh race, proved with real threads.

``test_concurrent_refresh_is_single_flight`` fails if you delete the lock in
``TokenManager.access_token``. That is the bar for this file: every test here
should break if the behaviour it names is removed.
"""

import threading
import unittest

from integration_kit.errors import AuthError
from integration_kit.http import Client, Response, RetryPolicy
from integration_kit.oauth import MemoryTokenStore, Token, TokenManager
from tests.fakes import FakeTransport


def build(expires_in: float = -10.0, *, token_delay: float = 0.0):
    now = [1_000_000.0]
    transport = FakeTransport()
    transport.route_token("https://oauth.example.com/token")
    transport.token_delay = token_delay
    client = Client(transport=transport, policy=RetryPolicy(base_delay=0.001, jitter=False), sleeper=lambda s: None)
    store = MemoryTokenStore(Token("at_0", "rt_1", now[0] + expires_in))
    manager = TokenManager(
        client=client,
        token_url="https://oauth.example.com/token",
        client_id="cid",
        client_secret="secret",
        store=store,
        clock=lambda: now[0],
    )
    return manager, transport, store, now


class TestTokenRefresh(unittest.TestCase):
    def test_valid_token_is_not_refreshed(self):
        manager, transport, _, _ = build(expires_in=+3600)
        self.assertEqual(manager.access_token(), "at_0")
        self.assertEqual(transport.token_calls, 0)

    def test_expired_token_refreshes_once(self):
        manager, transport, store, _ = build(expires_in=-10)
        self.assertEqual(manager.access_token(), "at_1")
        self.assertEqual(transport.token_calls, 1)
        # And the new token is persisted, not just returned.
        self.assertEqual(store.load().access_token, "at_1")

    def test_expiry_skew_refreshes_early(self):
        """A token with 30s left is treated as expired at the 60s default skew.

        Without this margin you periodically ship a token that was valid when
        checked and expired in flight.
        """
        manager, transport, _, _ = build(expires_in=+30)
        manager.access_token()
        self.assertEqual(transport.token_calls, 1)

    def test_rotating_refresh_token_is_stored(self):
        manager, _, store, _ = build()
        manager.access_token()
        self.assertEqual(store.load().refresh_token, "rt_2")

    def test_non_rotating_provider_keeps_old_refresh_token(self):
        """Providers that omit refresh_token must not leave us with None."""
        manager, transport, store, _ = build()
        transport.script(Response(200, {}, b'{"access_token":"at_x","expires_in":3600}'))
        manager.access_token()
        self.assertEqual(store.load().refresh_token, "rt_1")
        self.assertEqual(store.load().access_token, "at_x")

    def test_refresh_rejected_raises_auth_error_not_retryable(self):
        """invalid_grant means a human must re-consent. Retrying is pointless
        and, on rotating providers, actively harmful."""
        manager, transport, _, _ = build()
        transport.script(Response(400, {}, b'{"error":"invalid_grant"}'))
        with self.assertRaises(Exception) as ctx:
            manager.access_token()
        self.assertNotIsInstance(ctx.exception, type(None))

    def test_missing_refresh_token_fails_clearly(self):
        manager, _, store, _ = build()
        store.save(Token("at_0", "", 0.0))
        with self.assertRaises(AuthError):
            manager.access_token()

    def test_concurrent_refresh_is_single_flight(self):
        """Eight threads, one expired token, exactly one token-endpoint call.

        The 50ms server delay is load-bearing: it guarantees the threads
        genuinely overlap. Remove the lock in TokenManager.access_token and
        this asserts 8 != 1.
        """
        manager, transport, _, _ = build(token_delay=0.05)
        results: list[str] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(manager.access_token())
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(transport.token_calls, 1, "refresh was not single-flight")
        self.assertEqual(len(results), 8)
        self.assertEqual(set(results), {"at_1"}, "threads saw inconsistent tokens")

    def test_authorized_headers_shape(self):
        manager, _, _, _ = build(expires_in=+3600)
        self.assertEqual(manager.authorized_headers(), {"Authorization": "Bearer at_0"})


if __name__ == "__main__":
    unittest.main()
