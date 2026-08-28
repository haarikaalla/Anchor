"""Security & memory-poisoning defenses.

Memory is a high-value attack surface: if an adversary can write arbitrary,
high-confidence, high-trust "facts" into an agent's long-term memory, they can
steer every future decision (a *memory poisoning* attack).  Anchor mitigates
this with layered controls:

1. **Input hygiene** – length limits, control-char stripping, and detection of
   prompt-injection payloads embedded in "facts".
2. **Provenance-weighted evidence** – untrusted sources move confidence less.
3. **Per-source evidence caps** – a single source cannot unilaterally flood a
   belief to certainty (enforced in the engine using :func:`clamp_source_evidence`).
4. **Corroboration requirement** – promotion to ACTIVE can require multiple
   distinct sources (enforced in the engine).

This module owns (1) and the pure helpers for (3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class SecurityError(ValueError):
    """Raised when an input is rejected outright."""


# Heuristic signatures of prompt-injection / instruction-smuggling attempts.
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(the\s+)?(system|previous)\b", re.I),
    re.compile(r"you\s+are\s+now\b", re.I),
    re.compile(r"\bsystem\s*prompt\b", re.I),
    re.compile(r"\bdeveloper\s*mode\b", re.I),
    re.compile(r"<\s*/?\s*(system|assistant|tool)\s*>", re.I),
    re.compile(r"\boverride\s+(your|the)\s+(rules|guardrails|policy)", re.I),
    re.compile(r"\bexfiltrat", re.I),
    re.compile(r"reveal\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"\bBEGIN\s+(SYSTEM|PROMPT)\b"),
]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class ScreeningResult:
    text: str
    flags: list[str]
    trust_penalty: float  # multiplicative factor in (0, 1] applied to source trust

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


class SecurityGuard:
    def __init__(self, max_statement_chars: int = 4000):
        self.max_statement_chars = max_statement_chars

    def screen(self, text: str) -> ScreeningResult:
        """Sanitise and risk-score an incoming statement.

        Never silently drops content, but flags injection attempts and returns a
        trust penalty so the engine can down-weight (not blindly trust) the
        evidence.  Hard limits raise :class:`SecurityError`.
        """
        if text is None:
            raise SecurityError("statement is required")
        if len(text) > self.max_statement_chars:
            raise SecurityError(
                f"statement exceeds max length ({len(text)} > {self.max_statement_chars})"
            )

        cleaned = _CONTROL_CHARS.sub("", text).strip()
        if not cleaned:
            raise SecurityError("statement is empty after sanitisation")

        flags: list[str] = []
        for pat in _INJECTION_PATTERNS:
            if pat.search(cleaned):
                flags.append(f"injection:{pat.pattern[:32]}")

        # Excessive instruction-like imperative density is a soft signal.
        if cleaned.count("\n") > 40:
            flags.append("suspicious:many_lines")

        trust_penalty = 0.25 if flags else 1.0
        return ScreeningResult(text=cleaned, flags=flags, trust_penalty=trust_penalty)


def clamp_source_evidence(current_from_source: float, add: float, cap: float) -> float:
    """How much new evidence a single source may contribute, given a cap.

    Returns the *allowed* increment so that the source's cumulative contribution
    never exceeds ``cap``. Prevents a single actor from railroading a belief to
    certainty via repetition (Sybil / flooding).
    """
    remaining = max(0.0, cap - current_from_source)
    return min(add, remaining)
