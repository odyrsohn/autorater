"""Webhook client for the Go alerting engine."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from miner import obslog

log = logging.getLogger("miner.alerts")


class AlertClient:
    """Posts severe-regression alerts to the alerting engine webhook."""

    def __init__(self, webhook_url: str, timeout: float = 5.0):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.sent = 0

    def _post(self, payload: dict) -> int:
        req = urllib.request.Request(
            self.webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.status

    async def send(self, payload: dict) -> bool:
        """Fire the webhook without blocking the event loop."""
        try:
            status = await asyncio.get_running_loop().run_in_executor(
                None, self._post, payload
            )
        except Exception as exc:  # noqa: BLE001 — alerting must never kill mining
            obslog.log_event(
                log,
                "alert_webhook_failed",
                level=logging.ERROR,
                failure_mode="webhook_io",
                err=str(exc),
            )
            return False
        self.sent += 1
        return 200 <= status < 300
