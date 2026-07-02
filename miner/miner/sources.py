"""Ingested-traffic sources and durable sweep state.

The miner runs as a scheduled Fargate task, so nothing in-process survives
between sweeps. Two pieces of state are durable:

- **cursor** — the last processed object key per source. Listing resumes
  with ``StartAfter=<cursor>`` instead of re-reading the whole bucket.
  Keys are date-ordered (``tenants/<t>/YYYY/MM/DD/<uuid>.json``) so
  lexicographic resume is chronological; late-arriving objects with
  lexically earlier keys (backfills) are skipped by design.
- **lease** — a short-lived lock so overlapping task launches can't
  double-process the same range.

Production uses DynamoDB (conditional writes); local runs use a JSON file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Iterator, Protocol

log = logging.getLogger("miner.sources")

LEASE_KEY = "lease#miner"


class CursorStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def acquire_lease(self, owner: str, ttl_seconds: float) -> bool: ...
    def release_lease(self, owner: str) -> None: ...


class MemoryCursorStore:
    """In-memory store for tests."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lease: tuple[str, float] | None = None

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def acquire_lease(self, owner: str, ttl_seconds: float) -> bool:
        now = time.time()
        if self._lease and self._lease[1] > now and self._lease[0] != owner:
            return False
        self._lease = (owner, now + ttl_seconds)
        return True

    def release_lease(self, owner: str) -> None:
        if self._lease and self._lease[0] == owner:
            self._lease = None


class FileCursorStore:
    """JSON-file store for local/dev runs; written atomically."""

    def __init__(self, path: str):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"cursors": {}, "lease": None}
        return json.loads(self.path.read_text())

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.path)

    def get(self, key: str) -> str | None:
        return self._load()["cursors"].get(key)

    def set(self, key: str, value: str) -> None:
        state = self._load()
        state["cursors"][key] = value
        self._save(state)

    def acquire_lease(self, owner: str, ttl_seconds: float) -> bool:
        state = self._load()
        lease = state.get("lease")
        now = time.time()
        if lease and lease["expiry"] > now and lease["owner"] != owner:
            return False
        state["lease"] = {"owner": owner, "expiry": now + ttl_seconds}
        self._save(state)
        return True

    def release_lease(self, owner: str) -> None:
        state = self._load()
        if state.get("lease") and state["lease"]["owner"] == owner:
            state["lease"] = None
            self._save(state)


class DynamoCursorStore:
    """DynamoDB-backed store: survives Fargate restarts, and the conditional
    lease write guarantees a single active miner even if EventBridge fires
    while a slow sweep is still running."""

    def __init__(self, table_name: str):
        import boto3  # deferred: local mode must not require AWS deps

        self.table = boto3.resource("dynamodb").Table(table_name)

    def get(self, key: str) -> str | None:
        item = self.table.get_item(Key={"pk": f"cursor#{key}"}).get("Item")
        return item["value"] if item else None

    def set(self, key: str, value: str) -> None:
        self.table.put_item(Item={"pk": f"cursor#{key}", "value": value})

    def acquire_lease(self, owner: str, ttl_seconds: float) -> bool:
        from botocore.exceptions import ClientError

        now = int(time.time())
        try:
            self.table.put_item(
                Item={
                    "pk": LEASE_KEY,
                    "owner": owner,
                    "expiry": now + int(ttl_seconds),
                },
                ConditionExpression="attribute_not_exists(pk) OR expiry < :now OR #o = :owner",
                ExpressionAttributeNames={"#o": "owner"},
                ExpressionAttributeValues={":now": now, ":owner": owner},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release_lease(self, owner: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.table.delete_item(
                Key={"pk": LEASE_KEY},
                ConditionExpression="#o = :owner",
                ExpressionAttributeNames={"#o": "owner"},
                ExpressionAttributeValues={":owner": owner},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise


class LocalDirSource:
    """Polls a directory of JSONL files in path order, resuming from the
    durable cursor so restarts never re-mine processed files."""

    def __init__(self, root: str, cursor_store: CursorStore):
        self.root = Path(root)
        self.store = cursor_store
        self.cursor_key = f"localdir:{self.root}"

    def poll(self) -> Iterator[dict]:
        cursor = self.store.get(self.cursor_key) or ""
        for path in sorted(self.root.glob("**/*.jsonl")):
            key = str(path)
            if key <= cursor:
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield json.loads(line)
            self.store.set(self.cursor_key, key)


class S3Source:
    """Polls the ingestion data lake, resuming from the durable cursor via
    ListObjectsV2 StartAfter — a restart lists only unprocessed keys instead
    of the whole bucket."""

    def __init__(
        self, bucket: str, cursor_store: CursorStore, prefix: str = "tenants/"
    ):
        import boto3  # deferred: local mode must not require AWS deps

        self.bucket = bucket
        self.prefix = prefix
        self.store = cursor_store
        self.cursor_key = f"s3://{bucket}/{prefix}"
        self.client = boto3.client("s3")

    def poll(self) -> Iterator[dict]:
        cursor = self.store.get(self.cursor_key) or ""
        paginator = self.client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self.bucket, Prefix=self.prefix, StartAfter=cursor
        )
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".json"):
                    continue
                body = self.client.get_object(Bucket=self.bucket, Key=key)[
                    "Body"
                ].read()
                yield json.loads(body)
                # Advance only after the consumer finished with the record:
                # a crash mid-record re-mines exactly that record (at-least-once).
                self.store.set(self.cursor_key, key)


def cursor_store_from_env() -> CursorStore:
    table = os.getenv("CURSOR_TABLE")
    if table:
        return DynamoCursorStore(table)
    return FileCursorStore(os.getenv("CURSOR_FILE", "./.miner-cursor.json"))


def source_from_env(cursor_store: CursorStore):
    bucket = os.getenv("DATA_LAKE_BUCKET")
    if bucket:
        return S3Source(bucket, cursor_store)
    return LocalDirSource(os.getenv("LOCAL_DATA_DIR", "./data"), cursor_store)
