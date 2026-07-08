---
name: add-safety-category
description: Add or tune a safety/abuse classification category (patterns, severity) in the miner. Use when a new abuse class must be flagged or an existing category over/under-triggers.
---

# Add a safety category

Spec anchor: `docs/sdd/specs/SPEC-003-safety-classification.md`
(REQ-SAFE-*).

## Steps

1. **Category + severity** — in `miner/miner/safety.py`:
   - add the category to `CATEGORY_SEVERITY`. `critical` pages PagerDuty
     immediately and bypasses score thresholds — reserve it for classes
     needing human eyes now (injection, self-harm); use `high` otherwise.
   - add a pattern list to `_PATTERNS` (compiled, case-insensitive where
     text-like). One finding per category per record is emitted (first
     match wins), evidence snippet capped at 80 chars.
2. **Pipeline is automatic** — `MiningWorker.process_record` turns each
   finding into a `safety:<category>` case through dedup → judge → alert;
   `safety_categories` flows into alerts and results rows. No worker
   changes needed unless severity semantics change.
3. **Mock judge** — `safety:*` types already get base score 80 in
   `MockJudge._invoke`; only touch it if the new category should mock
   differently.
4. **Tests** — `miner/tests/test_safety.py`: positive match, clean-text
   negative, severity assertion. If `critical`, add a pipeline test in
   `test_worker.py` modeled on
   `test_prompt_injection_forces_critical_alert`.
5. **Model-backed classifier?** — keep the interface: implement a class
   with the same `classify(prompt, response) -> list[SafetyFinding]`
   signature and inject it into `MiningWorker` — don't fork the worker.

## Verify

```bash
cd miner
python3 -m unittest tests.test_safety tests.test_worker
ruff check .
# Athena: safety-category-volumes named query picks the new category up
# automatically (safety_categories is already a column).
```
