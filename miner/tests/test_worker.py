import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.alerts import AlertClient
from miner.judge import MockClaudeJudge
from miner.sources import LocalDirSource
from miner.worker import MiningWorker


class FakeAlerts(AlertClient):
    def __init__(self):
        super().__init__("http://unused")
        self.payloads = []

    async def send(self, payload):
        self.payloads.append(payload)
        return True


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


LOOP_RESPONSE = "I will check the database. " * 30


class TestPipelineCostGate(unittest.TestCase):
    def test_duplicates_never_reach_judge(self):
        worker = MiningWorker(source=None, alerts=FakeAlerts())
        record = {
            "tenant_id": "acme",
            "prompt": "what is the tire pressure spec",
            "response": LOOP_RESPONSE,
        }

        async def scenario():
            for _ in range(10):
                await worker.process_record(dict(record))

        run(scenario())
        self.assertEqual(worker.judge.calls, 1, "dedup gate must cap judge calls at 1")
        self.assertEqual(worker.dedup.suppressed, 9)

    def test_healthy_records_bypass_gate_and_judge(self):
        worker = MiningWorker(source=None, alerts=FakeAlerts())

        async def scenario():
            await worker.process_record(
                {"tenant_id": "a", "prompt": "hi", "response": "Hello there."}
            )

        run(scenario())
        self.assertEqual(worker.judge.calls, 0)

    def test_severe_case_fires_alert(self):
        alerts = FakeAlerts()
        worker = MiningWorker(source=None, alerts=alerts)
        record = {
            "record_id": "r-1",
            "tenant_id": "acme",
            "prompt": "route to depot",
            "response": LOOP_RESPONSE,  # loops score >= 85 in the mock judge
        }
        run(worker.process_record(record))
        self.assertEqual(len(alerts.payloads), 1)
        payload = alerts.payloads[0]
        self.assertEqual(payload["failure_type"], "non_terminating_loop")
        self.assertIn(payload["severity"], ("high", "critical"))
        self.assertTrue(payload["fingerprint"])


class TestJudgeAccounting(unittest.TestCase):
    def test_calls_and_tokens_counted(self):
        judge = MockClaudeJudge()
        v = judge.score(
            {"failure_type": "non_terminating_loop", "prompt": "p", "response": "r"}
        )
        self.assertTrue(v.severe)
        self.assertEqual(judge.calls, 1)
        self.assertGreater(judge.input_tokens, 0)


class TestLocalDirSource(unittest.TestCase):
    def test_files_polled_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "traffic.jsonl"
            f.write_text(
                json.dumps({"tenant_id": "a", "prompt": "x", "response": "y"}) + "\n"
            )
            src = LocalDirSource(tmp)
            self.assertEqual(len(list(src.poll())), 1)
            self.assertEqual(len(list(src.poll())), 0, "same file must not be re-mined")


if __name__ == "__main__":
    unittest.main()
