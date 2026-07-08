# autorater

Evaluation-mining pipeline: async Python miner sweeps ingested LLM traffic
for runtime failures + safety violations, cost-gates cases through semantic
dedup, scores with an LLM-as-Judge (OpenRouter, Claude Sonnet 5 / medium
effort default), lands
verdicts in a SQL-queryable results lake, and alerts through a Go
webhook (Slack/PagerDuty). **Multi-cloud**: Terraform in `iac/aws`
(us-east-1, Athena/Glue) AND `iac/azure` (eastus, Synapse serverless) —
both valid targets. Diagrams: `docs/architecture.md`. Specs: `docs/sdd/`.
Translation table: `docs/cloud-portability.md`.

## Components & commands

| Component | Path | Test / lint |
|---|---|---|
| Mining worker (Py 3.12) | `miner/` | `python3 -m unittest discover -s tests` · `ruff check .` · `ruff format --check .` |
| Alerting engine (Go 1.22) | `alerting/` | `go test -race ./...` · `gofmt -l .` · `go vet ./...` |
| Terraform (BOTH roots) | `iac/aws/`, `iac/azure/` | per root: `terraform fmt -check -recursive` · `terraform init -backend=false && terraform validate` |

Local E2E: see skill `run-e2e-smoke` (alerting in mock-dispatch mode +
miner over a JSONL dir with `CURSOR_FILE`; second sweep must process 0).

## Multi-cloud

Two sibling IaC roots — keep them **symmetric**: a resource added to one is
added to the other in the same change, or the gap is recorded in
`docs/cloud-portability.md`. The miner picks its backends from
`CLOUD_PROVIDER=aws|azure` (unset = local/dev fallbacks; unknown = fail
fast): `cursor_store_from_env` (DynamoDB ↔ Table Storage, same
conditional-write lease), `source_from_env` (S3 ↔ Blob), and
`results_sink_from_env` (S3 ↔ ADLS Gen2 for Synapse). Azure SDKs are
lazily imported like boto3; adapters live in `miner/miner/azure_sources.py`
+ `BlobResultsSink` in `results.py`, tested with fakes. The seven Athena
named queries have Synapse serverless twins in
`iac/azure/synapse-queries.sql` (OPENROWSET + filepath()).

## Hard conventions

- **The dedup gate guards spend**: nothing reaches the judge without
  passing `SemanticDeduplicator` (`miner/miner/dedup.py`). The gate keys on
  `failure_mode + response` (failure evidence), NOT the prompt. Never add a
  judge call outside `MiningWorker._judge_case`.
- **A judge must never crash a sweep**: HTTP/parse failures degrade to the
  fallback verdict (`degraded`/50, `category="other"`) and increment
  `judge_failures`, logged as `judge_fallback`. Keep that contract for any
  new provider (`miner/miner/judge.py::BaseJudge`).
- **Model switching is config**: `JUDGE_MODEL` env / `judge_model` TF var.
  No provider SDKs — the OpenRouter call is stdlib urllib on purpose.
- **Sweep state is durable**: cursors + single-runner lease live in
  DynamoDB (`CURSOR_TABLE`) or a local JSON file (`CURSOR_FILE`). Sources
  advance the cursor only AFTER a record is consumed (at-least-once). Do
  not reintroduce in-memory seen-sets.
- **Results rows are the query surface**: any new field written by
  `MiningWorker._judge_case` → results sink must be added to the Glue table
  columns in `iac/aws/analytics.tf` and, if relevant, the named queries.
- **`sweep_summary` is a metrics contract**: `worker.report()`'s structured
  event feeds CloudWatch metric filters (`iac/aws/analytics.tf`). Renaming the
  event name or a field key breaks the dashboard — treat both as API.
- **`failure_mode` is the standardized key** — never `failure_type` —
  across logs, alert payloads, results rows, the Glue column and named
  queries. `judge_category` is a *separate* judge-assigned classification
  (`hallucination`/`factual_error`/`refusal`/`format`/`other`); a
  `retrieval_failure` case commonly has `judge_category=hallucination`.
- **Tags**: five `app:*` tags via provider `default_tags` (`iac/aws/main.tf`);
  never per-resource. **Secrets**: SSM SecureStrings under
  `/projects/autorater/` (`OPENROUTER_API_KEY`, `SLACK_WEBHOOK_URL`,
  `PAGERDUTY_ROUTING_KEY`), values set manually per `iac/README.md`.
- **Severity routing**: `high` → Slack; `critical` (safety-forced or window
  anomaly) → Slack + PagerDuty. Alerting dedupes by fingerprint (15 min TTL).

## Structured logging (canonical envelope)

Both services emit single-line JSON: `time`, `level`, `msg` (a **stable
snake_case event name**, not prose), `service` (`miner`|`alerting`), `env`,
plus whichever slice-dimension keys are known: `tenant_id`, `failure_mode`,
`lang`, `client_platform`/`client_os_version`, `serving_model`,
`judge_category`, `case_id`/`sweep_id`. Python uses
`miner/miner/obslog.py` (`configure()` once, `log_event(logger,
"event_name", **fields)` everywhere); Go uses `log/slog` with base attrs
via `.With(...)`. **Never log prompt/response content.**

Event names + field keys are a compatibility contract — CloudWatch metric
filters (`iac/aws/analytics.tf`, `iac/aws/observability.tf`) and five saved Logs
Insights queries (`iac/aws/queries.tf`: `by-tenant`, `by-failure-mode`,
`by-language`, `by-client`, `by-model`) match on them. Renaming one means
moving the filter/query in the same change. Full design:
`.plan/standardized-logging.md`.

## Workflow

- PRs run lint/tests, `terraform validate`, Trivy, and post a plan comment;
  merge to `main` applies + deploys (OIDC). The miner needs no rollout —
  EventBridge launches the fresh image next sweep.
- `.claude/skills/`: guides for the five most common changes (new failure
  mode, new safety category, judge model switch, querying results, E2E
  smoke).
- Specs in `docs/sdd/specs/` are spec-anchored: behavior changes start by
  updating the matching `SPEC-*` requirement and its Anchors table.
