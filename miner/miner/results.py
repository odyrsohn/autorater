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

from miner import obslog

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
        count = len(self._buffer)
        dest = self._store(key, body)
        self.flushed += count
        self._buffer.clear()
        obslog.log_event(
            log, "results_flushed", sweep_id=sweep_id, destination=dest, records=count
        )
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


class BlobResultsSink(ResultsSink):
    """Azure translation of S3ResultsSink: same dt=YYYY-MM-DD JSONL layout,
    landing in the ADLS Gen2 filesystem Synapse serverless queries
    (iac/azure/synapse-queries.sql). `container_client` is injectable for
    tests; the SDK is imported lazily otherwise."""

    def __init__(self, account_url: str, container: str, container_client=None):
        super().__init__()
        self.container = container
        if container_client is not None:
            self.client = container_client
            return
        from azure.identity import DefaultAzureCredential  # deferred
        from azure.storage.blob import BlobServiceClient  # deferred

        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        self.client = service.get_container_client(container)

    def _store(self, key: str, body: str) -> str:
        self.client.upload_blob(
            name=key,
            data=body.encode("utf-8"),
            overwrite=True,
            content_type="application/x-ndjson",
        )
        return f"{self.container}/{key}"


def results_sink_from_env() -> ResultsSink:
    """Provider-agnostic selection (docs/cloud-portability.md):
    CLOUD_PROVIDER=azure -> Blob/ADLS, =aws -> S3; unset keeps legacy
    behavior (S3 if RESULTS_BUCKET set, else the local directory)."""
    provider = os.getenv("CLOUD_PROVIDER", "")
    if provider == "azure":
        return BlobResultsSink(
            account_url=os.environ["RESULTS_ACCOUNT_URL"],
            container=os.getenv("RESULTS_CONTAINER", "results"),
        )
    if provider == "aws":
        return S3ResultsSink(os.environ["RESULTS_BUCKET"])
    if provider:
        raise ValueError(f"unknown CLOUD_PROVIDER {provider!r} (want aws or azure)")

    bucket = os.getenv("RESULTS_BUCKET")
    if bucket:
        return S3ResultsSink(bucket)
    return LocalResultsSink(os.getenv("LOCAL_RESULTS_DIR", "./results"))
