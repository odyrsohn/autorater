import io
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner import obslog
from miner.results import LocalResultsSink


class TestLocalResultsSink(unittest.TestCase):
    def test_flush_writes_partitioned_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = LocalResultsSink(tmp)
            sink.write({"case_id": "c1", "score": 90})
            sink.write({"case_id": "c2", "score": 40})
            dest = sink.flush("sweep123")

            self.assertIsNotNone(dest)
            self.assertRegex(dest, r"results/dt=\d{4}-\d{2}-\d{2}/sweep123\.jsonl$")
            lines = Path(dest).read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)
            rec = json.loads(lines[0])
            self.assertEqual(rec["case_id"], "c1")
            self.assertIn("ts", rec)  # timestamp auto-attached
            self.assertEqual(sink.flushed, 2)

    def test_empty_flush_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = LocalResultsSink(tmp)
            self.assertIsNone(sink.flush("sweep123"))

    def test_buffer_clears_between_sweeps(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = LocalResultsSink(tmp)
            sink.write({"case_id": "c1"})
            first = sink.flush("s1")
            sink.write({"case_id": "c2"})
            second = sink.flush("s2")
            self.assertEqual(len(Path(first).read_text().strip().splitlines()), 1)
            self.assertEqual(len(Path(second).read_text().strip().splitlines()), 1)

    def test_flush_emits_structured_event(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(obslog.JsonFormatter("miner"))
        logging.getLogger().handlers = [handler]
        logging.getLogger().setLevel(logging.INFO)

        with tempfile.TemporaryDirectory() as tmp:
            sink = LocalResultsSink(tmp)
            sink.write({"case_id": "c1"})
            sink.write({"case_id": "c2"})
            dest = sink.flush("s1")

        rec = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(rec["msg"], "results_flushed")
        self.assertEqual(rec["sweep_id"], "s1")
        self.assertEqual(rec["records"], 2)
        self.assertEqual(rec["destination"], dest)


if __name__ == "__main__":
    unittest.main()
