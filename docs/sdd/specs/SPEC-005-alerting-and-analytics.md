# SPEC-005 — Alert routing & analytics surface

| | |
|---|---|
| Status | Active |
| Owner | mlops-platform |
| Anchors verified | branch `feature/sdd-and-skills` |

## Context

Two output surfaces: the alerting engine turns severe cases into pages
(deduplicated — one bad deploy pages once), and the results lake makes every
verdict queryable for reports, cost tracking and regression forensics.

## Goals / Non-goals

- Goals: fingerprint-level alert dedup, severity-based channel routing,
  crawler-free SQL surface, dashboard metrics from a stable stats contract.
- Non-goals: on-call scheduling/escalation policy (PagerDuty's job), BI
  dashboards beyond CloudWatch.

## Requirements

### Alerting (Go)

- **REQ-OUT-1** `POST /v1/alerts` validates fingerprint presence and
  severity ∈ {high, critical}; malformed → 400/422.
- **REQ-OUT-2** Alerts are deduplicated by fingerprint in a TTL cache
  (15 min): duplicates return `200 suppressed` with a duplicate count and
  are never re-dispatched.
- **REQ-OUT-3** Routing: `high` → Slack; `critical` → Slack + PagerDuty
  (Events v2, `dedup_key` = fingerprint). Payload shapes match the real
  APIs; with no URL configured a channel logs its payload (mock mode).
- **REQ-OUT-4** If every channel fails, the webhook returns `502` (the
  miner logs it; alert is lost — accepted, results row still exists).
- **REQ-OUT-5** Channel credentials come from SSM
  (`/projects/autorater/SLACK_WEBHOOK_URL`, `PAGERDUTY_ROUTING_KEY`).

### Analytics

- **REQ-OUT-6** Every judged case (alerted or not) is buffered per sweep
  and flushed as one JSONL object to
  `results/dt=YYYY-MM-DD/<sweep_id>.jsonl`.
- **REQ-OUT-7** The Glue table `judged_cases` uses partition projection on
  `dt` — no crawler, no MSCK; columns match the results-row schema exactly.
- **REQ-OUT-8** Athena named queries exist for: regression rate by
  day/tenant, top failure types, safety category volumes, judge usage by
  model; the workgroup enforces its output location.
- **REQ-OUT-9** The miner emits one pure-JSON `miner_stats` stdout line per
  sweep; CloudWatch metric filters lift `judge_calls`, `judge_failures`,
  `suppressed_by_dedup`, `safety_flags` into the `Autorater/<env>`
  namespace feeding the dashboard. Stats keys are a compatibility contract.

## Anchors

| Req | Implementation | Tests / gates |
|---|---|---|
| REQ-OUT-1 | `alerting/handler.go` `handleAlert` | `handler_test.go` `TestValidation` |
| REQ-OUT-2 | `alerting/dedupe/dedupe.go` `Cache.Admit` | `dedupe_test.go` all; `TestDuplicateFingerprintSuppressed` |
| REQ-OUT-3 | `handler.go` routing; `dispatch/dispatch.go` | `TestHighSeverityGoesToSlackOnly`, `TestCriticalPagesBothChannels` |
| REQ-OUT-4 | `handler.go` delivered==0 branch | `TestAllChannelsFailingReturns502` |
| REQ-OUT-5 | `iac/secrets.tf`, `iac/ecs.tf` alerting secrets | terraform validate |
| REQ-OUT-6 | `miner/miner/results.py` `ResultsSink.flush`; `worker.run` | `tests/test_results.py`; `test_worker.py` `TestResultsWiring` |
| REQ-OUT-7 | `iac/analytics.tf` Glue table + projection params | terraform validate + schema review |
| REQ-OUT-8 | `iac/analytics.tf` `locals.named_queries`, workgroup | terraform validate |
| REQ-OUT-9 | `worker.report`; `iac/analytics.tf` `locals.miner_metrics` | E2E stats line; filter/key match review |

## Verification

```bash
cd alerting && go test -race ./...
cd ../miner && python3 -m unittest tests.test_results tests.test_worker
cd ../iac && terraform init -backend=false && terraform validate
```

## Open questions

- Should alert-loss on all-channel failure (REQ-OUT-4) retry from a queue
  instead of relying on the results row?
