"""Semantic deduplication gate — the cost-control valve of the pipeline.

Identical production failures repeat in bursts (one bad deploy, thousands of
near-identical traces). Each LLM-as-Judge call costs real money, so before a
case may reach the judge it must pass this gate: a shingle-set Jaccard
similarity check against recently judged cases. Near-duplicates are counted
and dropped, never scored twice.
"""

from __future__ import annotations

import re
import time

_WORD = re.compile(r"[a-z0-9]+")


def shingles(text: str, k: int = 3) -> frozenset[str]:
    """Normalized k-word shingles; robust to whitespace/punctuation jitter."""
    words = _WORD.findall(text.lower())
    if len(words) < k:
        return frozenset([" ".join(words)]) if words else frozenset()
    return frozenset(" ".join(words[i : i + k]) for i in range(len(words) - k + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class SemanticDeduplicator:
    """TTL-bounded fingerprint memory with Jaccard similarity matching."""

    def __init__(
        self,
        threshold: float = 0.8,
        ttl_seconds: float = 3600.0,
        max_fingerprints: int = 5000,
        now: callable = time.monotonic,
    ):
        self.threshold = threshold
        self.ttl = ttl_seconds
        self.max_fingerprints = max_fingerprints
        self.now = now
        self._fingerprints: list[tuple[float, frozenset[str]]] = []
        self.suppressed = 0  # LLM calls avoided — reported as saved spend

    def _evict_expired(self) -> None:
        horizon = self.now() - self.ttl
        self._fingerprints = [
            (ts, fp) for ts, fp in self._fingerprints if ts >= horizon
        ]

    def is_duplicate(self, text: str) -> bool:
        """True (and counted) if `text` is semantically near a recent case.

        Novel cases are fingerprinted and admitted.
        """
        self._evict_expired()
        fp = shingles(text)
        for _, seen in self._fingerprints:
            if jaccard(fp, seen) >= self.threshold:
                self.suppressed += 1
                return True
        self._fingerprints.append((self.now(), fp))
        if len(self._fingerprints) > self.max_fingerprints:
            del self._fingerprints[: -self.max_fingerprints]
        return False
