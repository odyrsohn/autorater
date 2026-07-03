# SPEC-004 — Durable cursor & single-runner lease

| | |
|---|---|
| Status | Active |
| Owner | mlops-platform |
| Anchors verified | branch `feature/sdd-and-skills` |

## Context

The miner is a scheduled Fargate task — nothing in-process survives between
sweeps. Without durable state every launch would re-list and re-judge the
whole bucket, and overlapping launches would double-spend on the same
records.

## Goals / Non-goals

- Goals: incremental sweeps (list only unprocessed keys), at-least-once
  delivery, exactly one active miner, identical semantics locally (file)
  and in production (DynamoDB).
- Non-goals: exactly-once processing (dedup gate + idempotent alerting
  absorb the rare re-delivery), multi-runner sharding.

## Requirements

- **REQ-STATE-1** The cursor (last processed object key per source) is
  durable: DynamoDB item `cursor#<source>` in production, JSON file locally.
- **REQ-STATE-2** S3 listing resumes with `StartAfter=<cursor>` — a restart
  never re-lists processed key ranges.
- **REQ-STATE-3** The cursor advances only AFTER the consumer finished a
  record; a crash mid-record re-delivers exactly that record
  (at-least-once), never skips it.
- **REQ-STATE-4** Keys are date-ordered, so cursor resume is chronological;
  lexically-earlier late arrivals (backfills) are skipped by design and the
  tradeoff is documented.
- **REQ-STATE-5** A lease (`lease#miner`) taken with a conditional write
  (`attribute_not_exists OR expiry < now OR owner = me`) guarantees a
  single active miner; a second launch exits 0 without processing.
- **REQ-STATE-6** The lease is released on clean shutdown and self-expires
  (TTL 900s) after a crash — no permanent lockout.
- **REQ-STATE-7** Local dir sources use the same cursor contract (path
  order + durable store), so local runs also resume across processes.

## Anchors

| Req | Implementation | Tests |
|---|---|---|
| REQ-STATE-1 | `miner/miner/sources.py` `DynamoCursorStore`, `FileCursorStore`; `iac/dynamodb.tf` | `tests/test_sources.py` `TestFileCursorStore.test_cursor_survives_reopen` |
| REQ-STATE-2 | `S3Source.poll` StartAfter | `TestS3SourceResume.test_second_sweep_lists_after_cursor` |
| REQ-STATE-3 | `S3Source.poll` post-yield `store.set` | `test_partial_crash_resumes_at_least_once` |
| REQ-STATE-4 | module docstring; key scheme from ingestion repo | design note (review gate) |
| REQ-STATE-5 | `DynamoCursorStore.acquire_lease`; `worker.main` | `TestFileCursorStore.test_lease_blocks_second_owner` (same contract) |
| REQ-STATE-6 | `release_lease` in `worker.main` finally; `LEASE_TTL_SECONDS` | `test_expired_lease_taken_over`, `test_lease_reentrant_for_same_owner` |
| REQ-STATE-7 | `LocalDirSource.poll` | `TestLocalDirSourceResume.*` |

## Verification

```bash
cd miner && python3 -m unittest tests.test_sources
# live: skill run-e2e-smoke — sweep 2 must report records: 0
```

## Open questions

- Lease heartbeat/extension for sweeps longer than the TTL (900s)?
