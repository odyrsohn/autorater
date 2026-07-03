# autorater

Evaluation-mining pipeline: async Python miner sweeps ingested LLM traffic
for runtime failures + safety violations, cost-gates cases through semantic
dedup, scores with an LLM-as-Judge (OpenRouter, Gemini default), lands
verdicts in an Athena-queryable results lake, and alerts through a Go
webhook (Slack/PagerDuty). Terraform in `iac/`, region `us-east-1`.
Diagrams: `docs/architecture.md`. Specs: `docs/sdd/`.

## Components & commands

| Component | Path | Test / lint |
|---|---|---|
| Mining worker (Py 3.12) | `miner/` | `python3 -m unittest discover -s tests` · `ruff check .` · `ruff format --check .` |
| Alerting engine (Go 1.22) | `alerting/` | `go test -race ./...` · `gofmt -l .` · `go vet ./...` |
| Terraform | `iac/` | `terraform fmt -check -recursive` · `terraform init -backend=false && terraform validate` |

Local E2E: see skill `run-e2e-smoke` (alerting in mock-dispatch mode +
miner over a JSONL dir with `CURSOR_FILE`; second sweep must process 0).

## Hard conventions

- **The dedup gate guards spend**: nothing reaches the judge without
  passing `SemanticDeduplicator` (`miner/miner/dedup.py`). The gate keys on
  `failure_type + response` (failure evidence), NOT the prompt. Never add a
  judge call outside `MiningWorker._judge_case`.
- **A judge must never crash a sweep**: HTTP/parse failures degrade to the
  fallback verdict (`degraded`/50) and increment `judge_failures`. Keep
  that contract for any new provider (`miner/miner/judge.py::BaseJudge`).
- **Model switching is config**: `JUDGE_MODEL` env / `judge_model` TF var.
  No provider SDKs — the OpenRouter call is stdlib urllib on purpose.
- **Sweep state is durable**: cursors + single-runner lease live in
  DynamoDB (`CURSOR_TABLE`) or a local JSON file (`CURSOR_FILE`). Sources
  advance the cursor only AFTER a record is consumed (at-least-once). Do
  not reintroduce in-memory seen-sets.
- **Results rows are the query surface**: any new field written by
  `MiningWorker._judge_case` → results sink must be added to the Glue table
  columns in `iac/analytics.tf` and, if relevant, the named queries.
- **Stats line is a metrics contract**: the pure-JSON `miner_stats` stdout
  line feeds CloudWatch metric filters (`iac/analytics.tf`). Renaming a key
  breaks dashboards — treat keys as API.
- **Tags**: five `app:*` tags via provider `default_tags` (`iac/main.tf`);
  never per-resource. **Secrets**: SSM SecureStrings under
  `/projects/autorater/` (`OPENROUTER_API_KEY`, `SLACK_WEBHOOK_URL`,
  `PAGERDUTY_ROUTING_KEY`), values set manually per `iac/README.md`.
- **Severity routing**: `high` → Slack; `critical` (safety-forced or window
  anomaly) → Slack + PagerDuty. Alerting dedupes by fingerprint (15 min TTL).

## Workflow

- PRs run lint/tests, `terraform validate`, Trivy, and post a plan comment;
  merge to `main` applies + deploys (OIDC). The miner needs no rollout —
  EventBridge launches the fresh image next sweep.
- `.claude/skills/`: guides for the five most common changes (new failure
  type, new safety category, judge model switch, querying results, E2E
  smoke).
- Specs in `docs/sdd/specs/` are spec-anchored: behavior changes start by
  updating the matching `SPEC-*` requirement and its Anchors table.
