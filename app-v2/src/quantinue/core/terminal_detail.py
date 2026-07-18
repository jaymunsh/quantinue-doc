"""Bounded redacted detail retained with each terminal pipeline run."""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from quantinue.market_data.models import NewsMatchReason, NewsMatchStatus

DISPLAY_REFERENCE_MAX_LENGTH: Final = 4_096
DisplayTitle = Annotated[str, StringConstraints(max_length=200)]
DisplayText = Annotated[str, StringConstraints(max_length=1_000)]
DisplaySource = Annotated[str, StringConstraints(max_length=120)]
DisplayReference = Annotated[
    str,
    StringConstraints(max_length=DISPLAY_REFERENCE_MAX_LENGTH, strip_whitespace=False),
]
DisplayDecision = Annotated[str, StringConstraints(max_length=64)]
DisplayBlocker = Annotated[str, StringConstraints(max_length=240)]
IDENTITY_ERROR: Final = "news_selection_identity"
PARTITION_ERROR: Final = "news_selection_partition"
REPRESENTATIVE_ERROR: Final = "news_selection_representative"


class RedactedDetailModel(BaseModel):
    """Strict immutable base for administrator-safe terminal detail."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)


class CollectionFact(RedactedDetailModel):
    """One bounded collection fact suitable for administrator display."""

    title: DisplayTitle = ""
    summary: DisplayText = ""
    source: DisplaySource = ""
    reference: DisplayReference = ""
    score: float | None = Field(default=None, ge=0.0, le=1.0)


class StrategyDetail(RedactedDetailModel):
    """Bounded strategist result without model inputs or provider payloads."""

    proposal: DisplayDecision = ""
    rationale: DisplayText = ""
    gate: DisplayDecision = ""
    blockers: tuple[DisplayBlocker, ...] = Field(default=(), max_length=12)
    conviction: float | None = Field(default=None, ge=0.0, le=1.0)


class CriticDetail(RedactedDetailModel):
    """Bounded critic verdict without exception or provider detail."""

    verdict: DisplayDecision = ""
    rationale: DisplayText = ""
    layer: DisplayDecision = ""


class NewsSelectionDetailItem(RedactedDetailModel):
    """Typed display-safe record for one fetched Role 06 item."""

    status: NewsMatchStatus
    is_representative: bool
    score: int = Field(ge=0)
    reasons: tuple[NewsMatchReason | str, ...] = ()
    relevance_evaluated: bool = True
    representative_label: DisplayDecision = ""
    representative_explanation: DisplayText = ""
    title: str = ""
    published_at: str = ""
    reference: DisplayReference = ""

    @model_validator(mode="after")
    def _representative_matches_status(self) -> NewsSelectionDetailItem:
        if self.is_representative != (self.status is NewsMatchStatus.SELECTED):
            raise PydanticCustomError(
                IDENTITY_ERROR,
                "representative identity and selected status must match",
            )
        return self


class NewsSelectionDetail(RedactedDetailModel):
    """Complete Role 06 item collection retained without string encoding."""

    items: tuple[NewsSelectionDetailItem, ...] = ()

    @model_validator(mode="after")
    def _forms_exact_partition(self) -> NewsSelectionDetail:
        if any(item.status is NewsMatchStatus.FETCHED for item in self.items):
            raise PydanticCustomError(
                PARTITION_ERROR,
                "display rows must be relevant or excluded",
            )
        if sum(item.is_representative for item in self.items) > 1:
            raise PydanticCustomError(
                REPRESENTATIVE_ERROR,
                "at most one representative row is allowed",
            )
        return self


class RoleDetail(RedactedDetailModel):
    """Bounded, structured result retained for one of the eleven roles.

    This is deliberately a display projection rather than a provider payload:
    it retains the values used to make the pipeline decision, but never prompts,
    response envelopes, credentials, or raw exception text.
    """

    component: DisplayDecision
    title: DisplayTitle
    status: DisplayDecision = "pending"
    summary: DisplayText = ""
    facts: tuple[tuple[DisplayTitle, DisplayText], ...] = Field(default=(), max_length=24)
    items: tuple[str, ...] = ()
    news_selection: NewsSelectionDetail | None = None


class TerminalRunDetail(RedactedDetailModel):
    """Safe, structured 01--11 detail with legacy-safe empty placeholders."""

    disclosure: CollectionFact = Field(default_factory=CollectionFact)
    news: CollectionFact = Field(default_factory=CollectionFact)
    strategy: StrategyDetail = Field(default_factory=StrategyDetail)
    critic: CriticDetail = Field(default_factory=CriticDetail)
    roles: tuple[RoleDetail, ...] = Field(default=(), max_length=11)
