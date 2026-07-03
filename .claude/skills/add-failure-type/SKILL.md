---
name: add-failure-type
description: Add a new runtime failure type to the miner's detection taxonomy (e.g. tool-call error, empty response, latency breach). Use when a new class of production failure should be mined and judged.
---

# Add a failure type

Spec anchor: `docs/sdd/specs/SPEC-001-failure-mining.md` (REQ-MINE-*) —
add a requirement row for the new type.

## Steps

1. **Detection** — extend `classify_failure` in `miner/miner/detector.py`.
   Rules:
   - Return a stable snake_case type string; `error_type` on the record
     always wins (explicit upstream signal) — keep new heuristics BELOW it.
   - Heuristics must be cheap (regex/marker/counter) — this runs on every
     record; anything expensive belongs behind the dedup gate.
   - Order matters: first match wins; place more specific checks above
     generic ones.
2. **Mock judge score** — add the type to the base-score map in
   `MockJudge._invoke` (`miner/miner/judge.py`) so local/dev runs and tests
   produce sensible severities (≥70 ⇒ severe ⇒ alert path exercised).
3. **Tests** — `miner/tests/test_detector.py`: positive detection case +
   at least one near-miss negative. If the type should alert end-to-end,
   add a pipeline case in `test_worker.py` modeled on
   `test_severe_case_fires_alert`.
4. **Analytics** — nothing to change: `failure_type` is already a results
   column and the `top-failure-types` Athena query picks it up. Only touch
   `iac/analytics.tf` if you add a NEW results field.
5. **Docs** — update the taxonomy list in the spec + the pipeline diagram
   in `docs/architecture.md` if it names types.

## Verify

```bash
cd miner
python3 -m unittest tests.test_detector tests.test_worker
ruff check . && ruff format --check .
# optional live: seed a record exhibiting the failure into the E2E smoke
# (skill run-e2e-smoke) and confirm a judged case + alert.
```
