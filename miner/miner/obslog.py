"""Canonical structured logging for the miner (same envelope contract as the
other two repos — see .plan/standardized-logging.md).

Single-line JSON: ``time``, ``level``, ``msg`` (a stable snake_case event
name), ``service``, ``env``, plus whatever slice-dimension fields the
caller passes to :func:`log_event` (``tenant_id``, ``failure_mode``,
``lang``, ``client_platform``, ``client_os_version``, ``serving_model``,
``case_id``, ``sweep_id``, ...).

Event names and field keys are a compatibility contract: CloudWatch metric
filters, saved Logs Insights queries and the Athena results schema match
on them. Never log prompt/response content.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service
        self.env = os.getenv("APP_ENV", "dev")

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "service": self.service,
            "env": self.env,
        }
        payload.update(getattr(record, "envelope", {}))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(service: str, level: int = logging.INFO) -> logging.Logger:
    """Route the root logger through the JSON formatter for `service`."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    return logging.getLogger(service)


def log_event(
    logger: logging.Logger, msg: str, *, level: int = logging.INFO, **fields
) -> None:
    """Emit one envelope-shaped log line.

    ``msg`` is the stable event name (e.g. ``case_judged``); ``fields``
    become top-level envelope keys — never pass prompt/response content.
    """
    logger.log(level, msg, extra={"envelope": fields})
