---
name: query-results
description: Query judged cases (regressions, safety findings, judge spend, on-call slices) via Athena/Logs Insights or local results files, and extend the analytics surface. Use for investigations, reports, and dashboard questions.
---

# Query judged-case results

Every judged case is one row in the `judged_cases` Glue table
(JSONL under `s3://<results-bucket>/results/dt=YYYY-MM-DD/`, partition
projection — no crawlers, `dt` is always available) AND one `case_judged`
structured log line. Spec anchor:
`docs/sdd/specs/SPEC-005-alerting-and-analytics.md`. Athena covers
history; Logs Insights covers the last hours live — see skill
`slice-logs-oncall`-equivalent queries below for the fast path.

## Canned Athena queries (workgroup `autorater-<env>`)

- `regression-rate-by-day-tenant` — judged cases, avg score, regressions,
  alerts per day/tenant (30d)
- `top-failure-types` — case volume + avg score per `failure_mode` +
  `judge_category` (7d)
- `safety-category-volumes` — findings per safety category per day (30d)
- `judge-usage-by-model` — judged cases + avg score per **judge** model
  per day (30d) — the model that scored, not the model that served traffic
- `failure-rate-by-serving-model` — regression rate per **serving model**
  per day (30d) — "compare Claude slice vs OSS fallback slice"
- `regressions-by-language` — regressions where `lang LIKE 'es%'` grouped
  by mode (30d) — "Spanish-only regressions"
- `failures-by-client` — cases grouped by `client_platform`/
  `client_os_version` (30d) — "AAOS 12 only"

Ad-hoc example:

```sql
SELECT case_id, tenant_id, failure_mode, judge_category, score, rationale
FROM autorater_<env>.judged_cases
WHERE dt = '2026-07-03' AND alerted AND cardinality(safety_categories) > 0
ORDER BY score DESC LIMIT 50;
```

Always filter on `dt` — it's the partition key; unfiltered queries scan
everything.

## Saved Logs Insights queries (live, last hours)

`<app>-<env>/by-tenant`, `by-failure-mode`, `by-language`, `by-client`,
`by-model` — same five slices over `case_judged`/`alert_dispatched` log
lines instead of the Athena table. Fastest path for an active incident;
edit the literal filter value and run.

## Columns / envelope keys

`case_id, tenant_id, failure_mode, safety_categories(array), judge_category,
lang, client_platform, client_os_version, serving_model, score, verdict,
rationale, model, window_failure_rate, alerted, sweep_id, ts` + partition
`dt`. `model` = judge model; `serving_model` = the traffic's own model —
don't confuse them.

## Local (no AWS)

Local sweeps write the same JSONL under `./results/results/dt=*/`:

```bash
cat results/results/dt=*/*.jsonl | python3 -m json.tool --json-lines
# or jq: ... | jq -s 'group_by(.failure_mode) | map({mode: .[0].failure_mode, n: length})'
```

## Extending the surface

- **New results field**: add it in `MiningWorker._judge_case`'s
  `results.write(...)` dict AND as a column in the Glue table
  (`iac/analytics.tf`) — same name, Athena-compatible type. Old rows simply
  return NULL for it. Also add it to the `case_judged` log event if it's a
  slice-worthy dimension.
- **New canned query**: add to `locals.named_queries` in
  `iac/analytics.tf`.
- **New saved Logs Insights query**: add to `locals.autorater_saved_queries`
  in `iac/queries.tf`.
- **New dashboard metric**: add the key to the `sweep_summary` event
  (`MiningWorker.report`) and a matching entry in `locals.miner_metrics`
  + a widget in `iac/analytics.tf`. Event name + stats keys are a
  contract — never rename existing ones without moving the filter in the
  same change.
