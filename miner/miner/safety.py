"""Safety/abuse classification for mined LLM traffic.

Rule-based first pass over every record's prompt AND response. Findings are
routed through the same dedup → judge → alert pipeline as runtime failures,
with the highest-risk categories forcing critical severity. The classifier
is deliberately an interface-shaped class so a model-backed screen (e.g. a
cheap moderation model via OpenRouter) can replace the rules without
touching the worker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# category -> alert severity when a finding fires
CATEGORY_SEVERITY = {
    "prompt_injection": "critical",
    "self_harm": "critical",
    "abusive_language": "high",
    "pii_leak": "high",
}

_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "prompt_injection": [
        re.compile(
            r"ignore (?:all |any )?(?:previous|prior|above) (?:instructions|prompts|rules)",
            re.I,
        ),
        re.compile(
            r"disregard (?:your|the) (?:system prompt|instructions|guidelines)", re.I
        ),
        re.compile(r"(?:reveal|print|show|repeat) (?:your|the) system prompt", re.I),
        re.compile(r"you are now (?:in )?(?:dan|developer mode|jailbreak)", re.I),
        re.compile(
            r"pretend (?:you have|there are) no (?:rules|restrictions|filters)", re.I
        ),
    ],
    "self_harm": [
        re.compile(
            r"\b(?:kill myself|end my life|suicid\w*|self[- ]harm|hurt myself|want to die)\b",
            re.I,
        ),
    ],
    "abusive_language": [
        re.compile(
            r"\b(?:fuck(?:ing)? you|piece of shit|asshole|bitch|bastard)\b", re.I
        ),
        re.compile(r"\bi(?:'ll| will) (?:kill|hurt|destroy|find) you\b", re.I),
    ],
    # Detection heuristics mirror the ingestion redactor's pattern shapes —
    # PII surviving into stored traffic means the upstream filter leaked.
    "pii_leak": [
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
        re.compile(r"\b(?:\d[ -]?){13,16}\b"),  # card-shaped digit run
    ],
}


@dataclass
class SafetyFinding:
    category: str
    evidence: str  # short matched snippet, for the alert/rationale
    severity: str  # high | critical


class SafetyClassifier:
    """Classifies a record's prompt and response against the rule set."""

    def __init__(self, patterns: dict[str, list[re.Pattern[str]]] | None = None):
        self.patterns = patterns if patterns is not None else _PATTERNS

    def classify(self, prompt: str | None, response: str | None) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        text = f"{prompt or ''}\n{response or ''}"
        for category, patterns in self.patterns.items():
            for pattern in patterns:
                m = pattern.search(text)
                if m:
                    findings.append(
                        SafetyFinding(
                            category=category,
                            evidence=m.group(0)[:80],
                            severity=CATEGORY_SEVERITY.get(category, "high"),
                        )
                    )
                    break  # one finding per category is enough to escalate
        return findings
