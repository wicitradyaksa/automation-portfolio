"""Smoke-test a live Lead Intake workflow with dummy leads.

    python smoke_test.py                                   # production URL on localhost
    python smoke_test.py --url http://localhost:5678/webhook-test/lead-intake
    python smoke_test.py --url https://n8n.example.com/webhook/lead-intake --email-domain yourdomain.com

Sends dummy submissions that cover every branch of the workflow and checks the HTTP
response of each. Slack, Sheets and email can't be seen from here, so the script
prints what to look for afterwards.

Every run uses a unique tag in the email addresses, so a rerun creates new leads
instead of hitting the duplicate branch by accident. Use --email-domain with a
domain whose mail you receive if you want to see the auto-replies; the default
example.com never delivers.
"""
import argparse
import json
import time
import urllib.error
import urllib.request


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        return None, str(e.reason)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5678/webhook/lead-intake")
    ap.add_argument("--email-domain", default="example.com")
    a = ap.parse_args()

    tag = time.strftime("%m%d%H%M%S")
    d = a.email_domain
    cases = [
        ("new lead, camelCase fields", 200,
         {"firstName": "Maya", "lastName": "Santoso", "email": f"maya.santoso+{tag}@{d}",
          "company": "Kopi Nusantara", "source": "landing_page_q4"}),
        ("new lead, snake_case + messy email", 200,
         {"first_name": " Rizal ", "last_name": "Hakim", "email": f"  RIZAL.HAKIM+{tag}@{d.upper()} ",
          "company": "Bali Surf Co", "source": "partner_referral"}),
        ("new lead, name only (no company/source)", 200,
         {"firstName": "Chen", "email": f"chen.wei+{tag}@{d}"}),
        ("duplicate of Maya: touch #2", 200,
         {"firstName": "Maya", "lastName": "Santoso", "email": f"maya.santoso+{tag}@{d}",
          "company": "Kopi Nusantara", "source": "webinar_signup"}),
        ("duplicate of Maya: touch #3", 200,
         {"firstName": "Maya", "email": f"MAYA.SANTOSO+{tag}@{d}", "source": "pricing_page"}),
        ("invalid email", 400,
         {"firstName": "Bob", "lastName": "Spam", "email": "not-an-email", "source": "contact_form"}),
        ("missing email", 400,
         {"firstName": "Nadia", "company": "Ghost Ltd"}),
    ]

    print(f"Target: {a.url}\nRun tag: {tag}\n")
    fails = 0
    for name, want, payload in cases:
        code, body = post(a.url, payload)
        ok = code == want
        fails += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: HTTP {code} (want {want})  {body[:160]}")
        if code is None or code == 404:
            print("\n  n8n unreachable or webhook not registered. Activate the workflow, or click "
                  "'Listen for test event' and use the /webhook-test/ URL (it accepts ONE request per click).")
            return 1
        time.sleep(1.5)  # keep the duplicate checks ordered after the append lands

    print(f"""
{len(cases) - fails}/{len(cases)} HTTP checks passed. Now look at your accounts yourself:

  Google Sheet, tab 'Leads'
    3 new rows tagged +{tag}: maya.santoso, rizal.hakim (lower-cased, trimmed), chen.wei
    maya.santoso row: touchCount = 3 (one row, not three)
  Google Sheet, tab 'Rejected Submissions'
    2 new rows: 'not-an-email' and the one with no email

  Slack #sales-alerts, 5 messages:
    New lead: *Maya Santoso* (Kopi Nusantara) ...
    New lead: *Rizal Hakim* (Bali Surf Co) ...
    New lead: *Chen* ...
    Returning lead (touch #2): *Maya Santoso* ...
    Returning lead (touch #3): *Maya* ...
  Slack #eng-alerts: nothing (no failures expected)

  Email: 5 auto-replies to the addresses above ({'not delivered; example.com is a dummy domain' if d == 'example.com' else 'check that inbox'})
""")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
