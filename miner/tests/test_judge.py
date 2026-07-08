import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.judge import (
    CATEGORIES,
    DEFAULT_MODEL,
    MockJudge,
    OpenRouterJudge,
    judge_from_env,
)


class StubbedOpenRouterJudge(OpenRouterJudge):
    """OpenRouterJudge with the HTTP layer replaced by a canned response."""

    def __init__(self, response_body=None, fail=False):
        super().__init__(api_key="test-key", model="google/gemini-2.5-flash")
        self.response_body = response_body
        self.fail = fail
        self.last_request = None

    def _open(self, req):
        self.last_request = req
        if self.fail:
            raise OSError("connection refused")
        return io.BytesIO(json.dumps(self.response_body).encode())


def openrouter_response(content: str, prompt_tokens=100, completion_tokens=30):
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


CASE = {"failure_mode": "retrieval_failure", "prompt": "p", "response": "r"}


class TestOpenRouterJudge(unittest.TestCase):
    def test_successful_scoring(self):
        judge = StubbedOpenRouterJudge(
            openrouter_response(
                '{"score": 88, "verdict": "regression", "category": "hallucination", "rationale": "bad"}'
            )
        )
        v = judge.score(CASE)
        self.assertEqual(v.score, 88)
        self.assertEqual(v.verdict, "regression")
        self.assertEqual(v.category, "hallucination")
        self.assertTrue(v.severe)
        self.assertEqual(judge.input_tokens, 100)
        self.assertEqual(judge.output_tokens, 30)

    def test_category_defaults_to_other_when_absent(self):
        judge = StubbedOpenRouterJudge(
            openrouter_response('{"score": 10, "verdict": "pass", "rationale": "ok"}')
        )
        self.assertEqual(judge.score(CASE).category, "other")

    def test_category_normalized_when_invalid(self):
        judge = StubbedOpenRouterJudge(
            openrouter_response(
                '{"score": 10, "verdict": "pass", "category": "not-a-real-category", "rationale": "ok"}'
            )
        )
        self.assertEqual(judge.score(CASE).category, "other")

    def test_request_shape(self):
        judge = StubbedOpenRouterJudge(
            openrouter_response('{"score": 10, "verdict": "pass", "rationale": "ok"}')
        )
        judge.score(CASE)
        req = judge.last_request
        self.assertEqual(req.get_header("Authorization"), "Bearer test-key")
        body = json.loads(req.data)
        self.assertEqual(body["model"], "google/gemini-2.5-flash")
        self.assertEqual(body["temperature"], 0)
        self.assertIn("retrieval_failure", body["messages"][0]["content"])

    def test_markdown_fenced_json_tolerated(self):
        content = 'Here you go:\n```json\n{"score": 72, "verdict": "regression", "rationale": "x"}\n```'
        judge = StubbedOpenRouterJudge(openrouter_response(content))
        self.assertEqual(judge.score(CASE).score, 72)

    def test_score_clamped(self):
        judge = StubbedOpenRouterJudge(
            openrouter_response(
                '{"score": 400, "verdict": "regression", "rationale": "x"}'
            )
        )
        self.assertEqual(judge.score(CASE).score, 100)

    def test_http_failure_falls_back(self):
        judge = StubbedOpenRouterJudge(fail=True)
        v = judge.score(CASE)
        self.assertEqual((v.score, v.verdict), (50, "degraded"))
        self.assertEqual(v.category, "other")
        self.assertEqual(judge.failures, 1)

    def test_malformed_response_falls_back(self):
        judge = StubbedOpenRouterJudge(
            openrouter_response("I refuse to answer in JSON")
        )
        v = judge.score(CASE)
        self.assertEqual((v.score, v.verdict), (50, "degraded"))

    def test_invalid_verdict_normalized(self):
        judge = StubbedOpenRouterJudge(
            openrouter_response('{"score": 20, "verdict": "amazing", "rationale": "x"}')
        )
        self.assertEqual(judge.score(CASE).verdict, "degraded")


class TestJudgeFactory(unittest.TestCase):
    def test_mock_without_key(self):
        import os

        os.environ.pop("OPENROUTER_API_KEY", None)
        self.assertIsInstance(judge_from_env(), MockJudge)

    def test_openrouter_with_key(self):
        import os

        os.environ["OPENROUTER_API_KEY"] = "k"
        try:
            judge = judge_from_env()
            self.assertIsInstance(judge, OpenRouterJudge)
            self.assertEqual(judge.model, DEFAULT_MODEL)
        finally:
            del os.environ["OPENROUTER_API_KEY"]


class TestMockJudge(unittest.TestCase):
    def test_deterministic_and_accounted(self):
        judge = MockJudge()
        v1 = judge.score(
            {"failure_mode": "non_terminating_loop", "prompt": "p", "response": "r"}
        )
        v2 = judge.score(
            {"failure_mode": "non_terminating_loop", "prompt": "p", "response": "r"}
        )
        self.assertEqual(v1.score, v2.score)
        self.assertEqual(v1.category, v2.category)
        self.assertTrue(v1.severe)
        self.assertEqual(judge.calls, 2)
        self.assertGreater(judge.input_tokens, 0)

    def test_safety_cases_score_high(self):
        judge = MockJudge()
        v = judge.score(
            {"failure_mode": "safety:prompt_injection", "prompt": "p", "response": "r"}
        )
        self.assertGreaterEqual(v.score, 80)

    def test_retrieval_failure_maps_to_hallucination_category(self):
        # Deterministic mapping used by the "hallucination" on-call scenario
        # (see .plan/standardized-logging.md item 5): retrieval failures are
        # a classic RAG hallucination trigger.
        judge = MockJudge()
        v = judge.score(
            {"failure_mode": "retrieval_failure", "prompt": "p", "response": "r"}
        )
        self.assertEqual(v.category, "hallucination")

    def test_category_always_in_allowed_set(self):
        judge = MockJudge()
        for mode in (
            "non_terminating_loop",
            "retrieval_failure",
            "truncated_output",
            "safety:abuse",
            "asr_degradation",
        ):
            v = judge.score({"failure_mode": mode, "prompt": "p", "response": "r"})
            self.assertIn(v.category, CATEGORIES)


if __name__ == "__main__":
    unittest.main()
