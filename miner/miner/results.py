"""Results sink: every judged case lands in queryable storage.

Records are buffered per sweep and flushed as one JSONL object under a
date-partitioned key (``results/dt=YYYY-MM-DD/<sweep_id>.jsonl``) — the
layout the Glue table's partition projection expects, making the whole
history queryable in Athena with zero crawlers.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("miner.results")


class ResultsSink:
    """Buffering base; subclasses implement the flush destination."""

    def __init__(self) -> None:
        self._buffer: list[dict] = []
        self.flushed = 0

    def write(self, record: dict) -> None:
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self._buffer.append(record)

    def flush(self, sweep_id: str) -> str | None:
        """Persist the buffered records; returns the destination or None."""
        if not self._buffer:
            return None
        dt = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"results/dt={dt}/{sweep_id}.jsonl"
        body = "\n".join(json.dumps(r) for r in self._buffer) + "\n"
        dest = self._store(key, body)
        self.flushed += len(self._buffer)
        self._buffer.clear()
        log.info("flushed results to %s", dest)
        return dest

    def _store(self, key: str, body: str) -> str:
        raise NotImplementedError


class LocalResultsSink(ResultsSink):
    def __init__(self, root: str = "./results"):
        super().__init__()
        self.root = Path(root)

    def _store(self, key: str, body: str) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return str(path)


class S3ResultsSink(ResultsSink):
    def __init__(self, bucket: str):
        import boto3  # deferred: local mode must not require AWS deps

        super().__init__()
        self.bucket = bucket
        self.client = boto3.client("s3")

    def _store(self, key: str, body: str) -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        return f"s3://{self.bucket}/{key}"


def results_sink_from_env() -> ResultsSink:
    bucket = os.getenv("RESULTS_BUCKET")
    if bucket:
        return S3ResultsSink(bucket)
    return LocalResultsSink(os.getenv("LOCAL_RESULTS_DIR", "./results"))
