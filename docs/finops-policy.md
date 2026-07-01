# FinOps Policy — Evaluation-Mining & Autorater Pipeline

**Status:** Mandatory. PRs that add untagged infrastructure fail review.

## Tagging rule

All AWS resources inherit five cost-allocation tags from the Terraform
provider `default_tags` block (`infra/main.tf`): `app:name`,
`app:projectName`, `app:component`, `app:teamName`, `app:env`. Because the
tags are applied at the provider, they propagate to every resource in the
stack — including the observability layer itself: CloudWatch log groups,
metric filters, alarms, the X-Ray sampling rule and group, ECR repositories
and the ECS cluster all carry them.

## The executive billing dashboard story

The question leadership asks about an evaluation pipeline is always the same:
*"what does judging our LLM traffic actually cost, and who is spending it?"*
The tag set answers it directly on a Cost Explorer / CUR dashboard:

- **`app:projectName=eval-mining-autorater`** isolates the entire pipeline's
  spend — compute sweeps, log retention, tracing — as one line an executive
  can track quarter over quarter.
- **`app:component`** splits *observability* cost (CloudWatch ingestion,
  X-Ray traces) from *evaluation compute* cost (Fargate sweeps). When the
  logging bill grows faster than the mining bill, that shows up as a
  diverging pair of lines, not a mystery.
- **`app:teamName` + `app:env`** route the numbers to the owning budget:
  mlops-platform's prod spend is alertable via AWS Budgets without any
  manual allocation spreadsheet.

## Cost controls built into the system design

The pipeline's dominant *external* cost — LLM-as-Judge API calls — is
governed in code, not by hope:

1. **Semantic deduplication gate** (`miner/miner/dedup.py`): near-identical
   production failures are fingerprinted and suppressed before the judge is
   invoked. One bad deploy producing 10,000 identical traces costs *one*
   judge call, not 10,000. The worker logs `judge_calls` vs
   `suppressed_by_dedup` every sweep, making the avoided spend a reportable
   metric.
2. **Sliding-window anomaly detection** escalates severity without extra
   API calls — it reuses observations the miner already made.
3. **Scheduled sweeps, not resident pollers.** EventBridge launches the
   miner Fargate task on a cron; between sweeps the pipeline's compute cost
   is zero.
4. **Observability caps:** 30-day log retention and a 10% X-Ray sampling
   rate keep the tracing hub's cost proportional, and — because it is tagged
   — visible.
