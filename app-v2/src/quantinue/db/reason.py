"""Per-score reason payloads persisted as JSONB.

A reason is no longer opaque prose: each key names the score column the
sentence justifies, so a stored judgement can be audited score by score.
"""

from __future__ import annotations

from typing import Final

from pydantic import RootModel

SCORE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sentiment_score",
        "importance",
        "risk_score",
        "confidence",
        "relevance_score",
        "source_trust",
    }
)


class ReasonPayload(RootModel[dict[str, str]]):
    """Validated map from score column name to its stated rationale."""


def reason_payload(**scores: str) -> dict[str, str]:
    """Build a reason map, rejecting keys that are not score columns."""
    unknown = set(scores) - SCORE_KEYS
    if unknown:
        msg = f"unknown score keys: {sorted(unknown)}"
        raise ValueError(msg)
    return dict(scores)
