"""Inbound webhook verification: signature, replay window, idempotency.

Three separate defences, and skipping any one of them leaves a real hole:

1. **Signature** proves the payload came from someone holding the shared
   secret. Without it, anyone who learns your webhook URL can post whatever
   they like at it, and webhook URLs leak constantly -- they end up in browser
   history, in Slack, in a screenshot in a support ticket.

2. **Replay window** proves the payload is *recent*. A signature stays valid
   forever, so a captured-and-replayed request from six months ago verifies
   perfectly. Signing ``timestamp.payload`` rather than ``payload`` alone is
   what makes the timestamp tamper-proof, and therefore what makes the window
   mean anything.

3. **Idempotency** proves you have not already processed it. Providers retry
   on any non-2xx, including the 2xx you failed to return because your own
   process died *after* doing the work. Stripe, GitHub and Shopify all deliver
   at-least-once, never exactly-once. If a duplicate delivery charges a
   customer twice, that is your bug, not theirs.

The signature scheme implemented here is the Stripe one, because it is the
most widely copied: ``t=<unix>,v1=<hex hmac-sha256 of "t.payload">``.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .errors import ReplayError, SignatureError

DEFAULT_TOLERANCE = 300.0  # +/- 5 minutes, the Stripe default


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    """Build the header value a sender would send. Used by tests and by the
    ``--replay`` fixture tooling; a receiver never calls it."""
    signed = str(timestamp).encode() + b"." + payload
    mac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return "t=" + str(timestamp) + ",v1=" + mac


def _parse_header(header: str) -> tuple[int, list[str]]:
    """Returns (timestamp, [candidate signatures]).

    Multiple ``v1=`` values are legal and are how secret rotation works: during
    a rollover the sender signs with both the old and new secret. A receiver
    that reads only the first one breaks every rotation.
    """
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise SignatureError("malformed timestamp in signature header") from exc
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not signatures:
        raise SignatureError("signature header missing t= or v1=")
    return timestamp, signatures


def verify(
    payload: bytes,
    header: str,
    secrets: str | list[str],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    now: float | None = None,
) -> int:
    """Verify signature and freshness. Returns the signed timestamp.

    ``secrets`` accepts a list so you can accept both sides of a rotation.

    Order matters: the signature is checked *before* the timestamp, because
    the timestamp is only trustworthy once you know it was signed.
    """
    if isinstance(secrets, str):
        secrets = [secrets]

    timestamp, candidates = _parse_header(header)
    signed = str(timestamp).encode() + b"." + payload

    matched = False
    for secret in secrets:
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        for candidate in candidates:
            # compare_digest, never ==. A plain string compare short-circuits
            # on the first differing byte, and that timing difference is enough
            # to recover a valid signature byte by byte over enough requests.
            if hmac.compare_digest(expected, candidate):
                matched = True
    if not matched:
        raise SignatureError("no candidate signature matched any configured secret")

    now = time.time() if now is None else now
    drift = now - timestamp
    if abs(drift) > tolerance:
        # Negative drift (future timestamp) is rejected too. It means either
        # your clock is behind or someone is forging, and neither is a thing
        # you want to quietly accept.
        raise ReplayError(
            "timestamp outside +/-%.0fs window (drift %.1fs); check NTP on both ends"
            % (tolerance, drift)
        )
    return timestamp


@dataclass
class IdempotencyStore:
    """First-writer-wins set with a TTL.

    :meth:`claim` returns True exactly once per key. It is a single atomic
    check-and-set on purpose -- a separate ``seen()`` then ``add()`` is a race
    that two concurrent deliveries of the same event will both win.

    Production note: this is in-memory, so it dedupes within one process. With
    more than one replica you want the same logic against Redis ``SET NX EX``
    or a Postgres unique index on ``(event_id)``; the shape of the call does
    not change.
    """

    ttl: float = 24 * 3600.0
    clock: Callable[[], float] = time.time
    _seen: dict[str, float] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def claim(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            self._evict(now)
            if key in self._seen:
                return False
            self._seen[key] = now + self.ttl
            return True

    def _evict(self, now: float) -> None:
        expired = [k for k, exp in self._seen.items() if exp <= now]
        for key in expired:
            del self._seen[key]

    def __len__(self) -> int:
        with self._lock:
            self._evict(self.clock())
            return len(self._seen)


@dataclass
class WebhookReceiver:
    """Verify, dedupe, hand off. Returns the parsed event or None if duplicate."""

    secrets: list[str]
    store: IdempotencyStore = field(default_factory=IdempotencyStore)
    tolerance: float = DEFAULT_TOLERANCE
    clock: Callable[[], float] = time.time
    id_field: str = "id"

    def handle(self, payload: bytes, header: str) -> dict | None:
        import json

        verify(payload, header, self.secrets, tolerance=self.tolerance, now=self.clock())
        event = json.loads(payload.decode("utf-8"))
        event_id = event.get(self.id_field)
        if not event_id:
            raise SignatureError("event has no '" + self.id_field + "' to deduplicate on")
        if not self.store.claim(str(event_id)):
            return None  # already processed; respond 200 and do nothing
        return event
