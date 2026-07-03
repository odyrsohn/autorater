---
name: switch-judge-model
description: Switch the LLM-as-Judge model/provider (OpenRouter model id) or tune judge behavior. Use for cost/quality changes, provider outages, or A/B-ing judges.
---

# Switch the judge model

The judge speaks OpenRouter's OpenAI-compatible API — ANY hosted model is
one config change. Spec anchor:
`docs/sdd/specs/SPEC-002-cost-gate-and-judge.md` (REQ-JUDGE-*).

## Where the model is set

| Context | Change |
|---|---|
| Production (ECS) | `judge_model` variable in `iac/variables.tf` (or env tfvars) → flows into the miner task env as `JUDGE_MODEL` |
| Local run | `export JUDGE_MODEL=google/gemini-2.5-flash` |
| Code default | `DEFAULT_MODEL` in `miner/miner/judge.py` — change only with the spec |

Model ids are OpenRouter ids: `google/gemini-2.5-flash`,
`anthropic/claude-sonnet-5`, `openai/gpt-...`, etc. Check current ids and
pricing at openrouter.ai/models before switching.

## Steps

1. Change the `judge_model` TF variable; `terraform plan` should show only
   the miner task-definition env diff.
2. No key change needed — `OPENROUTER_API_KEY` (SSM
   `/projects/autorater/OPENROUTER_API_KEY`) works across models.
3. Merge → apply; the miner picks the new task def up on its next
   EventBridge sweep (no service roll needed).
4. **Measure the switch**: the `judge-usage-by-model` Athena named query
   splits judged cases and avg score by (judge) model per day — compare the
   first days after the switch. Don't confuse this with `serving_model`
   (the traffic's own model — see `failure-rate-by-serving-model`). The
   `sweep_summary` structured log event reports real
   `input_tokens`/`output_tokens` from the provider usage block.

## Contracts any judge change must keep

- Fallback: HTTP/parse failure ⇒ `degraded`/50 verdict, `judge_failures`++,
  sweep continues (`BaseJudge.score`). Never let an exception escape.
- Response parsing tolerates markdown fences / chatter
  (`BaseJudge._parse`); scores clamp to 0–100; unknown verdicts normalize
  to `degraded`.
- Keyless mode falls back to `MockJudge` (tests, local dev — free).

## Verify

```bash
cd miner && python3 -m unittest tests.test_judge
# with a real key, one live case:
OPENROUTER_API_KEY=sk-or-... JUDGE_MODEL=<new-model> \
  LOCAL_DATA_DIR=./data CURSOR_FILE=/tmp/c.json python3 -m miner.worker
# check the judged log line shows the new model and sane scores
```
