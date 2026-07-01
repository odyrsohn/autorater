"""Ingested-traffic sources the miner polls.

Production runs against the S3 data lake written by the ingestion pipeline;
local development reads JSONL files from a directory. Both yield the same
record dicts and remember a cursor so records are mined exactly once.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterator

log = logging.getLogger("miner.sources")


class LocalDirSource:
    """Polls a directory of JSONL files, tracking processed files by name."""

    def __init__(self, root: str):
        self.root = Path(root)
        self._seen: set[str] = set()

    def poll(self) -> Iterator[dict]:
        for path in sorted(self.root.glob("**/*.jsonl")):
            key = str(path)
            if key in self._seen:
                continue
            self._seen.add(key)
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield json.loads(line)


class S3Source:
    """Polls the ingestion data lake bucket for new objects."""

    def __init__(self, bucket: str, prefix: str = "tenants/"):
        import boto3  # deferred: local mode must not require AWS deps

        self.bucket = bucket
        self.prefix = prefix
        self.client = boto3.client("s3")
        self._seen: set[str] = set()

    def poll(self) -> Iterator[dict]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key in self._seen or not key.endswith(".json"):
                    continue
                self._seen.add(key)
                body = self.client.get_object(Bucket=self.bucket, Key=key)[
                    "Body"
                ].read()
                yield json.loads(body)


def source_from_env():
    bucket = os.getenv("DATA_LAKE_BUCKET")
    if bucket:
        return S3Source(bucket)
    return LocalDirSource(os.getenv("LOCAL_DATA_DIR", "./data"))
