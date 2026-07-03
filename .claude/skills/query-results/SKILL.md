---
name: query-results
description: Query judged cases (regressions, safety findings, judge spend) via Athena or local results files, and extend the analytics surface. Use for investigations, reports, and dashboard questions.
---

# Query judged-case results

Every judged case is one row in the `judged_cases` Glue table
(JSONL under `s3://<results-bucket>/results/dt=YYYY-MM-DD/`, partition
projection — no crawlers, `dt` is always available). Spec anchor:
`docs/sdd/specs/SPEC-005-alerting-and-analytics.md`.

## Canned queries (Athena workgroup `autorater-<env>`)

- `regression-rate-by-day-tenant` — judged cases, avg score, regressions,
  alerts per day/tenant (30d)
- `top-failure-types` — case volume + avg score per failure type (7d)
- `safety-category-volumes` — findings per safety category per day (30d)
- `judge-usage-by-model` — judged cases + avg score per model per day (30d)

Ad-hoc example:

```sql
SELECT case_id, tenant_id, failure_type, score, rationale
FROM autorater_<env>.judged_cases
WHERE dt = '2026-07-03' AND alerted AND cardinality(safety_categories) > 0
ORDER BY score DESC LIMIT 50;
```

Always filter on `dt` — it's the partition key; unfiltered queries scan
everything.

## Columns

`case_id, tenant_id, failure_type, safety_categories(array), score, verdict,
rationale, model, window_failure_rate, alerted, sweep_id, ts` + partition
`dt`.

## Local (no AWS)

Local sweeps write the same JSONL under `./results/results/dt=*/`:

```bash
cat results/results/dt=*/*.jsonl | python3 -m json.tool --json-lines
# or jq: ... | jq -s 'group_by(.failure_type) | map({type: .[0].failure_type, n: length})'
```

## Extending the surface

- **New results field**: add it in `MiningWorker._judge_case`'s
  `results.write(...)` dict AND as a column in the Glue table
  (`iac/analytics.tf`) — same name, Athena-compatible type. Old rows simply
  return NULL for it.
- **New canned query**: add to `locals.named_queries` in
  `iac/analytics.tf`.
- **New dashboard metric**: add the key to the `miner_stats` line
  (`MiningWorker.report`) and a matching entry in `locals.miner_metrics`
  + a widget in `iac/analytics.tf`. Stats keys are a contract — never
  rename existing ones.
