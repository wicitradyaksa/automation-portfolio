"""Source 1 -- CRM over OAuth2 (Google-shaped: Bearer token, rotating refresh).

Auth model: OAuth2 refresh-token grant. The interesting part is not building
the Authorization header, it is what happens when the token expires
*mid-flight*: the 401 arrives on a request you already sent. This connector
catches that once, forces a refresh, and replays -- and only once, so a
genuinely revoked grant fails fast instead of looping.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import AuthError
from ..http import Client
from ..oauth import TokenManager
from ..pagination import CursorPaginator
from ..schema import Contact, normalise_email, normalise_phone, stable_contact_id, to_utc_iso

SOURCE = "crm"


@dataclass
class CrmConnector:
    client: Client
    tokens: TokenManager
    base_url: str = "https://crm.example.com/v2"

    def fetch_contacts(self, page_size: int = 100):
        """Yield normalised Contacts. Handles the expired-mid-flight 401 once."""
        try:
            yield from self._walk(page_size)
        except AuthError:
            # Force the next access_token() call to refresh by expiring the
            # cached copy, then replay exactly once.
            self._invalidate()
            yield from self._walk(page_size)

    def _invalidate(self) -> None:
        token = self.tokens.store.load()
        if token is not None:
            from dataclasses import replace

            self.tokens.store.save(replace(token, expires_at=0.0))

    def _walk(self, page_size: int):
        paginator = CursorPaginator(
            client=self.client,
            url=self.base_url + "/contacts",
            headers=self.tokens.authorized_headers(),
            extract=lambda page: page.get("contacts", []),
            next_cursor=lambda page: page.get("nextPageToken"),
            cursor_param="pageToken",
            page_size=page_size,
            size_param="pageSize",
        )
        for record in paginator:
            yield self.to_contact(record)

    @staticmethod
    def to_contact(record: dict) -> Contact:
        email = normalise_email(record.get("primaryEmail"))
        source_id = str(record.get("resourceName") or record.get("id") or "")
        names = record.get("names") or [{}]
        display = names[0].get("displayName") if names else None
        phones = record.get("phoneNumbers") or [{}]
        orgs = record.get("organizations") or [{}]
        return Contact(
            contact_id=stable_contact_id(email, SOURCE, source_id),
            email=email,
            full_name=(display or "").strip() or None,
            phone=normalise_phone(phones[0].get("value") if phones else None),
            company=(orgs[0].get("name") if orgs else None) or None,
            source=SOURCE,
            source_id=source_id,
            created_at=to_utc_iso(record.get("createTime")),
            updated_at=to_utc_iso(record.get("updateTime")),
            tags=tuple(sorted(record.get("memberships", []))),
            raw=record,
        )
