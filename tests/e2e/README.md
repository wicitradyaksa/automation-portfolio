# End-to-End Tests: All Seven Workflows in a Real n8n

```bash
py tests/e2e/run_e2e.py            # build, run every scenario, tear down (needs Docker)
py tests/e2e/run_e2e.py p3 p6      # just some workflows
py tests/e2e/run_e2e.py --keep     # leave n8n running on http://localhost:5679 to poke at
```

This imports every `workflow.json` into **n8n 2.x** (built from [`docker/n8n.Dockerfile`](../../docker/n8n.Dockerfile): the official image plus FFmpeg, bash and Python/Pillow, the same image the repo-root compose file runs). It runs each workflow through its business scenarios and asserts on what the workflow actually did.

## What is real and what is mocked

| Real | Mocked |
|---|---|
| n8n itself: import, publish, webhook routing, execution order, expression resolution, paired items | Slack, Google Sheets, SMTP, SSH and Postgres, whose nodes are rewritten into HTTP Requests to [`mock_api.py`](./mock_api.py) |
| Code, IF, Switch, Merge, Split In Batches, Wait, Respond to Webhook | The ad platform, affiliate network, ComfyUI and asset store, played by the same mock over real HTTP |
| HTTP Request nodes, including pagination, binary downloads and multipart uploads | The IMAP trigger, replaced by a Manual Trigger plus a PDF download from the mock |
| Execute Command: FFmpeg renders, `ffprobe`, the Python post-processor and significance test | The Docker Engine API behind the socket proxy: restarts answer `204` for known containers and `404` otherwise, so both the self-heal and the failed-restart path run |
| Webhook → workflow → webhook hand-offs (workflow 5 calling workflow 4) | |

The mock records every call **with its parameters already resolved by n8n**, so the tests check the values a real Slack message or Sheets row would have contained. The most common n8n bug, an expression reading the wrong item, shows up as a wrong or empty value in the recording.

[`build_harness.py`](./build_harness.py) makes the test copies. Node names, positions, connections and node settings (*Always Output Data*, *Execute Once*, *On Error*) are preserved, so the data flow under test is the one in the repo.

## Also here

- [`schema_check.py`](./schema_check.py) validates every node's type, version, parameter names and enum values against the schemas n8n itself exports (`n8n export:nodes`).
- The static linter lives at [`../../scripts/lint_workflows.py`](../../scripts/lint_workflows.py) and runs with `py run_tests.py`.

## Bugs these tests found (all fixed)

| Workflow | Bug | Why static checks missed it |
|---|---|---|
| 3 | Execute Command's own *Execute Once* parameter defaults to **on**, so with two services down only the first was restarted and re-checked. The second was silently dropped | Valid JSON, valid schema, no bad references |
| 5 | The Python post-processor echoed `videoSourcePath: null`, which overwrote the brief's value, so the render-farm hand-off could never fire | The bug was in the data, not the graph |
| 6 | The buying report hung off a node fed by three branches, so n8n ran it once per branch and posted three reports | Execution-order semantics |
| 1 | A returning lead was announced as "New lead: *Ada * ()" | Only visible in a resolved message |

## Known limits

- n8n doesn't start error workflows for CLI-launched runs, so the Error Trigger path is tested through a webhook (workflow 1). The CLI-run workflows assert on the failure itself.
- Workflow 5's 5-minute give-up is shortened to 30 s in the test copy, so the wedged-queue scenario runs in reasonable time.
- The mocks answer the way the real services do in the documented happy and failure cases. They are not the real services: credentials, OAuth consent and API quotas still need a sandbox run.
