import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.dedup import SemanticDeduplicator, jaccard, shingles


class TestShingles(unittest.TestCase):
    def test_normalization(self):
        a = shingles("Retrieval FAILED for query: weather!")
        b = shingles("retrieval failed for query weather")
        self.assertEqual(a, b)

    def test_jaccard_bounds(self):
        a, b = (
            shingles("alpha beta gamma delta"),
            shingles("totally different words here"),
        )
        self.assertEqual(jaccard(a, a), 1.0)
        self.assertLess(jaccard(a, b), 0.1)


class TestDeduplicator(unittest.TestCase):
    def test_near_duplicates_suppressed(self):
        d = SemanticDeduplicator(threshold=0.8)
        base = "retrieval failure: no relevant documents for query about brake sensor calibration"
        self.assertFalse(d.is_duplicate(base))
        self.assertTrue(d.is_duplicate(base))  # exact repeat
        self.assertTrue(d.is_duplicate(base + " again"))  # near repeat
        self.assertEqual(d.suppressed, 2)

    def test_novel_cases_admitted(self):
        d = SemanticDeduplicator(threshold=0.8)
        self.assertFalse(
            d.is_duplicate("retrieval failure on brake sensor telemetry query")
        )
        self.assertFalse(
            d.is_duplicate("model loops forever repeating the same apology sentence")
        )
        self.assertEqual(d.suppressed, 0)

    def test_ttl_expiry_readmits(self):
        clock = [0.0]
        d = SemanticDeduplicator(threshold=0.8, ttl_seconds=60, now=lambda: clock[0])
        text = "retrieval failure: empty context for user query"
        self.assertFalse(d.is_duplicate(text))
        clock[0] = 61.0
        self.assertFalse(d.is_duplicate(text), "expired fingerprint must not suppress")

    def test_fingerprint_cap(self):
        d = SemanticDeduplicator(max_fingerprints=10)
        for i in range(50):
            d.is_duplicate(
                f"unique failure case number {i} with distinct content {i * 7}"
            )
        self.assertLessEqual(len(d._fingerprints), 10)


if __name__ == "__main__":
    unittest.main()
