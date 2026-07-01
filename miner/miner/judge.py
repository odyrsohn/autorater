"""LLM-as-Judge scoring (mocked Claude invocation).

The judge receives an extracted failure case and returns a structured
verdict. In production `MockClaudeJudge._invoke` is replaced by a real
`anthropic.Anthropic().messages.create(...)` call with the same rubric
prompt; everything around it (gating, accounting, thresholds) is real.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

RUBRIC_PROMPT = """You are an evaluation judge for production LLM traffic.
Score the following failure case from 0 (benign) to 100 (severe regression).
Respond with JSON: {{"score": <int>, "verdict": "<pass|degraded|regression>",
"rationale": "<one sentence>"}}.

Failure type: {failure_type}
Prompt: {prompt}
Response: {response}
"""

SEVERE_THRESHOLD = 70


@dataclass
class Verdict:
    score: int
    verdict: str
    rationale: str
    model: str

    @property
    def severe(self) -> bool:
        return self.score >= SEVERE_THRESHOLD


class MockClaudeJudge:
    """Deterministic stand-in for the Claude API with call accounting."""

    def __init__(self, model: str = "claude-sonnet-5"):
        self.model = model
        self.calls = 0
        self.input_tokens = 0

    def _invoke(self, prompt: str, failure_type: str) -> str:
        """Mocked messages.create — deterministic, shaped like the real thing."""
        # Severity heuristic: hash spreads scores; known-bad types score high.
        digest = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
        base = {
            "non_terminating_loop": 85,
            "retrieval_failure": 75,
            "truncated_output": 55,
        }.get(failure_type, 40)
        score = min(100, base + digest % 15)
        verdict = "regression" if score >= SEVERE_THRESHOLD else "degraded"
        return json.dumps(
            {
                "score": score,
                "verdict": verdict,
                "rationale": f"Mock-judged {failure_type} at severity {score}.",
            }
        )

    def score(self, case: dict) -> Verdict:
        failure_type = case.get("failure_type", "unknown")
        prompt = RUBRIC_PROMPT.format(
            failure_type=failure_type,
            prompt=(case.get("prompt") or "")[:2000],
            response=(case.get("response") or "")[:2000],
        )
        self.calls += 1
        self.input_tokens += len(prompt) // 4  # rough token estimate

        raw = self._invoke(prompt, failure_type)
        data = json.loads(raw)
        return Verdict(
            score=int(data["score"]),
            verdict=data["verdict"],
            rationale=data["rationale"],
            model=self.model,
        )
