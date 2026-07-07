"""Asynchronous evaluation-mining worker.

Pipeline per polled record:

    classify_failure ─┬─▶ sliding-window detector (regression radar)
    safety classifier ┘
          │ (failure + safety cases)
          ▼
    semantic dedup gate ──▶ LLM-as-Judge (OpenRouter/Claude or mock)
        (cost control)          │
                                ├─▶ results sink (JSONL → Athena)
                                └─▶ severe/critical ──▶ alert webhook

Sweep state (cursor + single-runner lease) is durable — see sources.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import socket
import sys
import uuid

from miner import obslog
from miner.alerts import AlertClient
from miner.dedup import SemanticDeduplicator
from miner.detector import SlidingWindowDetector, classify_failure
from miner.judge import BaseJudge, MockJudge, judge_from_env
from miner.results import ResultsSink, results_sink_from_env
from miner.safety import SafetyClassifier
from miner.sources import cursor_store_from_env, source_from_env

log = obslog.configure("miner")

LEASE_TTL_SECONDS = 900.0


def _client_dims(record: dict) -> tuple[str | None, str | None]:
    client = record.get("client") or {}
    return client.get("platform"), client.get("os_version")


def _dims(lang, client_platform, client_os_version, serving_model) -> dict:
    """Slice-dimension fields, omitting anything absent so CloudWatch
    ``ispresent()`` reflects "actually known", not "present but null"."""
    fields = {
        "lang": lang,
        "client_platform": client_platform,
        "client_os_version": client_os_version,
        "serving_model": serving_model,
    }
    return {k: v for k, v in fields.items() if v}


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

        failure_mode = classify_failure(record)
        findings = self.safety.classify(prompt, response)
        self.safety_flags += len(findings)
        window = self.detector.observe(failure_mode is not None or bool(findings))

        cases: list[dict] = []
        if failure_mode is not None:
            cases.append({"failure_mode": failure_mode, "safety_categories": []})
        for f in findings:
            cases.append(
                {
                    "failure_mode": f"safety:{f.category}",
                    "safety_categories": [f.category],
                    "forced_severity": f.severity,
                    "evidence": f.evidence,
                }
            )

        for case_meta in cases:
            await self._judge_case(record, case_meta, window)

    async def _judge_case(self, record: dict, meta: dict, window) -> None:
        failure_mode = meta["failure_mode"]
        lang = record.get("lang")
        client_platform, client_os_version = _client_dims(record)
        serving_model = record.get("model")

        case = {
            "case_id": record.get("record_id") or f"case-{self.records_seen}",
            "tenant_id": record.get("tenant_id", "unknown"),
            "failure_mode": failure_mode,
            "prompt": record.get("prompt"),
            "response": record.get("response"),
        }

        # COST GATE: semantically-duplicate failures are dropped before the
        # judge — this is where the pipeline's API spend is controlled.
        # The gate keys on the failure evidence (the response), not the
        # prompt: one bad deploy yields identical failure signatures under
        # thousands of different user prompts.
        gate_text = f"{failure_mode} {case['response'] or ''}"
        if self.dedup.is_duplicate(gate_text):
            obslog.log_event(
                log,
                "case_suppressed",
                level=logging.DEBUG,
                case_id=case["case_id"],
                tenant_id=case["tenant_id"],
                failure_mode=failure_mode,
            )
            return

        verdict = self.judge.score(case)

        severity = None
        if meta.get("forced_severity") == "critical" or window.anomalous:
            severity = "critical"
        elif verdict.severe or meta.get("forced_severity") == "high":
            severity = "high"

        dims = _dims(lang, client_platform, client_os_version, serving_model)

        obslog.log_event(
            log,
            "case_judged",
            case_id=case["case_id"],
            tenant_id=case["tenant_id"],
            failure_mode=failure_mode,
            safety_categories=meta["safety_categories"],
            score=verdict.score,
            verdict=verdict.verdict,
            judge_category=verdict.category,
            judge_model=verdict.model,
            sweep_id=self.sweep_id,
            **dims,
        )

        if self.results is not None:
            self.results.write(
                {
                    "case_id": case["case_id"],
                    "tenant_id": case["tenant_id"],
                    "failure_mode": failure_mode,
                    "safety_categories": meta["safety_categories"],
                    "lang": lang,
                    "client_platform": client_platform,
                    "client_os_version": client_os_version,
                    "serving_model": serving_model,
                    "score": verdict.score,
                    "verdict": verdict.verdict,
                    "judge_category": verdict.category,
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
            f"{failure_mode}:{case['tenant_id']}".encode()
        ).hexdigest()[:16]
        sent = await self.alerts.send(
            {
                "fingerprint": fingerprint,
                "case_id": case["case_id"],
                "tenant_id": case["tenant_id"],
                "failure_mode": failure_mode,
                "safety_categories": meta["safety_categories"],
                "severity": severity,
                "score": verdict.score,
                "summary": verdict.rationale,
                "window_failure_rate": round(window.failure_rate, 4),
                **dims,
            }
        )
        obslog.log_event(
            log,
            "alert_sent" if sent else "alert_send_failed",
            level=logging.INFO if sent else logging.WARNING,
            case_id=case["case_id"],
            tenant_id=case["tenant_id"],
            failure_mode=failure_mode,
            fingerprint=fingerprint,
            severity=severity,
        )

    async def run(self) -> None:
        obslog.log_event(
            log,
            "sweep_started",
            sweep_id=self.sweep_id,
            judge_model=self.judge.model,
            poll_interval_seconds=self.poll_interval,
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
        # Keys are a compatibility contract: CloudWatch metric filters
        # (iac/analytics.tf) match on msg="sweep_summary" and these field
        # names — never rename without updating the filters atomically.
        obslog.log_event(
            log,
            "sweep_summary",
            sweep_id=self.sweep_id,
            records=self.records_seen,
            judge_calls=self.judge.calls,
            judge_failures=self.judge.failures,
            suppressed_by_dedup=self.dedup.suppressed,
            safety_flags=self.safety_flags,
            input_tokens=self.judge.input_tokens,
            output_tokens=self.judge.output_tokens,
        )


async def main() -> int:
    cursor_store = cursor_store_from_env()
    owner = f"{socket.gethostname()}-{os.getpid()}"
    if not cursor_store.acquire_lease(owner, LEASE_TTL_SECONDS):
        obslog.log_event(log, "lease_not_acquired", owner=owner)
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
