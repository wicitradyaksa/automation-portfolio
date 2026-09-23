"""Cursor pagination that cannot spin forever.

Offset pagination (``?page=3``) is wrong for a changing dataset: if a row is
inserted while you are walking, every later page shifts and you either skip a
record or read one twice. Cursor pagination fixes that, and introduces two new
ways to hang a worker:

* The server returns the cursor you just sent. Now you loop forever, fetching
  the same page, and the only symptom is a job that never finishes.
* The server always returns a cursor, even on the last page. Same outcome.

Both are real behaviours from real APIs. So this walker carries a hard page
cap, refuses a repeated cursor, and surfaces both as errors rather than as a
worker that appears busy.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping

from .errors import PaginationError
from .http import Client


@dataclass
class CursorPaginator:
    """Walk a cursor-paginated collection, yielding items one at a time.

    ``extract`` pulls the item list out of a page; ``next_cursor`` pulls the
    continuation token (or None). They are injected because no two APIs agree
    on the envelope -- ``data``/``next``, ``items``/``cursor``,
    ``results``/``meta.next_cursor``.
    """

    client: Client
    url: str
    headers: Mapping[str, str]
    extract: Callable[[dict], list]
    next_cursor: Callable[[dict], str | None]
    cursor_param: str = "cursor"
    max_pages: int = 1000
    page_size: int | None = None
    size_param: str = "limit"

    def __iter__(self) -> Iterator[dict]:
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for page_number in range(1, self.max_pages + 1):
            resp = self.client.request("GET", self._url_for(cursor), headers=dict(self.headers))
            page = resp.json()
            items = self.extract(page)
            for item in items:
                yield item

            cursor = self.next_cursor(page)
            if not cursor:
                return

            if cursor in seen_cursors:
                raise PaginationError(
                    "server returned a cursor already used (page "
                    + str(page_number)
                    + "); refusing to loop"
                )
            seen_cursors.add(cursor)

            if not items:
                # A cursor pointing at nothing, repeatedly, is the other shape
                # of the same bug. One empty page is legal; continuing past it
                # forever is not, so stop on the first.
                raise PaginationError(
                    "empty page " + str(page_number) + " still advertised a next cursor"
                )

        raise PaginationError(
            "exceeded max_pages=" + str(self.max_pages) + "; raise the cap deliberately or narrow the query"
        )

    def _url_for(self, cursor: str | None) -> str:
        parts = urllib.parse.urlsplit(self.url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        if self.page_size is not None:
            query[self.size_param] = str(self.page_size)
        if cursor:
            query[self.cursor_param] = cursor
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
        )
