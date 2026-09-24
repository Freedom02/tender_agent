"""Shared boundary for the tender matching-stage handoff."""
from __future__ import annotations


# The matching stage carries all three review inputs together. Rejection is a
# risk check only and must never query enterprise evidence.
MATCH_STAGE_TARGETS = ("technical", "qualification", "rejection")
ENTERPRISE_MATCH_TARGETS = ("technical", "qualification")
RISK_CHECK_TARGETS = ("rejection",)


__all__ = [
    "ENTERPRISE_MATCH_TARGETS",
    "MATCH_STAGE_TARGETS",
    "RISK_CHECK_TARGETS",
]
