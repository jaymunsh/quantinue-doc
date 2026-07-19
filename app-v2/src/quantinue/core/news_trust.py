"""Source grading: which outlets may influence a decision, and how much.

Grading happens before any model call so an untrusted outlet costs no tokens
and, more importantly, cannot smuggle its framing into a judgement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from pathlib import Path

Grade = Literal["allow", "gray", "block"]
DEFAULT_TRUST_SCORES: dict[str, float] = {"allow": 0.95, "gray": 0.50, "block": 0.0}


def registrable_domain(url: str) -> str:
    """Return the host without a leading www, or an empty string when unusable."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


class NewsTrustPolicy(BaseModel):
    """Domain-to-grade mapping with the trust score each grade carries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=1, ge=1)
    default_grade: Grade = "gray"
    trust_scores: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_TRUST_SCORES))
    domains: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    def grade_for(self, url: str) -> Grade:
        """Return the grade for one article URL, matching subdomains too."""
        host = registrable_domain(url)
        if not host:
            return self.default_grade
        for grade in ("block", "allow", "gray"):
            for domain in self.domains.get(grade, ()):
                normalized = domain.lower().removeprefix("www.")
                if host == normalized or host.endswith(f".{normalized}"):
                    return grade  # type: ignore[return-value]
        return self.default_grade

    def trust_for(self, url: str) -> float:
        """Return the numeric trust the strategist should attribute to a source."""
        grade = self.grade_for(url)
        return self.trust_scores.get(grade, DEFAULT_TRUST_SCORES.get(grade, 0.0))

    def is_blocked(self, url: str) -> bool:
        """Return whether this source must be dropped before reaching a model."""
        return self.grade_for(url) == "block"


def load_news_trust_policy(path: Path) -> NewsTrustPolicy:
    """Load the grading policy; an absent file yields conservative defaults."""
    if not path.exists():
        return NewsTrustPolicy()
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    return NewsTrustPolicy.model_validate(document)
