# Plan — Standardized, sliceable logging (autorater)

Goal: miner + alerting emit the canonical JSON envelope, and every judged
case carries the five slice dimensions — **tenant**, **failure mode**,
**language**, **client/environment**, **serving model** — through logs,
alerts, AND the Athena results table, so on-call and analysts ask the same
questions in Logs Insights (last hours) and Athena (history).

## Canonical log envelope (shared across all three repos)

| Key | Req | Notes |
|---|---|---|
| `time`, `level`, `msg` | ✔ | `msg` = stable snake_case event name |
| `service` | ✔ | `miner` \| `alerting` |
| `env` | ✔ | dev/staging/prod |
| `tenant_id` | when known | dim 1 |
| `failure_mode` | on cases | dim 2 — **standardized key**, replaces `failure_type` (see item 6) |
| `lang` | when known | dim 3 — from the record (`und` fallback) |
| `client_platform`, `client_os_version` | when known | dim 4 |
| `serving_model` | when known | dim 5 — model that served the traffic (distinct from `judge_model`) |
| `record_id`, `case_id`, `sweep_id` | when known | correlation |

Depends on: multi-tenant-ingestion plan item 1 (schema carries
`lang`/`client`/`model` into the lake) — the miner can only slice what the
records contain. Sequence the two repos' plans together.

## Gap analysis (today)

| Where | State | Problem |
|---|---|---|
| `miner/miner/worker.py` etc. | **plain-text logs** + one pure-JSON `miner_stats` line | text logs unsliceable; `judged case=… type=…` is prose |
| Case context (`worker._judge_case`) | tenant_id, failure_type only | **no lang / client / serving_model** anywhere in cases, alerts, or results |
| `alerting/*` (Go) | slog JSON ✔ | no `service`/`env` base attrs; prose msgs (`alert dispatched`); alert struct lacks lang/client/serving_model |
| Results table (`iac/analytics.tf`) | 12 columns | missing the three new dims ⇒ Athena can't answer "failure rate by serving model" or "Spanish-only regressions" |
| Failure taxonomy (`miner/miner/detector.py`) | retrieval/loop/truncation + `error_type` passthrough + `safety:*` | no `hallucination`; ASR/TTS degradation only works if upstream sends `error_type` — undocumented |
| Judge rubric (`miner/miner/judge.py`) | score/verdict/rationale | no failure *category* from the judge ⇒ hallucination can't be classified today |

## Work items

1. **Miner JSON logging** — `miner/miner/obslog.py` formatter
   (service=`miner`, env from `APP_ENV`); convert all module loggers. Key
   events: `case_judged` {case_id, tenant_id, failure_mode, lang,
   client_platform, client_os_version, serving_model, score, verdict,
   judge_model, sweep_id}, `case_suppressed` {failure_mode, tenant_id},
   `alert_sent`, `judge_fallback`, `sweep_summary` (replaces the bare
   `miner_stats` print — SAME JSON keys, now inside the envelope; update the
   metric-filter pattern from `{ $.metric = "miner_stats" }` to
   `{ $.msg = "sweep_summary" }` atomically).
2. **Carry the dims** — `worker.process_record` extracts `lang`,
   `client.*`, `model` (→ `serving_model`) from the record into case
   context → logs, alert payload, results row.
3. **Results schema + queries** — add `lang`, `client_platform`,
   `client_os_version`, `serving_model` columns to the Glue table; new
   named queries:
   - `failure-rate-by-serving-model`: judged cases, regressions, regression
     rate per `serving_model` per day → "compare Claude slice vs OSS
     fallback slice" verbatim.
   - `regressions-by-language`: `WHERE verdict='regression' AND lang LIKE 'es%'` grouping.
   - `failures-by-client`: group by client_platform, client_os_version.
4. **Alerting (Go)** — base attrs (service=`alerting`); event renames
   (`alert_dispatched`, `alert_suppressed`); `Alert` struct + Slack/PD
   payloads gain `lang`, `client_platform`, `client_os_version`,
   `serving_model` (on-call sees the slice keys in the page itself).
5. **Taxonomy for the asked-for modes**:
   - Document + test the upstream `error_type` passthrough contract with an
     allow-listed vocabulary incl. `asr_degradation`, `tts_degradation`
     (voice clients report these; the miner treats them as first-class
     failure modes — they already flow, but unvalidated/undocumented).
   - `hallucination`: extend `RUBRIC_PROMPT` to also return
     `"category": "<hallucination|factual_error|refusal|format|other>"`;
     parse into `judge_category`, log + store it (new results column), and
     let `judge_category=hallucination` be a filterable classification.
     Mock judge returns deterministic categories for tests.
6. **`failure_type` → `failure_mode` rename** (cross-cutting decision):
   standardize the key everywhere (logs, alert payload JSON, results
   column, Glue, named queries, Go struct tags, specs, skills). Dev-scale
   data ⇒ recreate the Glue table with the new column; historical JSONL
   keeps the old key (rows predating the rename return NULL — acceptable,
   note in docs). Alternative if history matters later: view aliasing.
7. **Saved Logs Insights queries** (`iac/queries.tf`) on the miner +
   alerting log groups — the on-call menu:
   - `by-tenant`: `filter tenant_id = "TENANT_A" | fields @timestamp, msg, failure_mode, score, serving_model | sort @timestamp desc`
   - `by-failure-mode`: `filter failure_mode in ["hallucination","asr_degradation","tts_degradation"] | stats count() by failure_mode, bin(15m)` (any value works; example matches the on-call phrasing)
   - `by-language`: `filter msg = "case_judged" and lang like /^es/ and verdict = "regression"`
   - `by-client`: `filter client_platform = "aaos" and client_os_version like /^12/ | stats count() by failure_mode`
   - `by-model`: `filter msg = "case_judged" | stats count() as cases, sum(verdict = "regression") as regressions by serving_model`
8. **trafficgen alignment** (other repo): consume its lang/client/model
   mixes in the E2E smoke; assert `case_judged` events carry the dims.
9. **Docs**: SPEC-001/002/005 requirement + anchor updates (new REQ rows
   for dims, judge_category, envelope); CLAUDE.md contract list gains the
   new stable keys; skills `add-failure-type`/`query-results` updated.

## Acceptance (the five on-call questions)

1. Tenant A outage → Logs Insights `by-tenant`, seconds.
2. Hallucination / ASR-TTS only → `by-failure-mode` (and Athena
   `top-failure-types` picks the new modes up automatically).
3. Spanish-only prompt regressions → `by-language` (live) /
   `regressions-by-language` (history).
4. AAOS 12 / ChromeOS slice → `by-client` / `failures-by-client`.
5. Claude-vs-fallback failure rates → `by-model` /
   `failure-rate-by-serving-model`.

## Rollout order & risks

Item 6 (rename) decides everything downstream — do it first on paper, then
1+2 together (one deploy: emitters + `sweep_summary` filter), then 3–5,
then 7–9. Contracts that change and must move atomically: `miner_stats`
metric filter, `failure_type` key consumers (alerting struct tag, Glue,
named queries, dashboards). Judge-rubric change (5) alters the prompt —
re-run one live Gemini case to confirm category parsing before merging.
