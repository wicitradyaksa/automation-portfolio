# Alert runbook

Every alert in `prometheus/alerts.yml` links here, to its own anchor. The link is generated from the alert name, and `tests/test_alerts_and_dashboard.py` fails the build if an alert points at an anchor that does not exist — a dead runbook link delivered at 3am is worse than no link at all.

Each entry answers the four things you need at that hour: **what fired**, **what it probably is**, **what to check first**, and **when to escalate**.

---

## Escalation path

| Severity | Where it goes | Response expectation |
|---|---|---|
| `critical` | Page on-call (PagerDuty / phone) | Acknowledge within 15 minutes |
| `warning` | Slack `#automation-alerts` | Same business day |
| `info` | Slack `#automation-noise` | Look at it when convenient |

**The rule for adding a `critical`:** it must be something a human can act on *now*, and something that is worse if left until morning. If neither is true it is a `warning`. An estate where `critical` means "probably nothing" is an estate with no alerting, regardless of how many rules are configured.

---

## WorkflowStoppedRunning

**Severity:** critical · **Fires after:** 10m · **Threshold:** no success in 90 minutes

### What it means

A workflow has not completed successfully in over 90 minutes. For anything on a schedule, this nearly always means it is **not running at all**, rather than running and failing.

This is the most important alert in the file, and it is the one a naive setup does not have. A workflow that stopped firing produces zero runs, therefore zero failures, therefore a perfectly green error-rate dashboard. Rate-based alerting is structurally incapable of seeing it. Only the *age of the last success* can.

### Check in this order

1. **Is the trigger enabled?** n8n → the workflow → the Active toggle. Someone deactivates one to debug it and forgets. This is the most common cause by a wide margin.
2. **Is n8n running?**
   ```bash
   docker compose ps
   docker compose logs --tail=100 n8n
   ```
3. **Did a credential expire?** Check the workflow's last execution. An OAuth grant that needed re-consent fails at the first node, and if the workflow has no error branch it may not have reported anything.
4. **Is the exporter itself stale?** If several workflows alert at once, suspect the exporter rather than the workflows — see [MetricsEndpointDown](#metricsendpointdown).
5. **Was it genuinely idle?** A workflow triggered by inbound webhooks legitimately goes quiet. If that is this workflow, the threshold is wrong, not the workflow — fix the rule rather than muting it.

### Escalate if

n8n is up, the trigger is enabled, and a manual execution also produces nothing. That points at the data source rather than at the automation.

---

## WorkflowErrorRateHigh

**Severity:** warning · **Fires after:** 15m · **Threshold:** >10% of runs failing

### What it means

More than one run in ten is ending in error over a 15-minute window.

The `clamp_min` in the denominator is deliberate: without it a workflow with zero runs divides by zero, and the alert flaps between firing and "no data" indefinitely.

### Check in this order

1. **Which node?** The `Failures by node` dashboard panel, or:
   ```promql
   topk(5, sum by (workflow, node) (increase(n8n_workflow_node_failures_total[1h])))
   ```
   This skips the whole first step of triage.
2. **Is it one downstream dependency?** Failures concentrated on an HTTP Request node usually mean the far end, not you. Check their status page before you change anything.
3. **Is it rate limiting?** Cross-check [WorkflowRetriesClimbing](#workflowretriesclimbing). Retries rising alongside errors is the signature.
4. **Correlate the logs.** Take a correlation id from a failed run and follow it across every hop:
   ```bash
   docker compose logs --since 30m | grep '"correlation_id":"cid-abc123..."'
   ```
5. **Did anything deploy?** Check the workflow's version history for a change in the last hour.

### Escalate if

The error rate is above 50%, or the workflow writes to a system of record. A half-failing pipeline that writes partial data is worse than one that is fully down, because the damage is quiet.

---

## WorkflowDurationDegraded

**Severity:** warning · **Fires after:** 20m · **Threshold:** p95 > 5 minutes

### What it means

The slowest 5% of runs now take over five minutes.

p95 rather than the mean, because the mean hides the tail and the tail is what times out. Note the query sums buckets *before* `histogram_quantile` — quantile-of-an-average is not average-of-quantiles, and doing it the other way round produces a plausible number that is wrong.

### Check in this order

1. **Is the input larger, or is the work slower?** A doubled item count with a doubled duration is capacity, not a fault:
   ```promql
   rate(n8n_workflow_items_processed_total[30m])
   ```
2. **Is one node responsible?** Open a slow execution and read the per-node timings.
3. **Host resources.** Project 7's healthcheck covers disk, memory and load:
   ```bash
   ./project-7-vps-ops-data-pipeline/scripts/vps_healthcheck.sh | python -m json.tool
   ```
4. **Is the GPU queue backed up?** For `generative-creative-factory`, a wedged ComfyUI queue shows up here first.
5. **Database.** A missing index on a table that grew is the classic slow creep, and it never announces itself.

### Escalate if

p95 is above the workflow's own timeout, because runs are now being killed mid-flight, which is a correctness problem rather than a performance one.

---

## WorkflowRetriesClimbing

**Severity:** info · **Fires after:** 10m · **Threshold:** >0.2 retries/sec

### What it means

Retries are absorbing failures that have not surfaced as errors yet.

This is the earliest signal available, typically 20–30 minutes ahead of the error-rate alert. `info` on purpose: it is a reason to look, not a reason to wake someone. Making it `critical` would be the fastest way to teach the team to ignore the channel.

### Check in this order

1. **Which dependency?** Retries cluster on whichever external call is degrading.
2. **Are you being rate limited?** Check for 429s in the logs; if so, the fix is backoff and batching, not more retries. Honour `Retry-After` when the API sends it; [Project 8](../project-8-integration-kit/integration_kit/http.py) shows the pattern.
3. **Watch it for 30 minutes.** If retries are climbing *and* duration is climbing, you are ahead of an incident. Act now rather than after it becomes an error rate.

### Escalate if

Retries keep climbing for more than an hour. The retry budget is being exhausted and errors are next.

---

## WorkflowSucceedingButDoingNothing

**Severity:** warning · **Fires after:** 2h · **Threshold:** runs > 0 and items == 0

### What it means

The workflow is completing successfully and processing nothing.

This is the portfolio's first rule made measurable: **exit code 0 is not evidence that a job did its work.** An inbox filter that stopped matching, a sheet that was renamed, an API returning an empty page — all produce a workflow that succeeds forever while delivering nothing, and no conventional monitoring notices. Same class of bug as a backup that completes and contains no files.

### Check in this order

1. **Is the source genuinely empty?** Open it. Sometimes there is nothing to do, and that is fine.
2. **Did a filter stop matching?** A renamed label, a changed subject line, a moved folder.
3. **Did a schema change?** A renamed column makes a lookup return nothing without erroring. Compare the sheet or table headers against the node's column mapping.
4. **Did a credential quietly narrow?** A token whose scope was reduced returns an empty list rather than a 403 on several APIs.

### Escalate if

It has been two hours and the source is demonstrably not empty. Data is being dropped on the floor right now, and every hour extends the backfill.

---

## CollectorRejectingEvents

**Severity:** warning · **Fires after:** 15m · **Threshold:** any rejections

### What it means

The exporter is refusing malformed events, so the dashboards are now under-reporting by an unknown amount.

**Monitoring that has quietly gone blind is worse than no monitoring, because it is still trusted.** That is why this is an alert rather than a log line.

### Check in this order

1. **What is being rejected?**
   ```bash
   docker compose logs observability --since 30m | grep '"event rejected"'
   ```
2. **Which reason?**
   ```promql
   sum by (reason) (increase(n8n_collector_rejected_events_total[1h]))
   ```
   - `bad_status` → a workflow is reporting a status outside the known set, probably a typo in a Code node.
   - `malformed` → a missing workflow name or a non-numeric duration.
   - `unparseable` → the body is not JSON. Usually a Set node emitting a string.
3. **Fix the sender, not the collector.** Loosening validation here means a bogus metric label that lives forever.

### Escalate if

Rejections exceed 5% of events. Treat every dashboard as unreliable until it is fixed, and say so to anyone reading them.

---

## MetricsEndpointDown

**Severity:** critical · **Fires after:** 5m

### What it means

Prometheus cannot scrape the exporter.

Every other rule in this file depends on this scrape. **While it is down, the absence of alerts means nothing** — which is exactly when people assume it means everything is fine.

### Check in this order

1. ```bash
   curl -s localhost:9109/healthz
   docker compose ps observability
   docker compose logs --tail=50 observability
   ```
2. **Can Prometheus reach it?** Inside the compose network the hostname is the service name, not `localhost`. A `localhost:9109` target in `prometheus.yml` is the classic version of this failure.
3. **Did the exporter OOM?** `docker compose logs` shows the kill. If so, look for a high-cardinality label — the registry is supposed to refuse past 2000 series per metric, so an OOM means something is creating metrics rather than series.

### Escalate if

It does not come back on restart. Until it does, the estate is unmonitored, and anyone relying on these dashboards needs to be told.

---

## When an alert is wrong

An alert that fires and is not actionable is a bug in the alert. Fix it the same way you would fix any other bug:

1. Write down **why** it fired and why that was not worth acting on.
2. Change the threshold, the `for` duration, or the expression — in `obs/alerts.py`, not in the YAML.
3. Regenerate: `py -m obs.alerts`
4. Run the tests: `py -m unittest discover -s tests -t .`
5. Commit both the source and the generated file together.

Do not mute it. A muted alert is a rule everyone has agreed to stop reading, still consuming the attention budget of the ones that matter.
