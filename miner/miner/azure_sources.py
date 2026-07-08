"""Azure implementations of the miner's durable-state and source seams —
translations of DynamoCursorStore (→ Table Storage) and S3Source (→ Blob).

Azure SDKs are imported lazily inside the default adapters (the boto3
pattern); tests inject fakes implementing the small adapter interfaces, so
neither cloud's SDK is needed locally. Auth is DefaultAzureCredential
(managed identity in Container Apps Jobs, `az login` locally).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Iterator


class _AzureTableAdapter:
    """Thin wrapper over azure-data-tables translating SDK exceptions into
    plain return values — the seam fakes implement in tests."""

    def __init__(self, endpoint: str, table_name: str):
        from azure.data.tables import TableServiceClient  # deferred
        from azure.identity import DefaultAzureCredential  # deferred

        service = TableServiceClient(
            endpoint=endpoint, credential=DefaultAzureCredential()
        )
        self.client = service.get_table_client(table_name)

    def get(self, partition: str, row: str) -> dict | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return dict(self.client.get_entity(partition_key=partition, row_key=row))
        except ResourceNotFoundError:
            return None

    def upsert(self, entity: dict) -> None:
        self.client.upsert_entity(entity)

    def create(self, entity: dict) -> bool:
        """Insert-if-absent; False when the entity already exists — the
        conditional-write primitive the lease relies on (≙ DynamoDB
        attribute_not_exists)."""
        from azure.core.exceptions import ResourceExistsError

        try:
            self.client.create_entity(entity)
            return True
        except ResourceExistsError:
            return False

    def update_if_match(self, entity: dict, etag: str) -> bool:
        """Replace only if the entity is unchanged (ETag optimistic
        concurrency ≙ DynamoDB ConditionExpression)."""
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError
        from azure.data.tables import UpdateMode

        try:
            self.client.update_entity(
                entity,
                mode=UpdateMode.REPLACE,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
            return True
        except ResourceModifiedError:
            return False

    def delete_if_match(self, partition: str, row: str, etag: str) -> bool:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError

        try:
            self.client.delete_entity(
                partition_key=partition,
                row_key=row,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
            return True
        except ResourceModifiedError:
            return False


def _row_key(key: str) -> str:
    """Table RowKeys forbid '/', '\\', '#', '?' — cursor keys are URLs, so
    they're hashed; the original key is kept as an entity field."""
    return hashlib.sha256(key.encode()).hexdigest()[:32]


class TableCursorStore:
    """Azure Table Storage cursor + single-runner lease — the translation of
    DynamoCursorStore, with identical semantics: the lease is a conditional
    insert (create-if-absent), takeover requires the expiry to have passed,
    and contention is resolved by ETag-matched updates."""

    def __init__(self, endpoint: str = "", table_name: str = "", table=None):
        self.table = (
            table if table is not None else _AzureTableAdapter(endpoint, table_name)
        )

    def get(self, key: str) -> str | None:
        entity = self.table.get("cursor", _row_key(key))
        return entity["value"] if entity else None

    def set(self, key: str, value: str) -> None:
        self.table.upsert(
            {
                "PartitionKey": "cursor",
                "RowKey": _row_key(key),
                "value": value,
                "source": key,
            }
        )

    def acquire_lease(self, owner: str, ttl_seconds: float) -> bool:
        now = int(time.time())
        fresh = {
            "PartitionKey": "lease",
            "RowKey": "miner",
            "owner": owner,
            "expiry": now + int(ttl_seconds),
        }
        if self.table.create(fresh):
            return True

        current = self.table.get("lease", "miner")
        if current is None:  # raced with a release — retry the insert once
            return self.table.create(fresh)
        if int(current["expiry"]) > now and current["owner"] != owner:
            return False
        # Expired (or re-entrant) — take over, guarded by ETag so two
        # takeover attempts can't both win.
        fresh["odata.etag"] = current.get("odata.etag", "")
        return self.table.update_if_match(fresh, etag=current.get("odata.etag", ""))

    def release_lease(self, owner: str) -> None:
        current = self.table.get("lease", "miner")
        if current and current["owner"] == owner:
            self.table.delete_if_match(
                "lease", "miner", etag=current.get("odata.etag", "")
            )


class _AzureContainerAdapter:
    """Thin wrapper over azure-storage-blob for BlobSource/tests."""

    def __init__(self, account_url: str, container: str):
        from azure.identity import DefaultAzureCredential  # deferred
        from azure.storage.blob import BlobServiceClient  # deferred

        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        self.client = service.get_container_client(container)

    def list_names(self, prefix: str) -> Iterator[str]:
        for item in self.client.list_blobs(name_starts_with=prefix):
            yield item.name

    def download(self, name: str) -> bytes:
        return self.client.download_blob(name).readall()


class BlobSource:
    """Polls the ingestion data lake in Azure — the translation of S3Source.

    Documented gap (docs/cloud-portability.md): Blob listing has no
    StartAfter parameter, so resume filters the (lexicographically ordered)
    listing client-side — same correctness, listing cost is O(prefix) rather
    than O(unprocessed). Cursor semantics are identical: advance only after
    the consumer finished a record (at-least-once).
    """

    def __init__(
        self,
        account_url: str,
        cursor_store,
        prefix: str = "tenants/",
        container=None,
        container_name: str = "data-lake",
    ):
        self.prefix = prefix
        self.store = cursor_store
        self.cursor_key = f"{account_url.rstrip('/')}/{container_name}/{prefix}"
        self.client = (
            container
            if container is not None
            else _AzureContainerAdapter(account_url, container_name)
        )

    def poll(self) -> Iterator[dict]:
        cursor = self.store.get(self.cursor_key) or ""
        for name in self.client.list_names(self.prefix):
            if name <= cursor or not name.endswith(".json"):
                continue
            yield json.loads(self.client.download(name))
            # Advance only after the consumer finished with the record.
            self.store.set(self.cursor_key, name)
