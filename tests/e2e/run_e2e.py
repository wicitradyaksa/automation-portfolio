"""End-to-end tests: run all seven workflows inside a real n8n and assert on what they did.

    py tests/e2e/run_e2e.py            build, run every scenario, tear down
    py tests/e2e/run_e2e.py --keep     leave the stack running afterwards (n8n on :5679)
    py tests/e2e/run_e2e.py p3 p6      only some workflows

Requires Docker. See tests/e2e/README.md for what is real and what is mocked.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOCK = "http://localhost:8765"
N8N = "http://localhost:5679"
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
IDS = {f"p{i}": f"PortfolioE2E{i:04d}" for i in range(1, 8)}


# ------------------------------------------------------------------ plumbing
def sh(*args, check=True, quiet=True):
    r = subprocess.run(["docker", "compose", *args], cwd=HERE, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    if not quiet:
        print(r.stdout, r.stderr)
    return r


def http(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


def wait_for(fn, timeout=180, every=2, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(every)
    raise TimeoutError(f"timed out waiting for {what}")


def scenario(state):
    http("POST", f"{MOCK}/_scenario", state)


def calls(node=None):
    _, c = http("GET", f"{MOCK}/_calls")
    c = [x for x in c if x["path"] != "/_calls"]
    return [x for x in c if x.get("node") == node] if node else c


def cli_execute(key):
    return sh("exec", "-T", "-e", "N8N_RUNNERS_BROKER_PORT=5690", "n8n", "n8n", "execute", f"--id={IDS[key]}",
              check=False)


def text_of(node):
    return [c["params"].get("text", "") for c in calls(node)]


def columns_of(node):
    return [c["params"].get("columns", {}).get("value", {}) for c in calls(node)]


# ------------------------------------------------------------------ fixtures
def invoice_pdf(lines):
    """A minimal, valid single-page PDF with one text line per entry."""
    stream = "BT /F1 12 Tf 72 740 Td 16 TL\n" + "".join(
        "(%s) Tj T*\n" % l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for l in lines) + "ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1").hex()


def ad_row(variant, ad_id, imp, clicks, spend, budget=None):
    row = {"ad_id": ad_id, "ad_name": f"c_summer__{variant}__feed", "adset_id": f"as_{variant}",
           "campaign_id": "c_summer_sale", "impressions": imp, "clicks": clicks, "spend": spend}
    if budget is not None:
        row["daily_budget"] = budget
    return row


# ------------------------------------------------------------------ scenarios
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"    {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def p1():
    url = f"{N8N}/webhook/lead-intake"
    scenario({"sheets": {"Leads": []}})
    code, body = http("POST", url, {"first_name": "Ada", "lastName": "Lovelace", "email": "  ADA@Example.com ",
                                    "company": "Analytical Engines", "source": "landing_page_q4"})
    check("P1 new lead: 200 + normalized email echoed", code == 200 and body.get("email") == "ada@example.com", f"{code} {body}")
    created = columns_of("Create New CRM Row")
    check("P1 new lead: CRM row created from the submission",
          created and created[0].get("firstName") == "Ada" and created[0].get("touchCount") in (1, "1"), str(created))
    check("P1 new lead: no update on a new lead", not calls("Update Existing Row"))
    check("P1 new lead: Slack says 'New lead' with full name",
          any("New lead: *Ada Lovelace* (Analytical Engines)" in t for t in text_of("Notify Sales (Slack)")), str(text_of("Notify Sales (Slack)")))
    mail = calls("Send Confirmation Email")
    check("P1 new lead: confirmation email to the prospect",
          mail and mail[0]["params"].get("toEmail") == "ada@example.com" and "Ada" in mail[0]["params"].get("subject", ""))

    scenario({"sheets": {"Leads": [{"row_number": 2, "email": "ada@example.com", "touchCount": "3"}]}})
    code, _ = http("POST", url, {"firstName": "Ada", "email": "ada@example.com"})
    upd = columns_of("Update Existing Row")
    check("P1 duplicate: 200 and touchCount 3 -> 4", code == 200 and upd and str(upd[0].get("touchCount")) == "4", str(upd))
    check("P1 duplicate: no second CRM row", not calls("Create New CRM Row"))
    check("P1 duplicate: Slack says 'Returning lead (touch #4)' without empty ()",
          any("Returning lead (touch #4): *Ada* via" in t for t in text_of("Notify Sales (Slack)")), str(text_of("Notify Sales (Slack)")))

    scenario({})
    code, body = http("POST", url, {"firstName": "Bob", "email": "not-an-email"})
    check("P1 invalid: 400 with reason", code == 400 and body.get("reason") == "invalid_email", f"{code} {body}")
    check("P1 invalid: logged to Rejected Submissions", calls("Log Invalid Submission"))
    check("P1 invalid: CRM untouched", not calls("Check For Duplicate") and not calls("Create New CRM Row"))

    scenario({"fail_nodes": ["Check For Duplicate"]})
    code, _ = http("POST", url, {"firstName": "Cy", "email": "cy@example.com"})
    time.sleep(2)
    alerts = text_of("Alert Engineering (Slack)")
    check("P1 Sheets outage: Error Trigger alerts engineering with the error", code >= 500 and alerts and "failed" in alerts[0], f"{code} {alerts}")
    check("P1 Sheets outage: no half-processed lead (no Slack alert to sales)", not calls("Notify Sales (Slack)"))


def p2():
    def run(lines):
        scenario({"invoice_pdf_hex": invoice_pdf(lines)})
        cli_execute("p2")

    run(["Acme Supplies Pty Ltd", "Invoice #INV-1042", "Due Date: 30/09/2026", "Total Due: 1,240.50"])
    q = columns_of("Log To Approval Queue")
    check("P2 over threshold: routed to Pending Approval with parsed fields",
          q and q[0].get("invoiceNumber") == "INV-1042" and float(q[0].get("amount")) == 1240.5 and q[0].get("vendor") == "Acme Supplies Pty Ltd", str(q))
    check("P2 over threshold: manager pinged with invoice number",
          any("INV-1042" in t and "1240.5" in t for t in text_of("Alert Finance Manager")), str(text_of("Alert Finance Manager")))
    check("P2 over threshold: not auto-approved", not calls("Log To Approved Ledger"))

    run(["Bright Paper Co", "Invoice #INV-2001", "Due Date: 15/10/2026", "Total Due: 420.00"])
    check("P2 under threshold: auto-approved to the ledger", calls("Log To Approved Ledger") and not calls("Log To Approval Queue"))

    run(["Mystery Vendor", "Invoice #INV-3001", "Total Due: 99.00"])
    rev = columns_of("Log To Needs Review")
    check("P2 missing due date: sent to Needs Review with the raw text",
          rev and "Mystery Vendor" in (rev[0].get("extractedText") or ""), str(rev)[:300])
    check("P2 missing due date: finance ops alerted, never auto-approved",
          text_of("Alert Finance Ops") and not calls("Log To Approved Ledger") and not calls("Log To Approval Queue"))


def p3():
    # "website" is not a known container, so its restart gets a 404 like a real Docker API would
    scenario({"health": {"api-gateway": [200], "worker-queue": [503, 200], "public-website": [503, 503]},
              "containers": ["api-gateway", "worker-queue"]})
    cli_execute("p3")
    hb = columns_of("Log Heartbeat (OK)")
    check("P3 healthy service: heartbeat logged", any(r.get("service") == "api-gateway" for r in hb), str(hb))
    healed = text_of("Notify Self-Healed")
    check("P3 recovered service: self-heal notice, no page",
          any("worker-queue" in t for t in healed) and not any("worker-queue" in t for t in text_of("Escalate To On-Call")), str(healed))
    esc = text_of("Escalate To On-Call")
    check("P3 still-down service: escalated to on-call", any("public-website" in t for t in esc), str(esc))
    inc = columns_of("Log Incident") + columns_of("Log Self-Heal")
    check("P3 incidents logged as distinct outcomes",
          {("public-website", "escalated"), ("worker-queue", "self_healed")} <= {(r.get("service"), r.get("outcome")) for r in inc}, str(inc))
    restarts = sorted(c["path"] for c in calls() if c["path"].startswith("/docker/"))
    check("P3 each unhealthy container restarted through the Docker API",
          restarts == ["/docker/containers/website/restart", "/docker/containers/worker-queue/restart"], str(restarts))
    check("P3 failed docker restart did not stop the workflow (re-check still ran)",
          sum(1 for c in calls() if c["path"].startswith("/health/")) == 5)


def wait_mock(node, n=1, timeout=240):
    wait_for(lambda: len(calls(node)) >= n, timeout=timeout, what=f"{n} call(s) to {node}")


def p4():
    url = f"{N8N}/webhook/render-request"
    scenario({})
    code, body = http("POST", url, {"assetId": "hero01", "campaign": "summer", "sourcePath": "/data/masters/hero.mp4",
                                    "formats": ["9:16", "1:1"], "hooks": ["Try it free"]})
    check("P4 ack: 202 with the queued count", code == 202 and body.get("queued") == 2, f"{code} {body}")
    wait_mock("Post Render Summary")
    uploads = [c for c in calls() if c["path"] == "/assets/upload"]
    check("P4 both variants rendered, verified and uploaded as real files",
          len(uploads) == 2 and all(u["has_file"] and u["bytes"] > 10240 for u in uploads), str([(u["bytes"], u["has_file"]) for u in uploads]))
    man = columns_of("Log To Asset Manifest")
    check("P4 manifest rows carry the variant spec, not the upload response",
          len(man) == 2 and all(r.get("variantId", "").startswith("hero01_") and float(r.get("durationSec") or 0) >= 1 for r in man), str(man))
    check("P4 one summary per batch", len(calls("Post Render Summary")) == 1)

    scenario({})
    code, _ = http("POST", url, {"assetId": "ghost", "campaign": "summer", "sourcePath": "/data/masters/missing.mp4",
                                 "formats": ["9:16", "1:1", "16:9"], "hooks": ["A"]})
    wait_mock("Post Render Summary")
    failed = columns_of("Log Failed Render")
    check("P4 missing source: every variant logged as failed, batch not aborted", code == 202 and len(failed) == 3, str(failed))
    check("P4 missing source: nothing uploaded or added to the manifest",
          not [c for c in calls() if c["path"] == "/assets/upload"] and not calls("Log To Asset Manifest"))
    check("P4 missing source: creative ops alerted per variant", len(calls("Alert Creative Ops")) == 3)


def p5():
    brief = {"briefId": "b_001", "campaign": "summer", "subject": "iced coffee can", "style": "editorial",
             "status": "queued", "row_number": 2, "width": "512", "height": "512", "batchSize": "2"}
    scenario({"sheets": {"Creative Briefs": [brief]}, "history_ready_after": 2})
    cli_execute("p5")
    q = [c for c in calls() if c["path"] == "/comfy/prompt"]
    check("P5 ComfyUI graph queued with a KSampler node",
          q and q[0]["json"]["prompt"]["5"]["class_type"] == "KSampler", str(q)[:200])
    polls = [c for c in calls() if c["path"].startswith("/comfy/history/")]
    check("P5 polling loop polled until images landed (2 polls)", len(polls) == 2, str(len(polls)))
    done = columns_of("Mark Brief Rendered")
    check("P5 creatives post-processed by Python and brief marked rendered",
          done and done[0].get("status") == "rendered" and int(done[0].get("assetCount") or 0) >= 8, str(done))
    check("P5 no master video: no hand-off to the render farm",
          not any(c["path"] == "/assets/upload" for c in calls()) and text_of("Post Creative Digest"))

    scenario({"sheets": {"Creative Briefs": [{**brief, "videoSourcePath": "/data/masters/hero.mp4", "hooks": "Try it free"}]},
              "history_ready_after": 2})
    cli_execute("p5")
    wait_mock("Post Render Summary")
    uploads = [c for c in calls() if c["path"] == "/assets/upload"]
    check("P5 -> P4 hand-off: render farm rendered 3 formats for the brief", len(uploads) == 3, str(len(uploads)))

    scenario({"sheets": {"Creative Briefs": [brief]}, "history_ready_after": 999})
    cli_execute("p5")
    failed = columns_of("Mark Brief Failed")
    check("P5 wedged GPU queue: gives up and marks the brief failed_timeout",
          failed and failed[0].get("status") == "failed_timeout" and text_of("Alert GPU Queue Stuck"), str(failed))

    scenario({"sheets": {"Creative Briefs": []}})
    cli_execute("p5")
    check("P5 empty queue: nothing sent to ComfyUI", not [c for c in calls() if c["path"].startswith("/comfy")])


def p6():
    pages = [
        [ad_row("hero_control", "ad_1001", 50000, 1500, 900.0), ad_row("hero_story_b", "ad_1002", 48000, 1600, 950.0),
         ad_row("hero_story_c", "ad_1003", 47000, 1400, 880.0)],
        [ad_row("hero_story_d", "ad_1004", 46000, 1450, 900.0), ad_row("hero_story_e", "ad_1005", 800, 30, 20.0),
         ad_row("hero_story_f", "ad_1006", 50000, 1500, 900.0), ad_row("hero_story_g", "ad_1007", 50000, 1500, 900.0, budget=None)],
    ]
    conv = [{"sub_id": a, "conversions": c, "revenue": r} for a, c, r in [
        ("ad_1001", 75, 1200.0), ("ad_1002", 112, 1900.0), ("ad_1003", 42, 500.0), ("ad_1004", 78, 1250.0),
        ("ad_1005", 1, 15.0), ("ad_1006", 150, 3000.0), ("ad_1007", 150, 3000.0)]]
    # Budgets come from the ad-set endpoint, as on Meta; hero_story_g's ad set has none
    adsets = [{"id": f"as_{v}", "daily_budget": 100} for v in
              ("hero_control", "hero_story_b", "hero_story_c", "hero_story_d", "hero_story_e", "hero_story_f")]
    scenario({"ads_pages": pages, "conversions": conv, "adsets": adsets})
    cli_execute("p6")
    ins = [c for c in calls() if c["path"] == "/ads/insights"]
    check("P6 followed pagination (2 pages)", len(ins) == 2, str(len(ins)))
    learn = columns_of("Log Still Learning")
    check("P6 low-volume variant logged as still learning", any(r.get("variantId") == "hero_story_e" for r in learn), str(learn))
    decisions = {r.get("variantId"): r.get("decision") for r in columns_of("Log Decision")}
    check("P6 decisions: b hold (not significant), c pause (ROAS floor), d hold, f scale, g hold (budget unknown)",
          decisions == {"hero_story_b": "hold", "hero_story_c": "pause", "hero_story_d": "hold",
                        "hero_story_f": "scale", "hero_story_g": "hold"}, str(decisions))
    paused = [c for c in calls() if c["path"] == "/ads/ads/ad_1003"]
    check("P6 losing ad paused through the ads API", paused and paused[0]["json"] == {"status": "PAUSED"}, str(paused))
    scaled = [c for c in calls() if c["path"].startswith("/ads/adsets/")]
    check("P6 winner's budget scaled by the capped +20% step",
          len(scaled) == 1 and scaled[0]["path"] == "/ads/adsets/as_hero_story_f" and scaled[0]["json"] == {"daily_budget": 120}, str(scaled))
    rep = columns_of("Request Replacement Creative")
    check("P6 paused variant queued a replacement brief for Project 5",
          rep and rep[0].get("status") == "queued" and "hero_story_c" in rep[0].get("briefId", ""), str(rep))
    report = text_of("Post Buying Report")
    check("P6 one buying report listing every decision",
          len(report) == 1 and all(v in report[0] for v in ("hero_story_b", "hero_story_c", "hero_story_f")), str(report)[:300])


def p7():
    health = {"host": "vps1", "disk_used_pct": 62, "mem_used_pct": 40, "cert_days_left": 60,
              "unhealthy_containers": [], "failed_units": []}
    backup = {"host": "vps1", "archive": "/backups/v.tar.gz", "bytes": 52428800, "checksum": "abc", "checksum_verified": True}
    dq_ok = [{"rows_today": 1200, "null_keys": 0, "duplicate_keys": 0, "avg_prior_7d": 1100}]

    def run(h=health, b=backup, dq=dq_ok, df="62"):
        scenario({"ssh": {"vps_healthcheck.sh": json.dumps(h), "backup_volumes.sh": json.dumps(b),
                          "etl_load.py": json.dumps({"ok": True, "rows_upserted": 1200}),
                          "docker system prune": "Total reclaimed space: 4.2GB", "df --output": df},
                  "pg": {"rows_today": dq}})
        cli_execute("p7")

    run()
    log = columns_of("Log Nightly Run")
    check("P7 happy path: nightly log carries health, backup and load figures",
          log and str(log[0].get("diskUsedPct")) == "62" and log[0].get("backupArchive") == "/backups/v.tar.gz"
          and str(log[0].get("rowsLoaded")) == "1200", str(log))
    check("P7 happy path: digest posted, nothing quarantined", text_of("Post Nightly Digest") and not calls("Quarantine Bad Load"))

    run(h={**health, "disk_used_pct": 91}, df="92")
    check("P7 disk pressure: reclaim ran, re-check still full, escalated",
          calls("Reclaim Disk Space") and any("92%" in t for t in text_of("Escalate Disk Alert")), str(text_of("Escalate Disk Alert")))

    run(b={**backup, "bytes": 200})
    check("P7 200-byte backup: alert raised and ETL skipped", text_of("Alert Backup Failure") and not calls("Run ETL Load"))

    run(dq=[{"rows_today": 0, "null_keys": 0, "duplicate_keys": 0, "avg_prior_7d": 1100}])
    q = calls("Quarantine Bad Load")
    check("P7 zero-row load: quarantined with the failure reason",
          q and "no_rows_loaded" in json.dumps(q[0]["params"]) and not calls("Log Nightly Run"), str(q)[:300])
    check("P7 zero-row load: ops told dashboards are on yesterday's data",
          any("no_rows_loaded" in t for t in text_of("Alert Data Quality Failure")))

    # n8n does not start error workflows for CLI-launched runs, so this asserts the loud failure itself;
    # the Error Trigger path is exercised through a webhook in P1.
    scenario({"ssh": {"vps_healthcheck.sh": "Traceback (most recent call last): boom"}})
    r = cli_execute("p7")
    out = r.stdout + r.stderr
    check("P7 non-JSON healthcheck: execution fails loudly with the parse error",
          "returned non-JSON" in out and not calls("Backup Volumes") and not calls("Log Nightly Run"), out[-300:])


# ------------------------------------------------------------------ main
def setup():
    print("Building and starting the e2e stack ...")
    sh("down", "-v", check=False)
    (HERE / ".work" / "data" / "masters").mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE / "build_harness.py")], check=True, capture_output=True)
    sh("up", "-d", "--build")
    wait_for(lambda: http("GET", f"{N8N}/healthz")[0] == 200, what="n8n health")
    # /healthz answers before a fresh database has finished migrating; running the CLI
    # during migrations makes both processes apply them and one crashes.
    wait_for(lambda: "Editor is now accessible" in sh("logs", "n8n", check=False).stdout, what="n8n startup")
    sh("exec", "-T", "n8n", "sh", "-c",
       "mkdir -p /data/masters /data/renders /data/creatives && ffmpeg -y -loglevel error "
       "-f lavfi -i testsrc=size=1280x720:rate=25 -f lavfi -i sine=frequency=440:sample_rate=48000 "
       "-t 3 -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac /data/masters/hero.mp4")
    sh("exec", "-T", "n8n", "n8n", "import:workflow", "--separate", "--input=/work/workflows")
    for key in ("p1", "p4"):
        sh("exec", "-T", "-e", "N8N_RUNNERS_BROKER_PORT=5690", "n8n", "n8n", "publish:workflow", f"--id={IDS[key]}")
    sh("restart", "n8n")
    wait_for(lambda: http("POST", f"{N8N}/webhook/lead-intake", {})[0] != 404, what="webhooks to register")


def main(argv):
    keep = "--keep" in argv
    wanted = [a for a in argv if a.startswith("p")] or list(IDS)
    setup()
    for key in wanted:
        print(f"\n== {key.upper()}")
        started = time.time()
        try:
            globals()[key]()
        except Exception as e:  # a crashed scenario is a failure, not a skipped one
            check(f"{key.upper()} scenario crashed", False, repr(e))
        print(f"    ({time.time() - started:.0f}s)")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    if not keep:
        sh("down", "-v", check=False)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
