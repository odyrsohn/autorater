"""Asynchronous evaluation-mining worker.

Pipeline per polled record:

    classify_failure ─┬─▶ sliding-window detector (regression radar)
    safety classifier ┘
          │ (failure + safety cases)
          ▼
    semantic dedup gate ──▶ LLM-as-Judge (OpenRouter/Gemini or mock)
        (cost control)          │
                                ├─▶ results sink (JSONL → Athena)
                                └─▶ severe/critical ──▶ alert webhook

Sweep state (cursor + single-runner lease) is durable — see sources.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import socket
import sys
import uuid

from miner.alerts import AlertClient
from miner.dedup import SemanticDeduplicator
from miner.detector import SlidingWindowDetector, classify_failure
from miner.judge import BaseJudge, MockJudge, judge_from_env
from miner.results import ResultsSink, results_sink_from_env
from miner.safety import SafetyClassifier
from miner.sources import cursor_store_from_env, source_from_env

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("miner")

LEASE_TTL_SECONDS = 900.0


class MiningWorker:
    def __init__(
        self,
        source,
        alerts: AlertClient,
        judge: BaseJudge | None = None,
        results: ResultsSink | None = None,
        poll_interval: float = 10.0,
    ):
        self.source = source
        self.alerts = alerts
        self.judge = judge or MockJudge()
        self.results = results
        self.poll_interval = poll_interval
        self.detector = SlidingWindowDetector()
        self.dedup = SemanticDeduplicator()
        self.safety = SafetyClassifier()
        self.sweep_id = uuid.uuid4().hex[:12]
        self.records_seen = 0
        self.safety_flags = 0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def process_record(self, record: dict) -> None:
        self.records_seen += 1
        prompt, response = record.get("prompt"), record.get("response")

        failure_type = classify_failure(record)
        findings = self.safety.classify(prompt, response)
        self.safety_flags += len(findings)
        window = self.detector.observe(failure_type is not None or bool(findings))

        cases: list[dict] = []
        if failure_type is not None:
            cases.append({"failure_type": failure_type, "safety_categories": []})
        for f in findings:
            cases.append(
                {
                    "failure_type": f"safety:{f.category}",
                    "safety_categories": [f.category],
                    "forced_severity": f.severity,
                    "evidence": f.evidence,
                }
            )

        for case_meta in cases:
            await self._judge_case(record, case_meta, window)

    async def _judge_case(self, record: dict, meta: dict, window) -> None:
        failure_type = meta["failure_type"]
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

        severity = None
        if meta.get("forced_severity") == "critical" or window.anomalous:
            severity = "critical"
        elif verdict.severe or meta.get("forced_severity") == "high":
            severity = "high"

        if self.results is not None:
            self.results.write(
                {
                    "case_id": case["case_id"],
                    "tenant_id": case["tenant_id"],
                    "failure_type": failure_type,
                    "safety_categories": meta["safety_categories"],
                    "score": verdict.score,
                    "verdict": verdict.verdict,
                    "rationale": verdict.rationale,
                    "model": verdict.model,
                    "window_failure_rate": round(window.failure_rate, 4),
                    "alerted": severity is not None,
                    "sweep_id": self.sweep_id,
                }
            )

        if severity is None:
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
                "safety_categories": meta["safety_categories"],
                "severity": severity,
                "score": verdict.score,
                "summary": verdict.rationale,
                "window_failure_rate": round(window.failure_rate, 4),
            }
        )

    async def run(self) -> None:
        log.info(
            "mining worker started sweep=%s judge=%s (poll every %.0fs)",
            self.sweep_id,
            self.judge.model,
            self.poll_interval,
        )
        while not self._stop.is_set():
            for record in self.source.poll():
                await self.process_record(record)
            if self.results is not None:
                self.results.flush(self.sweep_id)
            self.report()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue

    def report(self) -> None:
        stats = {
            "metric": "miner_stats",
            "sweep_id": self.sweep_id,
            "records": self.records_seen,
            "judge_calls": self.judge.calls,
            "judge_failures": self.judge.failures,
            "suppressed_by_dedup": self.dedup.suppressed,
            "safety_flags": self.safety_flags,
            "input_tokens": self.judge.input_tokens,
            "output_tokens": self.judge.output_tokens,
        }
        # Pure-JSON line on stdout: CloudWatch metric filters parse this.
        print(json.dumps(stats), flush=True)


async def main() -> int:
    cursor_store = cursor_store_from_env()
    owner = f"{socket.gethostname()}-{os.getpid()}"
    if not cursor_store.acquire_lease(owner, LEASE_TTL_SECONDS):
        log.info("another miner holds the lease; exiting cleanly")
        return 0

    try:
        webhook = os.getenv("ALERT_WEBHOOK_URL", "http://localhost:8070/v1/alerts")
        worker = MiningWorker(
            source=source_from_env(cursor_store),
            alerts=AlertClient(webhook),
            judge=judge_from_env(),
            results=results_sink_from_env(),
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, worker.stop)

        await worker.run()
        worker.report()
    finally:
        cursor_store.release_lease(owner)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
