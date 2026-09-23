"""Cursor walking, and the two ways a cursor loop hangs a worker forever."""

import json
import random
import unittest

from integration_kit.errors import PaginationError
from integration_kit.http import Client, Response, RetryPolicy
from integration_kit.pagination import CursorPaginator
from tests.fakes import FakeTransport, canned_helpdesk_pages


def paginator(transport, **kwargs):
    client = Client(
        transport=transport,
        policy=RetryPolicy(base_delay=0.0, jitter=False),
        sleeper=lambda s: None,
        rng=random.Random(3),
    )
    defaults = dict(
        client=client,
        url="https://helpdesk.example.com/api/v1/requesters",
        headers={"X-Api-Key": "k"},
        extract=lambda page: page.get("results", []),
        next_cursor=lambda page: (page.get("meta") or {}).get("next_cursor"),
    )
    defaults.update(kwargs)
    return CursorPaginator(**defaults)


class TestCursorWalk(unittest.TestCase):
    def test_walks_all_pages(self):
        t = FakeTransport()
        t.route_pages("https://helpdesk.example.com/api/v1/requesters", canned_helpdesk_pages())
        items = list(paginator(t))
        self.assertEqual([i["requester_id"] for i in items], [9001, 9002])

    def test_cursor_and_page_size_go_into_the_query_string(self):
        t = FakeTransport()
        t.route_pages("https://helpdesk.example.com/api/v1/requesters", canned_helpdesk_pages())
        list(paginator(t, page_size=25, size_param="per_page"))
        self.assertIn("per_page=25", t.calls[0].url)
        self.assertNotIn("cursor=", t.calls[0].url, "first page must not send a cursor")
        self.assertIn("cursor=page-1", t.calls[1].url)

    def test_existing_query_params_are_preserved(self):
        t = FakeTransport()
        t.route_pages("https://helpdesk.example.com/api/v1/requesters", canned_helpdesk_pages())
        list(paginator(t, url="https://helpdesk.example.com/api/v1/requesters?updated_since=2026-01-01"))
        self.assertIn("updated_since=2026-01-01", t.calls[1].url)
        self.assertIn("cursor=page-1", t.calls[1].url)

    def test_repeated_cursor_raises_instead_of_looping(self):
        """Real APIs do this. Without the guard the job never finishes and the
        only symptom is a worker that looks busy."""
        t = FakeTransport()
        t.script(
            Response(200, {}, json.dumps({"results": [{"requester_id": 1}], "meta": {"next_cursor": "c1"}}).encode()),
            Response(200, {}, json.dumps({"results": [{"requester_id": 2}], "meta": {"next_cursor": "c1"}}).encode()),
        )
        with self.assertRaises(PaginationError) as ctx:
            list(paginator(t))
        self.assertIn("already used", str(ctx.exception))

    def test_empty_page_with_a_next_cursor_raises(self):
        t = FakeTransport()
        t.script(
            Response(200, {}, json.dumps({"results": [], "meta": {"next_cursor": "c1"}}).encode()),
        )
        with self.assertRaises(PaginationError) as ctx:
            list(paginator(t))
        self.assertIn("empty page", str(ctx.exception))

    def test_max_pages_is_a_hard_stop(self):
        t = FakeTransport()
        t.script(
            *[
                Response(
                    200,
                    {},
                    json.dumps({"results": [{"requester_id": i}], "meta": {"next_cursor": "c" + str(i)}}).encode(),
                )
                for i in range(10)
            ]
        )
        with self.assertRaises(PaginationError) as ctx:
            list(paginator(t, max_pages=3))
        self.assertIn("max_pages=3", str(ctx.exception))

    def test_single_page_no_cursor_terminates(self):
        t = FakeTransport()
        t.script(Response(200, {}, json.dumps({"results": [{"requester_id": 1}], "meta": {}}).encode()))
        self.assertEqual(len(list(paginator(t))), 1)

    def test_it_is_lazy(self):
        """A generator, not a list. Page 2 must not be fetched until page 1's
        items have been consumed -- that is what keeps a 400k-row pull off the
        heap."""
        t = FakeTransport()
        t.route_pages("https://helpdesk.example.com/api/v1/requesters", canned_helpdesk_pages())
        walker = iter(paginator(t))
        next(walker)
        self.assertEqual(len(t.calls), 1)

    def test_rate_limit_mid_walk_is_retried_not_fatal(self):
        t = FakeTransport()
        t.route_pages(
            "https://helpdesk.example.com/api/v1/requesters",
            canned_helpdesk_pages(),
            rate_limit_first=True,
        )
        items = list(paginator(t))
        self.assertEqual(len(items), 2)
        self.assertEqual(len(t.calls), 3, "one 429 + two successful pages")


if __name__ == "__main__":
    unittest.main()
