"""Failure extraction and sliding-window anomaly detection.

The detector answers two questions:
1. Is this individual record a runtime failure worth judging? (extraction)
2. Is the *rate* of failures anomalous versus the recent baseline? (window)

An anomaly escalates alert severity — a lone retrieval miss is mining input,
a spike of them is a prompt/model regression.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

# --- per-record failure extraction -----------------------------------------

RETRIEVAL_MARKERS = (
    "no relevant documents",
    "no results found",
    "unable to retrieve",
    "context is empty",
)

LOOP_MIN_REPEATS = 5
LOOP_CHUNK = 24


def detect_repetition(text: str) -> bool:
    """Cheap non-termination heuristic: a chunk repeating LOOP_MIN_REPEATS+
    times back-to-back indicates a degenerate generation loop."""
    n = len(text)
    if n < LOOP_CHUNK * LOOP_MIN_REPEATS:
        return False
    for start in range(0, n - LOOP_CHUNK * LOOP_MIN_REPEATS + 1, LOOP_CHUNK):
        chunk = text[start : start + LOOP_CHUNK]
        if chunk.strip() and text.count(chunk) >= LOOP_MIN_REPEATS:
            return True
    return False


def classify_failure(record: dict) -> str | None:
    """Return a failure type for the record, or None if it looks healthy."""
    if record.get("error_type"):
        return str(record["error_type"])

    response = (record.get("response") or "").lower()
    if record.get("retrieved_docs") == [] or any(
        m in response for m in RETRIEVAL_MARKERS
    ):
        return "retrieval_failure"
    if detect_repetition(record.get("response") or ""):
        return "non_terminating_loop"
    if record.get("finish_reason") == "max_tokens" and not response.rstrip().endswith(
        (".", "!", "?", "```")
    ):
        return "truncated_output"
    return None


# --- sliding-window anomaly detection ---------------------------------------


@dataclass
class WindowVerdict:
    anomalous: bool
    failure_rate: float
    baseline_rate: float
    z_score: float


class SlidingWindowDetector:
    """Failure-rate anomaly detection over a time-bounded sliding window.

    Keeps (timestamp, is_failure) events for `window_seconds`; the current
    window's failure rate is compared against an exponentially-weighted
    baseline of previous windows. A z-score above `sigma` (with at least
    `min_events` observed) flags a regression.
    """

    def __init__(
        self,
        window_seconds: float = 300.0,
        min_events: int = 20,
        sigma: float = 3.0,
        now: callable = time.monotonic,
    ):
        self.window_seconds = window_seconds
        self.min_events = min_events
        self.sigma = sigma
        self.now = now
        self.events: deque[tuple[float, bool]] = deque()
        # EWMA baseline of failure rate and its variance.
        self._baseline: float | None = None
        self._variance = 0.0
        self._alpha = 0.2

    def _evict(self) -> None:
        horizon = self.now() - self.window_seconds
        while self.events and self.events[0][0] < horizon:
            ts, failed = self.events.popleft()
            # Evicted events feed the baseline: the past window becomes history.
            rate = 1.0 if failed else 0.0
            if self._baseline is None:
                self._baseline = rate
            else:
                delta = rate - self._baseline
                self._baseline += self._alpha * delta
                self._variance = (1 - self._alpha) * (
                    self._variance + self._alpha * delta * delta
                )

    def observe(self, is_failure: bool) -> WindowVerdict:
        self._evict()
        self.events.append((self.now(), is_failure))

        total = len(self.events)
        failures = sum(1 for _, f in self.events if f)
        rate = failures / total

        baseline = self._baseline if self._baseline is not None else 0.0
        std = math.sqrt(self._variance) if self._variance > 0 else 0.0
        if std > 0:
            z = (rate - baseline) / std
        else:
            z = math.inf if rate > baseline else 0.0

        anomalous = total >= self.min_events and rate > baseline and z >= self.sigma
        return WindowVerdict(anomalous, rate, baseline, z)
