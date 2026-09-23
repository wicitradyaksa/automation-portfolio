"""Signature verification, replay window, idempotency -- including the races."""

import json
import threading
import time
import unittest

from integration_kit.errors import ReplayError, SignatureError
from integration_kit.webhooks import DEFAULT_TOLERANCE, IdempotencyStore, WebhookReceiver, sign, verify

SECRET = "whsec_test"
NOW = 1_772_000_000


def payload(event_id="evt_1"):
    return json.dumps({"id": event_id, "type": "customer.created"}).encode()


class TestSignature(unittest.TestCase):
    def test_valid_signature_returns_timestamp(self):
        body = payload()
        self.assertEqual(verify(body, sign(body, SECRET, NOW), SECRET, now=NOW), NOW)

    def test_wrong_secret_rejected(self):
        body = payload()
        with self.assertRaises(SignatureError):
            verify(body, sign(body, "whsec_other", NOW), SECRET, now=NOW)

    def test_tampered_payload_rejected(self):
        """The classic: attacker keeps the header, edits the body."""
        header = sign(payload(), SECRET, NOW)
        with self.assertRaises(SignatureError):
            verify(b'{"id":"evt_1","type":"customer.deleted"}', header, SECRET, now=NOW)

    def test_tampered_timestamp_rejected(self):
        """Because the timestamp is *inside* the signed string, moving it to
        dodge the replay window invalidates the signature."""
        header = sign(payload(), SECRET, NOW - 10_000)
        forged = header.replace("t=" + str(NOW - 10_000), "t=" + str(NOW))
        with self.assertRaises(SignatureError):
            verify(payload(), forged, SECRET, now=NOW)

    def test_secret_rotation_accepts_both(self):
        body = payload()
        old_header = sign(body, "whsec_old", NOW)
        new_header = sign(body, "whsec_new", NOW)
        for header in (old_header, new_header):
            self.assertEqual(verify(body, header, ["whsec_old", "whsec_new"], now=NOW), NOW)

    def test_multiple_v1_values_in_one_header(self):
        body = payload()
        good = sign(body, SECRET, NOW).split("v1=")[1]
        header = "t=" + str(NOW) + ",v1=deadbeef,v1=" + good
        self.assertEqual(verify(body, header, SECRET, now=NOW), NOW)

    def test_malformed_headers_rejected(self):
        body = payload()
        for header in ("", "garbage", "t=abc,v1=xx", "v1=" + "0" * 64, "t=" + str(NOW)):
            with self.subTest(header=header):
                with self.assertRaises(SignatureError):
                    verify(body, header, SECRET, now=NOW)


class TestReplayWindow(unittest.TestCase):
    def test_old_but_validly_signed_request_rejected(self):
        """A six-month-old capture verifies cryptographically. The window is
        the only thing that stops it."""
        body = payload()
        header = sign(body, SECRET, NOW - 200_000)
        with self.assertRaises(ReplayError):
            verify(body, header, SECRET, now=NOW)

    def test_future_timestamp_rejected(self):
        body = payload()
        header = sign(body, SECRET, NOW + int(DEFAULT_TOLERANCE) + 60)
        with self.assertRaises(ReplayError):
            verify(body, header, SECRET, now=NOW)

    def test_edges_of_the_window(self):
        body = payload()
        inside = sign(body, SECRET, NOW - int(DEFAULT_TOLERANCE) + 1)
        outside = sign(body, SECRET, NOW - int(DEFAULT_TOLERANCE) - 1)
        self.assertTrue(verify(body, inside, SECRET, now=NOW))
        with self.assertRaises(ReplayError):
            verify(body, outside, SECRET, now=NOW)


class TestIdempotency(unittest.TestCase):
    def test_claim_succeeds_once(self):
        store = IdempotencyStore()
        self.assertTrue(store.claim("evt_1"))
        self.assertFalse(store.claim("evt_1"))

    def test_claim_is_atomic_under_threads(self):
        """Twenty threads claiming the same key: exactly one wins.

        This is the test that catches a 'seen() then add()' implementation.
        """
        store = IdempotencyStore()
        wins: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait(timeout=5)
            won = store.claim("evt_race")
            with lock:
                wins.append(won)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(sum(wins), 1, "more than one claimant won the race")

    def test_ttl_expiry_allows_reclaim(self):
        clock = [1000.0]
        store = IdempotencyStore(ttl=60.0, clock=lambda: clock[0])
        self.assertTrue(store.claim("evt_1"))
        clock[0] += 61
        self.assertTrue(store.claim("evt_1"), "key should have expired out of the store")

    def test_expired_keys_are_evicted_not_accumulated(self):
        clock = [1000.0]
        store = IdempotencyStore(ttl=10.0, clock=lambda: clock[0])
        for i in range(50):
            store.claim("evt_" + str(i))
        self.assertEqual(len(store), 50)
        clock[0] += 11
        self.assertEqual(len(store), 0)


class TestReceiver(unittest.TestCase):
    def receiver(self):
        return WebhookReceiver(secrets=[SECRET], clock=lambda: NOW)

    def test_first_delivery_processed_duplicate_dropped(self):
        rec = self.receiver()
        body = payload("evt_42")
        header = sign(body, SECRET, NOW)
        self.assertIsNotNone(rec.handle(body, header))
        self.assertIsNone(rec.handle(body, header), "duplicate delivery was processed twice")

    def test_different_events_both_processed(self):
        rec = self.receiver()
        for event_id in ("evt_1", "evt_2"):
            body = payload(event_id)
            self.assertIsNotNone(rec.handle(body, sign(body, SECRET, NOW)))

    def test_event_without_id_is_refused(self):
        rec = self.receiver()
        body = json.dumps({"type": "customer.created"}).encode()
        with self.assertRaises(SignatureError):
            rec.handle(body, sign(body, SECRET, NOW))


class TestSignHelperUsesRealClock(unittest.TestCase):
    def test_sign_then_verify_with_wall_clock(self):
        body = payload()
        now = int(time.time())
        self.assertEqual(verify(body, sign(body, SECRET, now), SECRET), now)


if __name__ == "__main__":
    unittest.main()
