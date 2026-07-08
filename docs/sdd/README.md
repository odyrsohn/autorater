# Spec-Driven Development (SDD) — spec-anchored

Specs under `specs/` are the behavioral source of truth for this repo. Each
spec follows the **spec-anchored** approach: every requirement has a stable
ID and an **Anchors table** mapping it to the exact code and tests that
implement and prove it. Specs describe the system as it IS; a divergence
between spec and code is a bug in one of them.

## How to work spec-first

1. **Behavior change?** Start in the matching `SPEC-*` file: add or amend a
   requirement (never renumber existing IDs; retire with ~~strikethrough~~
   and a note).
2. **Implement**, keeping the anchor paths accurate.
3. **Update the Anchors table** — every requirement row must point at real
   files (and the test that exercises it).
4. **Run the Verification section** of the touched spec before merging.

## Spec format

```
SPEC-NNN-<slug>.md
├── Header: status, owners, last-verified commit
├── Context            why this capability exists
├── Goals / Non-goals
├── Requirements       REQ-<AREA>-NN — testable statements, stable IDs
├── Design notes       only what's needed to understand the requirements
├── Anchors            REQ id → implementation path(s) → test path(s)
├── Verification       exact commands proving the spec holds
└── Open questions
```

## Index

| Spec | Capability | Req prefix |
|---|---|---|
| [SPEC-001](specs/SPEC-001-failure-mining.md) | Failure taxonomy & anomaly detection | `REQ-MINE` |
| [SPEC-002](specs/SPEC-002-cost-gate-and-judge.md) | Dedup cost gate & LLM-as-Judge | `REQ-JUDGE` |
| [SPEC-003](specs/SPEC-003-safety-classification.md) | Safety/abuse classification | `REQ-SAFE` |
| [SPEC-004](specs/SPEC-004-durable-sweep-state.md) | Durable cursor & single-runner lease | `REQ-STATE` |
| [SPEC-005](specs/SPEC-005-alerting-and-analytics.md) | Alert routing & analytics surface | `REQ-OUT` |
