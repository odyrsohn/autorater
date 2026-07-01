# Automated Evaluation-Mining Pipeline & LLM-as-Judge Autorater

A Python mining worker sweeps ingested production LLM traffic for runtime
failures (retrieval misses, non-terminating loops, truncations), gates them
through cost controls, scores survivors with an LLM-as-Judge (mocked Claude
call), and reports severe regressions to a Go alerting engine that
deduplicates and pages Slack/PagerDuty.

```
S3 data lake ──poll──▶ miner (Python, async)
                        │ classify_failure ─▶ sliding-window anomaly radar
                        │ semantic dedup gate  ◀── cost control: duplicate
                        │ LLM-as-Judge (mock Claude)   failures never re-judged
                        ▼ severe/anomalous only
              alerting (Go) ── fingerprint dedupe ──▶ Slack (high)
                                                  └─▶ Slack + PagerDuty (critical)
```

## Repository layout

```
├── miner/                 Python async mining worker
│   └── miner/             detector.py (failure taxonomy + sliding window),
│                          dedup.py (Jaccard fingerprint gate), judge.py
│                          (mock Claude + call/token accounting), worker.py
├── alerting/              Go webhook: TTL dedupe + Slack/PagerDuty dispatch
├── infra/                 Terraform: ECS cluster, EventBridge cron, ECR,
│                          CloudWatch + X-Ray hub, default_tags
├── .github/workflows/     lint/tests/tf-validate/trivy → plan (PR) → apply+deploy (main)
└── docs/finops-policy.md
```

## Local evaluation runs

Both services run with zero cloud dependencies:

```bash
# Terminal 1 — alerting engine (mock dispatch mode: payloads are logged)
cd alerting && go test -race ./... && go run .

# Terminal 2 — mining worker over local traffic
cd miner && python -m unittest discover -s tests
mkdir -p data && cat > data/traffic.jsonl <<'EOF'
{"record_id":"r1","tenant_id":"acme","prompt":"tire spec?","response":"I will check the database. I will check the database. I will check the database. I will check the database. I will check the database. I will check the database."}
{"record_id":"r2","tenant_id":"acme","prompt":"route?","response":"no relevant documents found"}
EOF
LOCAL_DATA_DIR=./data ALERT_WEBHOOK_URL=http://localhost:8070/v1/alerts python -m miner.worker
```

The worker logs per-sweep stats — `judge_calls` vs `suppressed_by_dedup` is
the live view of the cost gate working. Re-appending the same failures shows
suppression climbing while judge calls stay flat.

Swapping the mock for the real API: replace `MockClaudeJudge._invoke` with an
`anthropic` `messages.create` call using the same `RUBRIC_PROMPT`; nothing
else changes.

## Deployment guide

1. **PR:** gofmt/vet + `go test -race`, ruff + unittest, `terraform fmt`/
   `validate`, Trivy IaC security scan, and a `terraform plan` posted as a
   PR comment.
2. **Merge to main:** `terraform apply` provisions the ECS cluster, the
   EventBridge schedule (`rate(15 minutes)` in prod), ECR repos, CloudWatch
   log groups/alarms and the X-Ray sampling rule + group.
3. **App deploy:** both images are built and pushed to ECR; the alerting ECS
   service is rolled. The miner needs no rollout — EventBridge launches the
   new image on the next scheduled sweep.

Auth is GitHub OIDC → AWS IAM roles; per-env settings live in
`infra/envs/*.tfvars`.

## FinOps highlight: the pipeline's external API consumption

The expensive resource here is not compute — it is the LLM-as-Judge API.
Cost control is embedded in the ML system design: a **semantic
deduplication gate** (shingle-set Jaccard with TTL) sits *before* the judge,
so repeated production failures are counted, not re-scored, and a
**sliding-window anomaly detector** escalates severity using data the miner
already has. Every AWS resource inherits the five `app:*` tags via provider
`default_tags`, which splits evaluation compute from observability spend on
the executive billing dashboard. Full policy:
[docs/finops-policy.md](docs/finops-policy.md).
