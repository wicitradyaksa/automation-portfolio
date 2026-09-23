"""The thing that ties the three connectors together, plus a runnable demo.

``py -m integration_kit.sync --demo`` runs the whole pipeline against the
in-repo fake servers: OAuth refresh, a rate-limited paginated pull, a signed
webhook, normalisation, and the merge. No network, no credentials, no
accounts to create -- which is the only reason a reviewer will actually run it.
"""

from __future__ import annotations

import argparse
import json
import sys

from .schema import merge_all


def collect(sources) -> list:
    """Pull from every source, normalise, merge on contact_id.

    Sources are passed in as iterables so a failing source can be swapped for
    an empty list without the merge logic caring.
    """
    contacts = []
    for source in sources:
        contacts.extend(source)
    return merge_all(contacts)


def _demo() -> int:
    # Imported here, not at module scope: the demo harness is test-support
    # code and should not be a production import.
    import time

    from .connectors import BillingWebhookConnector, CrmConnector, HelpdeskConnector
    from .http import Client, RetryPolicy
    from .oauth import MemoryTokenStore, Token, TokenManager
    from .webhooks import IdempotencyStore, WebhookReceiver, sign

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from tests.fakes import FakeTransport, canned_billing_event, canned_crm_pages, canned_helpdesk_pages

    slept: list[float] = []
    transport = FakeTransport()
    transport.route_pages("https://crm.example.com/v2/contacts", canned_crm_pages())
    transport.route_pages(
        "https://helpdesk.example.com/api/v1/requesters",
        canned_helpdesk_pages(),
        # First call gets rate-limited so the demo actually exercises backoff.
        rate_limit_first=True,
    )
    transport.route_token("https://oauth.example.com/token")

    client = Client(
        transport=transport,
        policy=RetryPolicy(max_attempts=4, base_delay=0.01, jitter=False),
        sleeper=slept.append,
    )

    store = MemoryTokenStore(
        Token(access_token="expired", refresh_token="rt_1", expires_at=time.time() - 10)
    )
    tokens = TokenManager(
        client=client,
        token_url="https://oauth.example.com/token",
        client_id="demo",
        client_secret="demo-secret",
        store=store,
    )

    crm = CrmConnector(client=client, tokens=tokens)
    helpdesk = HelpdeskConnector(client=client, api_key="hd_demo_key")

    secret = "whsec_demo"
    payload = json.dumps(canned_billing_event()).encode()
    header = sign(payload, secret, int(time.time()))
    billing = BillingWebhookConnector(
        receiver=WebhookReceiver(secrets=[secret], store=IdempotencyStore())
    )
    first = billing.handle(payload, header)
    duplicate = billing.handle(payload, header)  # same delivery, second time

    merged = collect([crm.fetch_contacts(), helpdesk.fetch_requesters(), [first] if first else []])

    print("=== Integration Kit demo ===")
    print("OAuth refreshes performed : " + str(tokens.refresh_calls) + " (token was expired at start)")
    print("Token endpoint calls      : " + str(transport.token_calls))
    print("Backoff sleeps honoured   : " + str([round(s, 3) for s in slept]))
    print("Webhook first delivery    : " + (first.email if first else "None"))
    print("Webhook duplicate replay  : " + ("None (deduplicated)" if duplicate is None else "LEAKED"))
    print("Contacts after merge      : " + str(len(merged)))
    print()
    for contact in merged:
        print(json.dumps(contact.to_dict(), indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Integration Kit sync")
    parser.add_argument("--demo", action="store_true", help="run against in-repo fakes, no network")
    args = parser.parse_args(argv)
    if args.demo:
        return _demo()
    parser.error("only --demo is wired up in this repo; real credentials are yours to add")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
