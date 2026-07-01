"""Asynchronous evaluation-mining worker.

Pipeline per polled record:

    classify_failure ──▶ sliding-window detector (regression radar)
          │
          ▼ (failures only)
    semantic dedup gate ──▶ LLM-as-Judge (mock Claude) ──▶ severe? ──▶ webhook
        (cost control: near-duplicate failures never reach the judge)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal

from miner.alerts import AlertClient
from miner.dedup import SemanticDeduplicator
from miner.detector import SlidingWindowDetector, classify_failure
from miner.judge import MockClaudeJudge
from miner.sources import source_from_env

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("miner")


class MiningWorker:
    def __init__(self, source, alerts: AlertClient, poll_interval: float = 10.0):
        self.source = source
        self.alerts = alerts
        self.poll_interval = poll_interval
        self.detector = SlidingWindowDetector()
        self.dedup = SemanticDeduplicator()
        self.judge = MockClaudeJudge()
        self.records_seen = 0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def process_record(self, record: dict) -> None:
        self.records_seen += 1
        failure_type = classify_failure(record)
        window = self.detector.observe(failure_type is not None)
        if failure_type is None:
            return

        case = {
            "case_id": record.get("record_id") or f"case-{self.records_seen}",
            "tenant_id": record.get("tenant_id", "unknown"),
            "failure_type": failure_type,
            "prompt": record.get("prompt"),
            "response": record.get("response"),
        }

        # COST GATE: semantically-duplicate failures are dropped before the
        # judge — this is where the pipeline's API spend is controlled.
        # The gate keys on the failure evidence (the response), not the
        # prompt: one bad deploy yields identical failure signatures under
        # thousands of different user prompts.
        gate_text = f"{failure_type} {case['response'] or ''}"
        if self.dedup.is_duplicate(gate_text):
            log.debug("dedup gate suppressed case %s", case["case_id"])
            return

        verdict = self.judge.score(case)
        log.info(
            "judged case=%s type=%s score=%d verdict=%s",
            case["case_id"],
            failure_type,
            verdict.score,
            verdict.verdict,
        )
        if not (verdict.severe or window.anomalous):
            return

        fingerprint = hashlib.sha256(
            f"{failure_type}:{case['tenant_id']}".encode()
        ).hexdigest()[:16]
        await self.alerts.send(
            {
                "fingerprint": fingerprint,
                "case_id": case["case_id"],
                "tenant_id": case["tenant_id"],
                "failure_type": failure_type,
                "severity": "critical" if window.anomalous else "high",
                "score": verdict.score,
                "summary": verdict.rationale,
                "window_failure_rate": round(window.failure_rate, 4),
            }
        )

    async def run(self) -> None:
        log.info("mining worker started (poll every %.0fs)", self.poll_interval)
        while not self._stop.is_set():
            for record in self.source.poll():
                await self.process_record(record)
            self.report()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue

    def report(self) -> None:
        log.info(
            "stats: records=%d judge_calls=%d suppressed_by_dedup=%d est_input_tokens=%d",
            self.records_seen,
            self.judge.calls,
            self.dedup.suppressed,
            self.judge.input_tokens,
        )


async def main() -> None:
    webhook = os.getenv("ALERT_WEBHOOK_URL", "http://localhost:8070/v1/alerts")
    worker = MiningWorker(source_from_env(), AlertClient(webhook))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)

    await worker.run()
    worker.report()


if __name__ == "__main__":
    asyncio.run(main())
