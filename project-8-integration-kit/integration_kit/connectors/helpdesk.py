"""Source 3 -- helpdesk over a static API key with cursor pagination.

Auth model: the simplest one, and therefore the one where the difficulty moves
somewhere else entirely -- volume. A static key never expires, so there is no
refresh to get right; instead you are walking tens of thousands of records
through an endpoint that rate-limits, which is why this connector is the one
that leans hardest on the retry policy and the cursor guards.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..http import Client
from ..pagination import CursorPaginator
from ..schema import Contact, normalise_email, normalise_phone, stable_contact_id, to_utc_iso

SOURCE = "helpdesk"


@dataclass
class HelpdeskConnector:
    client: Client
    api_key: str
    base_url: str = "https://helpdesk.example.com/api/v1"

    @property
    def headers(self) -> dict[str, str]:
        # Key in a header, never in the query string. Query strings end up in
        # access logs, in proxy logs, and in Referer headers.
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    def fetch_requesters(self, page_size: int = 100, max_pages: int = 500):
        paginator = CursorPaginator(
            client=self.client,
            url=self.base_url + "/requesters",
            headers=self.headers,
            extract=lambda page: page.get("results", []),
            next_cursor=lambda page: (page.get("meta") or {}).get("next_cursor"),
            cursor_param="cursor",
            page_size=page_size,
            size_param="per_page",
            max_pages=max_pages,
        )
        for record in paginator:
            yield self.to_contact(record)

    @staticmethod
    def _company(record: dict) -> str | None:
        """The helpdesk sends company as an object, a bare string, or as an
        object containing an empty string. All three mean "we do not know" in
        at least one case, and an empty string downstream is worse than a
        null -- it renders as a blank cell that looks like real data."""
        value = record.get("company")
        if isinstance(value, dict):
            value = value.get("name")
        return (value or "").strip() or None

    @staticmethod
    def to_contact(record: dict) -> Contact:
        email = normalise_email(record.get("requester_email"))
        source_id = str(record.get("requester_id") or "")
        first = (record.get("first_name") or "").strip()
        last = (record.get("last_name") or "").strip()
        full_name = (first + " " + last).strip() or None
        return Contact(
            contact_id=stable_contact_id(email, SOURCE, source_id),
            email=email,
            full_name=full_name,
            phone=normalise_phone(record.get("mobile") or record.get("phone")),
            company=HelpdeskConnector._company(record),
            source=SOURCE,
            source_id=source_id,
            created_at=to_utc_iso(record.get("created_at")),
            updated_at=to_utc_iso(record.get("updated_at")),
            tags=tuple(sorted(record.get("labels", []))),
            raw=record,
        )
