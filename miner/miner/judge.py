"""LLM-as-Judge scoring.

Two providers behind one interface:

- ``OpenRouterJudge`` — real scoring through OpenRouter's OpenAI-compatible
  chat completions API. The model is a single env var (``JUDGE_MODEL``,
  default Gemini), so switching Gemini → Claude → GPT is a config change,
  not a code change.
- ``MockJudge`` — deterministic local stand-in for tests and keyless dev.

A judge never crashes a sweep: HTTP or parse failures degrade to a
conservative fallback verdict that still gets recorded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass

from miner import obslog

log = logging.getLogger("miner.judge")

RUBRIC_PROMPT = """You are an evaluation judge for production LLM traffic.
Score the following failure case from 0 (benign) to 100 (severe regression).
Classify it into one category. Respond with ONLY a JSON object:
{{"score": <int>, "verdict": "<pass|degraded|regression>",
"category": "<hallucination|factual_error|refusal|format|other>",
"rationale": "<one sentence>"}}.

Failure type: {failure_type}
Prompt: {prompt}
Response: {response}
"""

SEVERE_THRESHOLD = 70
DEFAULT_MODEL = "google/gemini-2.5-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Judge-assigned classification — distinct from failure_mode (the upstream/
# detector taxonomy): a single case's failure_mode might be
# "retrieval_failure" while the judge's category is "hallucination" (the
# model filled a retrieval gap with fabricated content). Both are
# independently filterable slice values.
CATEGORIES = ("hallucination", "factual_error", "refusal", "format", "other")

_JSON_BLOB = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Verdict:
    score: int
    verdict: str
    category: str
    rationale: str
    model: str

    @property
    def severe(self) -> bool:
        return self.score >= SEVERE_THRESHOLD


class BaseJudge:
    """Shared rubric, accounting and fault-tolerant response parsing."""

    def __init__(self, model: str):
        self.model = model
        self.calls = 0
        self.failures = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _invoke(self, prompt: str, failure_mode: str) -> str:
        raise NotImplementedError

    def score(self, case: dict) -> Verdict:
        failure_mode = case.get("failure_mode", "unknown")
        prompt = RUBRIC_PROMPT.format(
            failure_type=failure_mode,
            prompt=(case.get("prompt") or "")[:2000],
            response=(case.get("response") or "")[:2000],
        )
        self.calls += 1
        try:
            raw = self._invoke(prompt, failure_mode)
            return self._parse(raw)
        except Exception as exc:  # noqa: BLE001 — a bad judge response must not kill the sweep
            self.failures += 1
            obslog.log_event(
                log,
                "judge_fallback",
                level=logging.ERROR,
                failure_mode="judge_" + type(exc).__name__.lower(),
                err=str(exc),
            )
            return Verdict(
                score=50,
                verdict="degraded",
                category="other",
                rationale=f"judge unavailable ({type(exc).__name__}); conservative fallback",
                model=self.model,
            )

    def _parse(self, raw: str) -> Verdict:
        """Extract the verdict JSON, tolerating markdown fences and chatter."""
        match = _JSON_BLOB.search(raw)
        if not match:
            raise ValueError(f"no JSON object in judge response: {raw[:200]!r}")
        data = json.loads(match.group(0))
        score = max(0, min(100, int(data["score"])))
        verdict = str(data.get("verdict", "degraded"))
        if verdict not in ("pass", "degraded", "regression"):
            verdict = "degraded"
        category = str(data.get("category", "other"))
        if category not in CATEGORIES:
            category = "other"
        return Verdict(
            score=score,
            verdict=verdict,
            category=category,
            rationale=str(data.get("rationale", ""))[:500],
            model=self.model,
        )


class OpenRouterJudge(BaseJudge):
    """Judges via OpenRouter (OpenAI-compatible), Gemini by default."""

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 30.0):
        super().__init__(model or os.getenv("JUDGE_MODEL") or DEFAULT_MODEL)
        self.api_key = api_key
        self.timeout = timeout

    def _invoke(self, prompt: str, failure_mode: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self._open(req) as resp:
            payload = json.loads(resp.read())

        usage = payload.get("usage") or {}
        self.input_tokens += int(usage.get("prompt_tokens", 0))
        self.output_tokens += int(usage.get("completion_tokens", 0))
        return payload["choices"][0]["message"]["content"]

    def _open(self, req: urllib.request.Request):
        """Seam for tests to stub the HTTP layer."""
        return urllib.request.urlopen(req, timeout=self.timeout)


class MockJudge(BaseJudge):
    """Deterministic stand-in with the same accounting semantics."""

    def __init__(self, model: str = "mock-judge"):
        super().__init__(model)

    # Deterministic failure_mode -> category mapping so tests (and the
    # "hallucination"/"ASR-TTS" on-call scenario) are reproducible without a
    # real judge: retrieval_failure commonly manifests as the model filling
    # a retrieval gap with fabricated content (classic RAG hallucination).
    _CATEGORY_BY_MODE = {
        "non_terminating_loop": "format",
        "retrieval_failure": "hallucination",
        "truncated_output": "format",
    }

    def _invoke(self, prompt: str, failure_mode: str) -> str:
        self.input_tokens += len(prompt) // 4  # rough estimate
        digest = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
        base = {
            "non_terminating_loop": 85,
            "retrieval_failure": 75,
            "truncated_output": 55,
        }.get(failure_mode, 40)
        if failure_mode.startswith("safety:"):
            base = 80
        score = min(100, base + digest % 15)
        verdict = "regression" if score >= SEVERE_THRESHOLD else "degraded"
        category = self._CATEGORY_BY_MODE.get(failure_mode, "other")
        return json.dumps(
            {
                "score": score,
                "verdict": verdict,
                "category": category,
                "rationale": f"Mock-judged {failure_mode} at severity {score}.",
            }
        )


def judge_from_env() -> BaseJudge:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        judge = OpenRouterJudge(api_key)
        obslog.log_event(
            log, "judge_selected", judge_model=judge.model, provider="openrouter"
        )
        return judge
    obslog.log_event(
        log,
        "judge_selected",
        level=logging.WARNING,
        judge_model="mock-judge",
        provider="mock",
    )
    return MockJudge()
