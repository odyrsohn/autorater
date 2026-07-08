# SPEC-003 — Safety/abuse classification

| | |
|---|---|
| Status | Active |
| Owner | mlops-platform |
| Anchors verified | branch `feature/sdd-and-skills` |

## Context

Mined traffic can contain abuse aimed at the product: prompt-injection
attempts, self-harm signals, abusive language, and PII that leaked past the
upstream redaction pipeline. These must surface with appropriate urgency,
not just as generic "failures".

## Goals / Non-goals

- Goals: rule-based first pass on every record, category-scoped severities,
  reuse of the existing dedup/judge/alert path, pluggable for a model-backed
  classifier.
- Non-goals: blocking/filtering traffic (detection & escalation only),
  perfect recall (rules are a tripwire, not a moderation system).

## Requirements

- **REQ-SAFE-1** Every record's prompt AND response are classified against
  the category rule sets on every sweep.
- **REQ-SAFE-2** Categories and severities: `prompt_injection` → critical,
  `self_harm` → critical, `abusive_language` → high, `pii_leak` → high.
- **REQ-SAFE-3** At most one finding per category per record, carrying an
  evidence snippet (≤80 chars).
- **REQ-SAFE-4** Findings become `safety:<category>` cases flowing through
  the standard dedup → judge → results → alert path; `critical` categories
  force alert severity regardless of judge score or window state.
- **REQ-SAFE-5** `safety_categories` propagates into alert payloads
  (Slack/PagerDuty render it) and results rows (Athena-queryable).
- **REQ-SAFE-6** `pii_leak` rules mirror the ingestion redactor's pattern
  shapes — a hit means the upstream filter leaked; treat as a
  cross-pipeline defect.
- **REQ-SAFE-7** The classifier is replaceable: same
  `classify(prompt, response) -> list[SafetyFinding]` contract for a future
  model-backed implementation.

## Anchors

| Req | Implementation | Tests |
|---|---|---|
| REQ-SAFE-1 | `miner/miner/worker.py` `process_record` | `tests/test_worker.py` `TestSafetyPipeline` |
| REQ-SAFE-2 | `miner/miner/safety.py` `CATEGORY_SEVERITY`, `_PATTERNS` | `tests/test_safety.py` category + severity tests |
| REQ-SAFE-3 | `SafetyClassifier.classify` break-per-category | `test_one_finding_per_category`, `test_evidence_snippet_captured` |
| REQ-SAFE-4 | `worker._judge_case` `forced_severity` | `test_prompt_injection_forces_critical_alert`, `test_safety_cases_also_pass_dedup_gate` |
| REQ-SAFE-5 | alert payload in `_judge_case`; `alerting/dispatch/dispatch.go` `Alert.SafetyCategories` | Go `handler_test.go` + payload assertions |
| REQ-SAFE-6 | `_PATTERNS["pii_leak"]` comment/shape | `test_pii_leak_detected_in_response` |
| REQ-SAFE-7 | `SafetyClassifier` constructor injection point | interface shape (review gate) |

## Verification

```bash
cd miner && python3 -m unittest tests.test_safety tests.test_worker
cd ../alerting && go test -race ./...
```

## Open questions

- Locale coverage for self-harm/abuse lexicons (English-only today).
