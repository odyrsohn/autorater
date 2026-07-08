"""Azure adapter tests — fakes implement the small adapter seams
(_AzureTableAdapter / _AzureContainerAdapter interfaces), so no Azure SDK
is needed, mirroring the FakeS3Client pattern in test_sources.py."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import miner.results as results
import miner.sources as sources
from miner.azure_sources import BlobSource, TableCursorStore, _row_key


class FakeTable:
    """Dict-backed stand-in for _AzureTableAdapter with ETag semantics."""

    def __init__(self):
        self.entities = {}  # (partition, row) -> entity dict
        self._etag = 0

    def _stamp(self, entity):
        self._etag += 1
        entity["odata.etag"] = f"W/{self._etag}"
        return entity

    def get(self, partition, row):
        e = self.entities.get((partition, row))
        return dict(e) if e else None

    def upsert(self, entity):
        key = (entity["PartitionKey"], entity["RowKey"])
        self.entities[key] = self._stamp(dict(entity))

    def create(self, entity):
        key = (entity["PartitionKey"], entity["RowKey"])
        if key in self.entities:
            return False
        self.entities[key] = self._stamp(dict(entity))
        return True

    def update_if_match(self, entity, etag):
        key = (entity["PartitionKey"], entity["RowKey"])
        current = self.entities.get(key)
        if current is None or current["odata.etag"] != etag:
            return False
        self.entities[key] = self._stamp(dict(entity))
        return True

    def delete_if_match(self, partition, row, etag):
        current = self.entities.get((partition, row))
        if current is None or current["odata.etag"] != etag:
            return False
        del self.entities[(partition, row)]
        return True


class TestTableCursorStore(unittest.TestCase):
    def test_cursor_roundtrip_survives_new_store(self):
        table = FakeTable()
        TableCursorStore(table=table).set(
            "https://lake/tenants/", "tenants/a/2026/x.json"
        )
        # Fresh store instance == fresh Container Apps Job run.
        self.assertEqual(
            TableCursorStore(table=table).get("https://lake/tenants/"),
            "tenants/a/2026/x.json",
        )

    def test_row_key_is_url_safe(self):
        key = _row_key("https://acct.blob.core.windows.net/data-lake/tenants/")
        for forbidden in ("/", "\\", "#", "?"):
            self.assertNotIn(forbidden, key)

    def test_lease_blocks_second_owner(self):
        table = FakeTable()
        a, b = TableCursorStore(table=table), TableCursorStore(table=table)
        self.assertTrue(a.acquire_lease("job-1", ttl_seconds=60))
        self.assertFalse(b.acquire_lease("job-2", ttl_seconds=60))

    def test_expired_lease_taken_over(self):
        table = FakeTable()
        store = TableCursorStore(table=table)
        self.assertTrue(store.acquire_lease("job-1", ttl_seconds=-1))  # already expired
        self.assertTrue(
            TableCursorStore(table=table).acquire_lease("job-2", ttl_seconds=60)
        )

    def test_lease_reentrant_for_same_owner(self):
        table = FakeTable()
        store = TableCursorStore(table=table)
        self.assertTrue(store.acquire_lease("job-1", ttl_seconds=60))
        self.assertTrue(store.acquire_lease("job-1", ttl_seconds=60))

    def test_release_then_reacquire(self):
        table = FakeTable()
        store = TableCursorStore(table=table)
        store.acquire_lease("job-1", ttl_seconds=60)
        store.release_lease("job-1")
        self.assertTrue(
            TableCursorStore(table=table).acquire_lease("job-2", ttl_seconds=60)
        )

    def test_release_by_non_owner_is_noop(self):
        table = FakeTable()
        store = TableCursorStore(table=table)
        store.acquire_lease("job-1", ttl_seconds=60)
        store.release_lease("job-2")  # not the owner
        self.assertFalse(
            TableCursorStore(table=table).acquire_lease("job-3", ttl_seconds=60)
        )


class FakeContainer:
    """Dict-backed stand-in for _AzureContainerAdapter."""

    def __init__(self, blobs: dict[str, dict]):
        self.blobs = blobs
        self.list_calls = 0

    def list_names(self, prefix):
        self.list_calls += 1
        for name in sorted(self.blobs):
            if name.startswith(prefix):
                yield name

    def download(self, name):
        return json.dumps(self.blobs[name]).encode()


BLOBS = {
    "tenants/acme/2026/07/01/a.json": {"record_id": "r1"},
    "tenants/acme/2026/07/02/b.json": {"record_id": "r2"},
    "tenants/acme/notes.txt": {"record_id": "ignored"},
}


def make_blob_source(container, store):
    return BlobSource(
        account_url="https://lake.blob.core.windows.net",
        cursor_store=store,
        container=container,
    )


class TestBlobSourceResume(unittest.TestCase):
    def test_first_sweep_processes_all_and_advances_cursor(self):
        store = sources.MemoryCursorStore()
        src = make_blob_source(FakeContainer(dict(BLOBS)), store)
        ids = [r["record_id"] for r in src.poll()]
        self.assertEqual(ids, ["r1", "r2"])

    def test_second_sweep_skips_processed(self):
        store = sources.MemoryCursorStore()
        make_blob_source(FakeContainer(dict(BLOBS)), store).poll().__iter__()
        list(make_blob_source(FakeContainer(dict(BLOBS)), store).poll())
        self.assertEqual(
            list(make_blob_source(FakeContainer(dict(BLOBS)), store).poll()), []
        )

    def test_partial_crash_resumes_at_least_once(self):
        store = sources.MemoryCursorStore()
        gen = make_blob_source(FakeContainer(dict(BLOBS)), store).poll()
        next(gen)  # r1 handed to the worker
        next(gen)  # worker finished r1 (cursor advances past it), r2 in flight
        gen.close()

        ids = [
            r["record_id"]
            for r in make_blob_source(FakeContainer(dict(BLOBS)), store).poll()
        ]
        self.assertEqual(ids, ["r2"])


class FakeResultsContainer:
    def __init__(self):
        self.uploads = {}

    def upload_blob(self, name, data, overwrite, content_type):
        self.uploads[name] = data


class TestBlobResultsSink(unittest.TestCase):
    def test_flush_writes_partitioned_jsonl(self):
        client = FakeResultsContainer()
        sink = results.BlobResultsSink("https://x", "results", container_client=client)
        sink.write({"case_id": "c1", "score": 90})
        dest = sink.flush("sweep123")

        self.assertRegex(
            dest, r"^results/results/dt=\d{4}-\d{2}-\d{2}/sweep123\.jsonl$"
        )
        ((name, data),) = client.uploads.items()
        self.assertEqual(json.loads(data.decode().strip())["case_id"], "c1")


class TestFactorySelection(unittest.TestCase):
    def test_azure_provider_selects_azure_impls(self):
        env = {
            "CLOUD_PROVIDER": "azure",
            "CURSOR_TABLE_ENDPOINT": "https://acct.table.core.windows.net",
            "CURSOR_TABLE_NAME": "minerstate",
            "DATA_LAKE_ACCOUNT_URL": "https://lake.blob.core.windows.net",
            "RESULTS_ACCOUNT_URL": "https://res.blob.core.windows.net",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch("miner.azure_sources.TableCursorStore") as table_cls:
                sources.cursor_store_from_env()
            table_cls.assert_called_once()

            with mock.patch("miner.azure_sources.BlobSource") as blob_cls:
                sources.source_from_env(sources.MemoryCursorStore())
            blob_cls.assert_called_once()

            with mock.patch.object(results, "BlobResultsSink") as sink_cls:
                results.results_sink_from_env()
            sink_cls.assert_called_once()

    def test_aws_provider_selects_aws_impls(self):
        env = {
            "CLOUD_PROVIDER": "aws",
            "CURSOR_TABLE": "t",
            "DATA_LAKE_BUCKET": "b",
            "RESULTS_BUCKET": "r",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch.object(sources, "DynamoCursorStore") as dyn_cls:
                sources.cursor_store_from_env()
            dyn_cls.assert_called_once_with("t")

            with mock.patch.object(sources, "S3Source") as s3_cls:
                sources.source_from_env(sources.MemoryCursorStore())
            s3_cls.assert_called_once()

            with mock.patch.object(results, "S3ResultsSink") as sink_cls:
                results.results_sink_from_env()
            sink_cls.assert_called_once_with("r")

    def test_unknown_provider_rejected(self):
        with mock.patch.dict("os.environ", {"CLOUD_PROVIDER": "gcp"}, clear=False):
            with self.assertRaises(ValueError):
                sources.cursor_store_from_env()
            with self.assertRaises(ValueError):
                results.results_sink_from_env()

    def test_unset_provider_keeps_local_fallbacks(self):
        import os

        cleaned = {
            k: os.environ.pop(k, None)
            for k in (
                "CLOUD_PROVIDER",
                "CURSOR_TABLE",
                "DATA_LAKE_BUCKET",
                "RESULTS_BUCKET",
            )
        }
        try:
            self.assertIsInstance(
                sources.cursor_store_from_env(), sources.FileCursorStore
            )
            self.assertIsInstance(
                sources.source_from_env(sources.MemoryCursorStore()),
                sources.LocalDirSource,
            )
            self.assertIsInstance(
                results.results_sink_from_env(), results.LocalResultsSink
            )
        finally:
            for k, v in cleaned.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
