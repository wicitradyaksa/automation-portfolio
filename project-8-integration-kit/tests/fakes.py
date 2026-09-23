"""Fake transport + canned payloads.

The point of a seam here rather than ``unittest.mock.patch``: patching
``urllib`` would test that we called urllib, which is not interesting. A fake
Transport lets the real retry loop, the real pagination walker and the real
token manager run exactly as they do in production, and lets a test assert on
the *sequence of requests they chose to make*.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

from integration_kit.http import Response, Transport


@dataclass
class Recorded:
    method: str
    url: str
    headers: dict
    body: bytes | None


class FakeTransport(Transport):
    """Routes by URL path, records every call, scripts failures per route."""

    def __init__(self):
        self.calls: list[Recorded] = []
        self._pages: dict[str, list[dict]] = {}
        self._rate_limit_first: dict[str, bool] = {}
        self._hits: dict[str, int] = {}
        self._token_url: str | None = None
        self.token_calls = 0
        self.token_delay: float = 0.0
        self.token_serial: int = 0
        self._lock = threading.Lock()
        self.scripted: list[Response] = []

    # ---- routing setup -------------------------------------------------
    def route_pages(self, url: str, pages: list[dict], *, rate_limit_first: bool = False):
        path = self._path(url)
        self._pages[path] = pages
        self._rate_limit_first[path] = rate_limit_first
        self._hits[path] = 0

    def route_token(self, url: str):
        self._token_url = self._path(url)

    def script(self, *responses: Response):
        """Queue exact responses, consumed in order before any routing."""
        self.scripted.extend(responses)

    @staticmethod
    def _path(url: str) -> str:
        import urllib.parse

        return urllib.parse.urlsplit(url).path

    # ---- Transport -----------------------------------------------------
    def send(self, method, url, *, headers, body=None):
        self.calls.append(Recorded(method, url, dict(headers), body))
        if self.scripted:
            return self.scripted.pop(0)

        path = self._path(url)

        if self._token_url and path == self._token_url:
            return self._token_response()

        if path in self._pages:
            return self._page_response(path, url)

        return Response(404, {}, json.dumps({"error": "no route for " + path}).encode())

    # ---- behaviours ----------------------------------------------------
    def _token_response(self) -> Response:
        # The delay + lock-free counter is what makes the concurrent-refresh
        # test meaningful: without a delay, threads serialise by luck and the
        # test passes even when the single-flight lock is removed.
        if self.token_delay:
            time.sleep(self.token_delay)
        with self._lock:
            self.token_calls += 1
            self.token_serial += 1
            serial = self.token_serial
        return Response(
            200,
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "access_token": "at_" + str(serial),
                    "refresh_token": "rt_" + str(serial + 1),
                    "expires_in": 3600,
                    "scope": "contacts.read",
                }
            ).encode(),
        )

    def _page_response(self, path: str, url: str) -> Response:
        import urllib.parse

        self._hits[path] += 1
        if self._rate_limit_first.get(path) and self._hits[path] == 1:
            return Response(
                429,
                {"Retry-After": "2", "Content-Type": "application/json"},
                json.dumps({"error": "rate_limited"}).encode(),
            )

        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        cursor = query.get("cursor") or query.get("pageToken")
        pages = self._pages[path]
        index = 0
        if cursor is not None:
            # Cursors in the fakes are simply "page-<n>".
            index = int(str(cursor).rsplit("-", 1)[-1])
        if index >= len(pages):
            return Response(404, {}, b'{"error":"bad cursor"}')
        return Response(
            200,
            {"Content-Type": "application/json"},
            json.dumps(pages[index]).encode(),
        )


# ---- canned payloads ----------------------------------------------------


def canned_crm_pages() -> list[dict]:
    """Two pages, Google-People-shaped, second page ends the walk."""
    return [
        {
            "contacts": [
                {
                    "resourceName": "people/c1",
                    "primaryEmail": "  Ada.Lovelace@Example.com ",
                    "names": [{"displayName": "Ada Lovelace"}],
                    "phoneNumbers": [{"value": "+61 2 9000 1111"}],
                    "organizations": [{"name": "Analytical Engines Pty Ltd"}],
                    "createTime": "2025-01-04T09:15:00Z",
                    "updateTime": "2026-03-11T22:01:00Z",
                    "memberships": ["customers"],
                }
            ],
            "nextPageToken": "page-1",
        },
        {
            "contacts": [
                {
                    "resourceName": "people/c2",
                    "primaryEmail": "grace@example.com",
                    "names": [{"displayName": "Grace Hopper"}],
                    "phoneNumbers": [{"value": "0400 123 456"}],
                    "organizations": [{"name": "Compiler Co"}],
                    "createTime": "2025-06-02T00:00:00Z",
                    "updateTime": "2026-02-01T10:00:00Z",
                    "memberships": ["customers", "vip"],
                }
            ]
        },
    ]


def canned_helpdesk_pages() -> list[dict]:
    """Two pages, cursor lives under ``meta.next_cursor``.

    Ada appears here too, with a different casing and a missing company, so the
    merge has something real to resolve.
    """
    return [
        {
            "results": [
                {
                    "requester_id": 9001,
                    "requester_email": "ADA.LOVELACE@example.com",
                    "first_name": "Ada",
                    "last_name": "",
                    "mobile": "0400 999 000",
                    "company": {"name": ""},
                    "created_at": 1735689600,
                    "updated_at": "2026-04-02T08:30:00Z",
                    "labels": ["priority"],
                }
            ],
            "meta": {"next_cursor": "page-1"},
        },
        {
            "results": [
                {
                    "requester_id": 9002,
                    "requester_email": "not-an-email",
                    "first_name": "Anon",
                    "last_name": "Requester",
                    "phone": "",
                    "created_at": 1740000000,
                    "updated_at": 1740000000,
                    "labels": [],
                }
            ],
            "meta": {"next_cursor": None},
        },
    ]


def canned_billing_event() -> dict:
    return {
        "id": "evt_1",
        "type": "customer.created",
        "created": 1772000000,
        "data": {
            "object": {
                "id": "cus_1",
                "email": "grace@example.com",
                "name": "Grace B. Hopper",
                "phone": "+61 3 8000 2222",
                "description": "Compiler Co",
                "currency": "aud",
                "created": 1771000000,
                "address": {"line1": "1 Navy Way"},
            }
        },
    }
