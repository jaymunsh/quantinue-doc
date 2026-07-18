"""Canonical logical vocabularies persisted as PostgreSQL TEXT."""

from enum import StrEnum, unique
from typing import Final, Literal, TypeAlias

EventType: TypeAlias = Literal[
    "earnings",
    "guidance_change",
    "ma",
    "capital_raise",
    "buyback",
    "management_change",
    "insider_trade",
    "product_deal",
    "analyst_rating",
    "regulation_legal",
    "delisting_halt",
    "other",
]
EVENT_TYPES: Final[tuple[EventType, ...]] = (
    "earnings",
    "guidance_change",
    "ma",
    "capital_raise",
    "buyback",
    "management_change",
    "insider_trade",
    "product_deal",
    "analyst_rating",
    "regulation_legal",
    "delisting_halt",
    "other",
)


@unique
class Bucket(StrEnum):
    """Daily screener source bucket."""

    TREND_LEADER = "trend_leader"
    VOLUME_SURGE = "volume_surge"
    HIGH_52W_BREAKOUT = "high_52w_breakout"
    PULLBACK = "pullback"
    SQUEEZE_BREAKOUT = "squeeze_breakout"
    BACKFILL = "backfill"


@unique
class Trend(StrEnum):
    """Technical trend classification."""

    UP = "up"
    MIXED = "mixed"
    DOWN = "down"
    NO_DATA = "no_data"


@unique
class Regime(StrEnum):
    """Market risk regime."""

    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


@unique
class Permission(StrEnum):
    """Source-level trading permission."""

    BLOCK = "block"
    BLOCK_BUY = "block_buy"
    TRADE_ELIGIBLE = "trade_eligible"


@unique
class InvestmentType(StrEnum):
    """Portfolio policy variant."""

    AGGRESSIVE = "aggressive"
    CONSERVATIVE = "conservative"


@unique
class Side(StrEnum):
    """Trading direction supported by the MVP."""

    BUY = "buy"
    HOLD = "hold"


@unique
class Decision(StrEnum):
    """Critic decision."""

    PASS = "pass"  # noqa: S105 - decision vocabulary, not a credential
    REJECT = "reject"
    HOLD = "hold"


@unique
class OrderStatus(StrEnum):
    """Paper-order lifecycle."""

    PLANNED = "planned"
    SUBMITTED = "submitted"
    FILLED = "filled"
    FAILED = "failed"
    CANCELED = "canceled"


@unique
class EvidenceKind(StrEnum):
    """Evidence provenance category."""

    MARKET_DATA = "market_data"
    DISCLOSURE = "disclosure"
    NEWS = "news"
    MODEL_OUTPUT = "model_output"
    BROKER = "broker"


@unique
class ModelProvider(StrEnum):
    """Stable identity of the model execution boundary."""

    MOCK = "mock"
    OPENAI = "openai"
    LOCAL = "local"


@unique
class RunState(StrEnum):
    """Operational execution lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@unique
class StageAttemptState(StrEnum):
    """Retry-aware lifecycle for one persisted stage attempt."""

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@unique
class ReviewSource(StrEnum):
    """Price source used for review snapshots."""

    FIXTURE = "fixture"
    MARKET_DATA = "market_data"


@unique
class SubmissionState(StrEnum):
    """Pre-submit broker reservation lifecycle."""

    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    FAILED = "failed"
