"""Source 2 -- billing events over HMAC-signed webhooks (Stripe-shaped).

Auth model: no credential goes out; a shared secret proves what came in. This
is the inverse of the other two connectors and the one people get wrong most
often, because the failure is silent -- an unverified endpoint works perfectly
right up until someone posts to it.

The verification itself lives in :mod:`integration_kit.webhooks`. This file is
only the mapping plus the event-type routing.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schema import Contact, normalise_email, normalise_phone, stable_contact_id, to_utc_iso
from ..webhooks import WebhookReceiver

SOURCE = "billing"

# Event types we act on. Anything else is verified, deduped, acknowledged with
# a 200, and dropped. Returning a non-2xx for an event you simply do not care
# about makes the provider retry it, then eventually disable your endpoint.
HANDLED = frozenset({"customer.created", "customer.updated"})


@dataclass
class BillingWebhookConnector:
    receiver: WebhookReceiver

    def handle(self, payload: bytes, signature_header: str) -> Contact | None:
        """Returns a Contact for events we map, None for duplicates and for
        event types we deliberately ignore. Raises on bad signature or replay.
        """
        event = self.receiver.handle(payload, signature_header)
        if event is None:
            return None  # duplicate delivery
        if event.get("type") not in HANDLED:
            return None
        return self.to_contact(event)

    @staticmethod
    def to_contact(event: dict) -> Contact:
        obj = (event.get("data") or {}).get("object") or {}
        email = normalise_email(obj.get("email"))
        source_id = str(obj.get("id") or event.get("id") or "")
        address = obj.get("address") or {}
        return Contact(
            contact_id=stable_contact_id(email, SOURCE, source_id),
            email=email,
            full_name=(obj.get("name") or "").strip() or None,
            phone=normalise_phone(obj.get("phone")),
            company=(address.get("line1") and obj.get("description")) or obj.get("description") or None,
            source=SOURCE,
            source_id=source_id,
            created_at=to_utc_iso(obj.get("created")),
            updated_at=to_utc_iso(event.get("created")),
            tags=("billing:" + (obj.get("currency") or "unknown"),),
            raw=event,
        )
