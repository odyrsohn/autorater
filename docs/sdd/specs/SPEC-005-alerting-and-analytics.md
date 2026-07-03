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
- **REQ-OUT-10** The alerting service logs the canonical JSON envelope
  (`service=alerting`, `env`, snake_case `msg`); events `alert_dispatched`
  (`fingerprint`, `tenant_id`, `severity`, `failure_mode`, `lang`,
  `client_platform`, `client_os_version`, `serving_model`, `channels`),
  `alert_suppressed` (`fingerprint`, `tenant_id`, `duplicates`),
  `dispatch_failed`, `dispatch_mock` (mock-mode payload logging). The
  `Alert` struct's slice-dimension fields (`Lang`, `ClientPlatform`,
  `ClientOSVersion`, `ServingModel`) let on-call read them straight off the
  Slack/PagerDuty page, not just the logs.

### Analytics

- **REQ-OUT-6** Every judged case (alerted or not) is buffered per sweep
  and flushed as one JSONL object to
  `results/dt=YYYY-MM-DD/<sweep_id>.jsonl`, carrying all five slice
  dimensions (`tenant_id`, `failure_mode`, `lang`, `client_platform`/
  `client_os_version`, `serving_model`) plus `judge_category` whenever the
  source record/verdict provided them.
- **REQ-OUT-7** The Glue table `judged_cases` uses partition projection on
  `dt` — no crawler, no MSCK; columns match the results-row schema exactly,
  including `lang`, `client_platform`, `client_os_version`, `serving_model`,
  `judge_category`.
- **REQ-OUT-8** Athena named queries exist for: regression rate by
  day/tenant, top failure modes + judge category, safety category volumes,
  judge usage by (judge) model, **failure rate by serving model**
  (`failure-rate-by-serving-model` — Claude slice vs OSS-fallback slice),
  **regressions by language** (`regressions-by-language` — e.g.
  Spanish-only), **failures by client** (`failures-by-client` — e.g. AAOS
  12 / ChromeOS); the workgroup enforces its output location.
- **REQ-OUT-9** The miner emits one structured `sweep_summary` log event
  per sweep (canonical envelope, not a bare `print`); CloudWatch metric
  filters match `$.msg = "sweep_summary"` and lift `judge_calls`,
  `judge_failures`, `suppressed_by_dedup`, `safety_flags` into the
  `Autorater/<env>` namespace feeding the dashboard. Event name + these
  field keys are a compatibility contract.
- **REQ-OUT-11** Five saved CloudWatch Logs Insights queries per service
  pair (`aws_cloudwatch_query_definition`, named `<app>-<env>/<slice>`,
  spanning both the miner and alerting log groups) answer the standard
  on-call slices live: `by-tenant`, `by-failure-mode` (covers both
  `failure_mode` passthrough values like `asr_degradation`/
  `tts_degradation` and judge `category=hallucination`), `by-language`,
  `by-client`, `by-model`.
- **REQ-OUT-12** `failure_mode` (renamed from `failure_type`) is the
  standardized key across logs, alert payloads, results rows, the Glue
  column, and named queries — never `failure_type`. Historical Athena rows
  written before the rename return NULL for `failure_mode`.

## Anchors

| Req | Implementation | Tests / gates |
|---|---|---|
| REQ-OUT-1 | `alerting/handler.go` `handleAlert` | `handler_test.go` `TestValidation` |
| REQ-OUT-2 | `alerting/dedupe/dedupe.go` `Cache.Admit` | `dedupe_test.go` all; `TestDuplicateFingerprintSuppressed` |
| REQ-OUT-3 | `handler.go` routing; `dispatch/dispatch.go` | `TestHighSeverityGoesToSlackOnly`, `TestCriticalPagesBothChannels` |
| REQ-OUT-4 | `handler.go` delivered==0 branch | `TestAllChannelsFailingReturns502` |
| REQ-OUT-5 | `iac/secrets.tf`, `iac/ecs.tf` alerting secrets | terraform validate |
| REQ-OUT-6 | `miner/miner/results.py` `ResultsSink.flush`; `worker.py` `_judge_case` results dict | `tests/test_results.py`; `test_worker.py` `TestResultsWiring`, `TestSliceDimensionPropagation.test_results_row_carries_dims` |
| REQ-OUT-7 | `iac/analytics.tf` Glue table columns + projection params | terraform validate + schema review |
| REQ-OUT-8 | `iac/analytics.tf` `locals.named_queries`, workgroup | terraform validate |
| REQ-OUT-9 | `worker.report` → `sweep_summary`; `iac/analytics.tf` `locals.miner_metrics` filter pattern | E2E (`.plan/standardized-logging.md`); `test_sweep_summary_event_is_the_stats_contract`; filter/key match review |
| REQ-OUT-10 | `alerting/main.go` base attrs; `handler.go` event calls; `dispatch/dispatch.go` `Alert` fields | `handler_test.go` `TestHighSeverityGoesToSlackOnly` (envelope + dims), `TestDuplicateFingerprintSuppressed` |
| REQ-OUT-11 | `iac/queries.tf` `aws_cloudwatch_query_definition.oncall` | `terraform validate`; query text review against emitted field names |
| REQ-OUT-12 | rename applied in `worker.py`, `dispatch.go` (`FailureMode` tag), `iac/analytics.tf` | grep gate: no `failure_type` outside this note; `handler_test.go`, `test_worker.py` all use `failure_mode` |

## Verification

```bash
cd alerting && go test -race ./...
cd ../miner && python3 -m unittest tests.test_results tests.test_worker
cd ../iac && terraform init -backend=false && terraform validate
grep -rn "failure_type" --include='*.go' --include='*.py' --include='*.tf' . && echo "RENAME VIOLATION" || echo ok
```

## Open questions

- Should alert-loss on all-channel failure (REQ-OUT-4) retry from a queue
  instead of relying on the results row?
