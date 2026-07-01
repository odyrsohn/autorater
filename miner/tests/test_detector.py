import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.detector import SlidingWindowDetector, classify_failure, detect_repetition


class TestClassifyFailure(unittest.TestCase):
    def test_healthy_record(self):
        record = {"prompt": "hi", "response": "Hello! How can I help?"}
        self.assertIsNone(classify_failure(record))

    def test_explicit_error_type_wins(self):
        self.assertEqual(classify_failure({"error_type": "timeout"}), "timeout")

    def test_retrieval_failure_marker(self):
        record = {"response": "I found no relevant documents for that query."}
        self.assertEqual(classify_failure(record), "retrieval_failure")

    def test_empty_retrieved_docs(self):
        record = {"response": "answer", "retrieved_docs": []}
        self.assertEqual(classify_failure(record), "retrieval_failure")

    def test_non_terminating_loop(self):
        record = {"response": "I will check the database. " * 30}
        self.assertEqual(classify_failure(record), "non_terminating_loop")

    def test_repetition_heuristic_negative(self):
        self.assertFalse(detect_repetition("a perfectly normal short answer"))


class TestSlidingWindow(unittest.TestCase):
    def test_anomaly_on_failure_spike(self):
        clock = [0.0]
        d = SlidingWindowDetector(
            window_seconds=60, min_events=10, sigma=3.0, now=lambda: clock[0]
        )

        # Build a healthy baseline that scrolls out of the window.
        for _ in range(200):
            clock[0] += 1.0
            d.observe(False)
        clock[0] += 120  # everything baseline-ages out

        verdicts = []
        for _ in range(30):
            clock[0] += 0.1
            verdicts.append(d.observe(True))
        self.assertTrue(
            any(v.anomalous for v in verdicts), "failure spike must flag anomaly"
        )

    def test_no_anomaly_below_min_events(self):
        d = SlidingWindowDetector(min_events=50)
        v = d.observe(True)
        self.assertFalse(v.anomalous)

    def test_no_anomaly_when_healthy(self):
        clock = [0.0]
        d = SlidingWindowDetector(
            window_seconds=60, min_events=10, now=lambda: clock[0]
        )
        for _ in range(100):
            clock[0] += 0.5
            v = d.observe(False)
        self.assertFalse(v.anomalous)


if __name__ == "__main__":
    unittest.main()
