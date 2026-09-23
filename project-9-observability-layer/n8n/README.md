# Wiring an existing n8n workflow into this layer

Three nodes per workflow. No change to the business logic, which is the point — instrumentation that requires rewriting a workflow does not get added to the seven that already exist.

---

## 1. At the entry point — adopt or start the trace

A **Code** node, first thing after the trigger.

```javascript
// Adopt the caller's correlation id, or start a new trace.
//
// The regex is the same one obs/correlation.py enforces. Validating here
// matters because the id ends up in log lines and metric labels: an
// unvalidated value from an inbound webhook is a log-injection vector, and
// a high-cardinality one is a Prometheus killer.
const VALID = /^[a-z0-9]{1,12}-[0-9a-f]{16}$/;

const incoming =
  $json.headers?.['x-correlation-id'] ??
  $json.correlation_id ??
  null;

function newId(prefix = 'n8n') {
  let hex = '';
  for (let i = 0; i < 16; i++) hex += Math.floor(Math.random() * 16).toString(16);
  return `${prefix}-${hex}`;
}

const correlationId = VALID.test(incoming ?? '') ? incoming : newId();

return [{
  json: {
    ...$json,
    correlation_id: correlationId,
    // Monotonic-ish start marker. Used to compute duration at the end.
    _started_at: Date.now(),
  },
}];
```

## 2. At every outbound hop — propagate it

On any **HTTP Request** node that calls another workflow or an external service, add one header:

| Name | Value |
|---|---|
| `X-Correlation-Id` | `={{ $json.correlation_id }}` |

For the Bash hops — `vps_healthcheck.sh` and `render_variant.sh` in projects 4 and 7 — an **Execute Command** node cannot carry a header, so the id travels as an environment variable. `obs.correlation.from_environ()` picks it back up:

```
CORRELATION_ID={{ $json.correlation_id }} ./scripts/vps_healthcheck.sh
```

## 3. At the exit — report the outcome

An **HTTP Request** node on the success path, and the same node on the error path with `status` set to `error`.

- **Method:** POST
- **URL:** `http://observability:9109/events` (inside the compose network) or `http://localhost:9109/events`
- **Header:** `X-Correlation-Id: ={{ $json.correlation_id }}`
- **Body (JSON):**

```json
{
  "workflow": "={{ $workflow.name }}",
  "status": "success",
  "duration_seconds": "={{ ($now.toMillis() - $json._started_at) / 1000 }}",
  "items": "={{ $items().length }}",
  "retries": 0,
  "correlation_id": "={{ $json.correlation_id }}"
}
```

**Set `items` honestly.** It is what `WorkflowSucceedingButDoingNothing` alerts on, and inflating it to make a dashboard look busy disables the alert that catches a source going silently empty.

---

## Also wire the error trigger

Every workflow in this portfolio already has an `errorTrigger`. Add one HTTP Request node to it:

```json
{
  "workflow": "={{ $json.workflow.name }}",
  "status": "error",
  "duration_seconds": "={{ $json.execution?.executionTime ?? 0 }}",
  "node": "={{ $json.execution?.lastNodeExecuted ?? '' }}",
  "correlation_id": "={{ $json.correlation_id ?? '' }}"
}
```

The `node` field is what powers the *Failures by node* panel, which skips the first step of triage entirely — you know which node broke before you open the execution.

---

## Verify it landed

```bash
curl -s localhost:9109/metrics | grep 'workflow="your-workflow-name"'
```

You should see `runs_total`, a `duration_seconds` histogram, and a `last_success_timestamp_seconds`. If `last_success` is missing, the success path is not reporting, and the staleness alert cannot protect that workflow.

Then confirm the trace actually joins across hops:

```bash
docker compose logs --since 10m | grep '"correlation_id":"n8n-abc123..."'
```

One id, every hop, in order. That is the thing this project exists to make possible.

---

## What this deliberately does not do

It does not use n8n's own Prometheus endpoint (`N8N_METRICS=true`). That exposes process-level metrics — event loop lag, HTTP request counts — which tell you whether *n8n* is healthy. This layer answers a different question: whether the **workflows** are doing their work. Both are worth having, and they scrape independently.
