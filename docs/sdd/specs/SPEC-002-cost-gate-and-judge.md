# SPEC-002 — Dedup cost gate & LLM-as-Judge

| | |
|---|---|
| Status | Active |
| Owner | mlops-platform |
| Anchors verified | branch `feature/sdd-and-skills` |

## Context

Judge API calls are the pipeline's dominant external cost. One bad deploy
emits thousands of near-identical failures; they must cost one judge call,
not thousands — and a flaky judge must never take the pipeline down.

## Goals / Non-goals

- Goals: semantic suppression before any spend, provider-agnostic judging,
  fault-tolerant verdicts, auditable accounting.
- Non-goals: exact-duplicate hashing only (near-duplicates must match),
  judge fine-tuning/prompt experimentation frameworks.

## Requirements

- **REQ-JUDGE-1** Every case passes the semantic dedup gate before the
  judge: shingle-set Jaccard ≥ 0.8 against TTL-bounded (1h) fingerprints ⇒
  suppressed (counted, never judged). Fingerprint memory is capped.
- **REQ-JUDGE-2** The gate keys on failure evidence — `failure_mode` +
  `response` — NOT the prompt (identical failures under many prompts must
  collapse).
- **REQ-JUDGE-3** The production judge calls OpenRouter's OpenAI-compatible
  chat completions API with `temperature: 0`; model comes from
  `JUDGE_MODEL` (default `anthropic/claude-sonnet-5`) with reasoning effort
  from `JUDGE_REASONING_EFFORT` (default `medium`); provider/model/effort
  switch is config-only.
- **REQ-JUDGE-4** Verdict parsing tolerates markdown fences and chatter;
  scores clamp to 0–100; unknown verdicts normalize to `degraded`.
- **REQ-JUDGE-5** Any judge invocation/parse failure yields the fallback
  verdict (`degraded`, 50), increments `judge_failures`, and never
  interrupts the sweep.
- **REQ-JUDGE-6** Without `OPENROUTER_API_KEY` the deterministic `MockJudge`
  runs (tests/local, zero spend), same accounting semantics.
- **REQ-JUDGE-7** Accounting: `calls`, `failures`, and (real judge)
  provider-reported `input_tokens`/`output_tokens` are tracked and reported
  in the `sweep_summary` structured log event (SPEC-005 REQ-OUT-9).
- **REQ-JUDGE-8** Severity `high` requires judge score ≥ 70 (or
  safety-forced `high`); nothing below threshold alerts (recorded only).
- **REQ-JUDGE-9** Every verdict carries a judge-assigned `category` ∈
  `CATEGORIES` (`hallucination`, `factual_error`, `refusal`, `format`,
  `other`) — a classification independent of `failure_mode` (e.g. a
  `retrieval_failure` case's judge `category` is commonly
  `hallucination`). Missing/invalid categories from a real judge normalize
  to `other`, same fault-tolerance contract as `verdict`.

## Anchors

| Req | Implementation | Tests |
|---|---|---|
| REQ-JUDGE-1 | `miner/miner/dedup.py` `SemanticDeduplicator` | `tests/test_dedup.py` all; `test_worker.py` `test_duplicates_never_reach_judge` |
| REQ-JUDGE-2 | `worker.py` `_judge_case` `gate_text` | `test_worker.py` cost-gate tests |
| REQ-JUDGE-3 | `miner/miner/judge.py` `OpenRouterJudge`, `DEFAULT_MODEL` | `tests/test_judge.py` `test_request_shape` |
| REQ-JUDGE-4 | `BaseJudge._parse` | `test_markdown_fenced_json_tolerated`, `test_score_clamped`, `test_invalid_verdict_normalized` |
| REQ-JUDGE-5 | `BaseJudge.score` try/except; `judge_fallback` event | `test_http_failure_falls_back`, `test_malformed_response_falls_back` |
| REQ-JUDGE-6 | `judge_from_env`, `MockJudge` | `TestJudgeFactory`, `TestMockJudge` |
| REQ-JUDGE-7 | `BaseJudge` counters; `worker.report` → `sweep_summary` | `test_successful_scoring` (usage), `test_sweep_summary_event_is_the_stats_contract` |
| REQ-JUDGE-8 | `worker._judge_case` severity block; `SEVERE_THRESHOLD` | `test_severe_case_fires_alert`, healthy-record test |
| REQ-JUDGE-9 | `judge.py` `CATEGORIES`, `Verdict.category`, `MockJudge._CATEGORY_BY_MODE` | `test_category_defaults_to_other_when_absent`, `test_category_normalized_when_invalid`, `test_retrieval_failure_maps_to_hallucination_category`, `test_category_always_in_allowed_set` |

## Verification

```bash
cd miner && python3 -m unittest tests.test_dedup tests.test_judge tests.test_worker
# cost gate live: skill run-e2e-smoke (suppressed_by_dedup climbs, judge_calls flat)
```

## Open questions

- Per-tenant dedup namespaces (one tenant's failures currently suppress
  another's identical signature — acceptable? saves more)?
