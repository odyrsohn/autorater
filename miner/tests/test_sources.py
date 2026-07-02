import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.sources import FileCursorStore, LocalDirSource, MemoryCursorStore, S3Source


class TestFileCursorStore(unittest.TestCase):
    def test_cursor_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "cursor.json")
            FileCursorStore(path).set("src", "tenants/a/2026/07/02/x.json")
            # Fresh instance == fresh process.
            self.assertEqual(
                FileCursorStore(path).get("src"), "tenants/a/2026/07/02/x.json"
            )

    def test_lease_blocks_second_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "cursor.json")
            store = FileCursorStore(path)
            self.assertTrue(store.acquire_lease("task-1", ttl_seconds=60))
            self.assertFalse(
                FileCursorStore(path).acquire_lease("task-2", ttl_seconds=60)
            )
            store.release_lease("task-1")
            self.assertTrue(
                FileCursorStore(path).acquire_lease("task-2", ttl_seconds=60)
            )

    def test_expired_lease_taken_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileCursorStore(str(Path(tmp) / "cursor.json"))
            self.assertTrue(
                store.acquire_lease("task-1", ttl_seconds=-1)
            )  # already expired
            self.assertTrue(store.acquire_lease("task-2", ttl_seconds=60))

    def test_lease_reentrant_for_same_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileCursorStore(str(Path(tmp) / "cursor.json"))
            self.assertTrue(store.acquire_lease("task-1", ttl_seconds=60))
            self.assertTrue(store.acquire_lease("task-1", ttl_seconds=60))


class TestLocalDirSourceResume(unittest.TestCase):
    def test_restart_does_not_remine(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            (data / "a.jsonl").write_text(json.dumps({"record_id": "r1"}) + "\n")
            store = MemoryCursorStore()

            first = list(LocalDirSource(str(data), store).poll())
            # New source instance == fresh Fargate task, same durable store.
            second = list(LocalDirSource(str(data), store).poll())
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 0)

    def test_new_files_after_cursor_are_mined(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            (data / "a.jsonl").write_text(json.dumps({"record_id": "r1"}) + "\n")
            store = MemoryCursorStore()
            list(LocalDirSource(str(data), store).poll())

            (data / "b.jsonl").write_text(json.dumps({"record_id": "r2"}) + "\n")
            records = list(LocalDirSource(str(data), store).poll())
            self.assertEqual([r["record_id"] for r in records], ["r2"])


class FakeS3Client:
    """Just enough of the S3 API for S3Source: pagination + get_object."""

    def __init__(self, objects: dict[str, dict]):
        self.objects = objects
        self.list_calls = []

    def get_paginator(self, _name):
        client = self

        class Paginator:
            def paginate(self, Bucket, Prefix, StartAfter):
                client.list_calls.append(StartAfter)
                keys = sorted(
                    k for k in client.objects if k.startswith(Prefix) and k > StartAfter
                )
                yield {"Contents": [{"Key": k} for k in keys]}

        return Paginator()

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(json.dumps(self.objects[Key]).encode())}


def make_s3_source(objects, store):
    src = S3Source.__new__(S3Source)  # skip __init__ (boto3 client)
    src.bucket = "lake"
    src.prefix = "tenants/"
    src.store = store
    src.cursor_key = "s3://lake/tenants/"
    src.client = FakeS3Client(objects)
    return src


class TestS3SourceResume(unittest.TestCase):
    OBJECTS = {
        "tenants/acme/2026/07/01/a.json": {"record_id": "r1"},
        "tenants/acme/2026/07/02/b.json": {"record_id": "r2"},
        "tenants/acme/notes.txt": {"record_id": "ignored"},
    }

    def test_first_sweep_processes_all_and_advances_cursor(self):
        store = MemoryCursorStore()
        src = make_s3_source(dict(self.OBJECTS), store)
        ids = [r["record_id"] for r in src.poll()]
        self.assertEqual(ids, ["r1", "r2"])
        self.assertEqual(
            store.get("s3://lake/tenants/"), "tenants/acme/2026/07/02/b.json"
        )

    def test_second_sweep_lists_after_cursor(self):
        store = MemoryCursorStore()
        src = make_s3_source(dict(self.OBJECTS), store)
        list(src.poll())

        src2 = make_s3_source(dict(self.OBJECTS), store)
        self.assertEqual(list(src2.poll()), [])
        # The whole point: the restart listed from the cursor, not from "".
        self.assertEqual(src2.client.list_calls, ["tenants/acme/2026/07/02/b.json"])

    def test_partial_crash_resumes_at_least_once(self):
        store = MemoryCursorStore()
        src = make_s3_source(dict(self.OBJECTS), store)
        gen = src.poll()
        next(gen)  # r1 handed to the worker
        next(gen)  # worker finished r1 (cursor advances past it), r2 in flight
        gen.close()  # crash before r2 completes

        # Resume: fully-processed r1 is never re-mined; in-flight r2 is
        # re-delivered exactly once (at-least-once semantics).
        src2 = make_s3_source(dict(self.OBJECTS), store)
        ids = [r["record_id"] for r in src2.poll()]
        self.assertEqual(ids, ["r2"])


if __name__ == "__main__":
    unittest.main()
