# SPEC-001 — Failure taxonomy & anomaly detection

| | |
|---|---|
| Status | Active |
| Owner | mlops-platform |
| Anchors verified | branch `feature/sdd-and-skills` |

## Context

The miner turns raw production traffic into evaluation cases. Two signals:
per-record failure classification (what is wrong) and a windowed rate view
(is it a regression, not a one-off).

## Goals / Non-goals

- Goals: cheap per-record heuristics, explicit-upstream-signal priority,
  baseline-relative anomaly detection with bounded memory.
- Non-goals: model-based classification at this stage (cost — see
  SPEC-002's gate), exact statistics (EWMA baseline is intentionally
  approximate).

## Requirements

- **REQ-MINE-1** An explicit `error_type` on a record always wins over
  heuristics.
- **REQ-MINE-2** Heuristic taxonomy: `retrieval_failure` (empty
  `retrieved_docs` or marker phrases), `non_terminating_loop` (a 24-byte
  chunk repeating ≥5×), `truncated_output` (`finish_reason=max_tokens` with
  non-terminal ending). Healthy records classify as None.
- **REQ-MINE-3** Classification runs on every record and must stay cheap
  (regex/markers/counting only).
- **REQ-MINE-4** The sliding-window detector keeps events for a bounded
  time window; evicted events feed an EWMA baseline (rate + variance).
- **REQ-MINE-5** A window is anomalous only when: ≥ `min_events` observed,
  current failure rate > baseline, and z-score ≥ `sigma` (default 3).
- **REQ-MINE-6** Both runtime failures and safety findings count as failure
  observations for the window (regression radar sees everything).
- **REQ-MINE-7** The classification result is called `failure_mode`
  everywhere downstream (logs, alerts, results rows) — never `failure_type`
  — and any upstream `error_type` string is accepted verbatim. Known
  non-text-client vocabulary (`asr_degradation`, `tts_degradation` — voice
  pipelines where the LLM stage never observes raw audio) is documented in
  `KNOWN_UPSTREAM_FAILURE_MODES` and asserted in tests.

## Anchors

| Req | Implementation | Tests |
|---|---|---|
| REQ-MINE-1 | `miner/miner/detector.py` `classify_failure` head | `tests/test_detector.py` `test_explicit_error_type_wins` |
| REQ-MINE-2 | `classify_failure`, `detect_repetition`, `RETRIEVAL_MARKERS` | `TestClassifyFailure.*` |
| REQ-MINE-3 | code review gate (no I/O in detector) | n/a |
| REQ-MINE-4 | `SlidingWindowDetector._evict` (EWMA on eviction) | `test_anomaly_on_failure_spike` |
| REQ-MINE-5 | `SlidingWindowDetector.observe` | `test_no_anomaly_below_min_events`, `test_no_anomaly_when_healthy` |
| REQ-MINE-6 | `miner/miner/worker.py` `process_record` observe call | `test_worker.py` safety + failure paths |
| REQ-MINE-7 | `detector.py` `KNOWN_UPSTREAM_FAILURE_MODES`; rename applied throughout `worker.py` | `test_known_upstream_failure_modes_pass_through`, `test_known_upstream_failure_modes_documented` |

## Verification

```bash
cd miner && python3 -m unittest tests.test_detector && ruff check .
```

## Open questions

- Per-tenant windows (currently global) for tenant-scoped regression
  detection?
