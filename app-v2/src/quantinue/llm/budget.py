"""Spend ledger and the budget guard that precedes every billable model call."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import anyio
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from quantinue.llm.provider import (
        AnalysisResult,
        AnalysisTask,
        LlmAnalyzer,
    )
    from quantinue.llm.usage_limits import MaximumTokenUsage


class LlmBudgetExceededError(RuntimeError):
    """Raised instead of making a call the day's budget cannot pay for."""


class LlmUsageBoundExceededError(RuntimeError):
    """Raised after recording usage that violated its provider-enforced bound."""


class ModelPrice(BaseModel):
    """Per-million-token rates for one model, owned by config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_usd_per_1m: float = Field(default=0.0, ge=0)
    output_usd_per_1m: float = Field(default=0.0, ge=0)


class LlmUsageRecord(BaseModel):
    """One row of the tb_llm_usage ledger."""

    model_config = ConfigDict(frozen=True)

    called_at: datetime
    task: str
    model: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    est_cost_usd: Decimal = Field(ge=0)
    run_id: str | None = None


def require_pricing_for(model: str, pricing: Mapping[str, ModelPrice]) -> None:
    """Refuse to start a billable provider whose model has no declared rate.

    fail-closed다. 요율이 없으면 ``_cost``가 늘 0을 내고, 0만 쌓이는 원장은
    상한을 영원히 안 넘긴다 — 예산이 있는 척하면서 없는 상태가 된다.
    모델명을 바꾸고 config를 안 고친 순간이 정확히 그 상태이므로, 기동에서 막는다.
    """
    if model not in pricing:
        message = (
            f"no model_pricing declared for billable model {model!r} "
            "— add it under mvp2.budget.model_pricing"
        )
        raise ValueError(message)


class LlmUsageLedger(Protocol):
    """Narrow spend-ledger capability used by the budget guard."""

    async def llm_spend_on(self, day: date) -> Decimal:
        """Return the total estimated spend recorded for that calendar day."""
        ...

    async def record_llm_usage(self, record: LlmUsageRecord) -> None:
        """Append one call to the ledger."""
        ...


class BudgetedAnalyzer:
    """Wraps an analyzer so every billable call is counted and capped."""

    def __init__(  # noqa: PLR0913 - 한 가드는 원장·한도·예약·요율·시계를 함께 소유한다.
        self,
        inner: LlmAnalyzer,
        *,
        ledger: LlmUsageLedger,
        daily_limit_usd: float,
        sell_budget_reserve_ratio: float = 0.0,
        pricing: dict[str, ModelPrice],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Store the wrapped analyzer and the ceiling it must respect."""
        self._inner = inner
        self._ledger = ledger
        self._limit = Decimal(str(daily_limit_usd))
        self._general_limit = self._limit * (
            Decimal(1) - Decimal(str(sell_budget_reserve_ratio))
        )
        self._pricing = pricing
        self._now = now
        self._spend_lock = anyio.Lock()
        self._reserved_by_day: dict[date, Decimal] = {}
        self._committed_by_day: dict[date, Decimal] = {}

    @property
    def reserved_usd(self) -> Decimal:
        """Return process-local spend currently reserved by in-flight calls."""
        return sum(self._reserved_by_day.values(), Decimal(0))

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        """Refuse, or run the wrapped call and write what it cost to the ledger."""
        return await self._analyze(
            task, prompt, profile=profile, spending_limit=self._general_limit
        )

    async def analyze_reserved(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        """Use the sell-only reserve for a holding rejudgement call."""
        return await self._analyze(
            task, prompt, profile=profile, spending_limit=self._limit
        )

    async def _analyze(
        self,
        task: AnalysisTask,
        prompt: str,
        *,
        profile: str | None,
        spending_limit: Decimal,
    ) -> AnalysisResult:
        called_at = self._now()
        day = called_at.date()
        maximum = self._inner.maximum_usage(task, prompt, profile=profile)
        reservation = self._usage_cost(maximum)
        async with self._spend_lock:
            ledger_committed = await self._ledger.llm_spend_on(day)
            committed = max(
                ledger_committed, self._committed_by_day.get(day, Decimal(0))
            )
            self._committed_by_day[day] = committed
            reserved = self._reserved_by_day.get(day, Decimal(0))
            if committed + reserved + reservation > spending_limit:
                # 남은 예산으로 살 수 없으면 **안 산다**. 여기서 중립 결과를
                # 지어내 돌려주면 판단 없이 주문이 나가는 길이 열린다 —
                # 분석 잡은 예외를 종목 단위로 격리하므로 이 종목만 건너뛴다.
                message = (
                    "daily llm budget exhausted: "
                    f"{committed} committed + {reserved} reserved + "
                    f"{reservation} requested > {spending_limit}"
                )
                raise LlmBudgetExceededError(message)
            self._reserved_by_day[day] = reserved + reservation

        reservation_active = True
        try:
            result = await self._inner.analyze(task, prompt, profile=profile)
            usage = result.usage
            if usage is None:
                return result
            model = result.metadata.model
            cost = self._cost(model, usage.input_tokens, usage.output_tokens)
            with anyio.CancelScope(shield=True):
                async with self._spend_lock:
                    await self._ledger.record_llm_usage(
                        LlmUsageRecord(
                            called_at=called_at,
                            task=task.value,
                            model=model,
                            prompt_tokens=usage.input_tokens,
                            completion_tokens=usage.output_tokens,
                            est_cost_usd=cost,
                        )
                    )
                    self._committed_by_day[day] += cost
                    self._release(day, reservation)
                    reservation_active = False
            if cost > reservation:
                message = (
                    f"provider usage cost {cost} exceeded reserved maximum "
                    f"{reservation}"
                )
                raise LlmUsageBoundExceededError(message)
            return result
        finally:
            if reservation_active:
                with anyio.CancelScope(shield=True):
                    async with self._spend_lock:
                        self._release(day, reservation)

    def _release(self, day: date, amount: Decimal) -> None:
        remaining = self._reserved_by_day[day] - amount
        if remaining == 0:
            del self._reserved_by_day[day]
        else:
            self._reserved_by_day[day] = remaining

    def _cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        """Estimate one call's cost from the configured per-model rates.

        요율이 없는 모델은 0이다 — 로컬 LLM이 실제로 공짜라 그게 정직한 값이고,
        그래서 openai 모드에서 요율 선언을 빠뜨리면 예산이 조용히 풀린다.
        그 구멍은 기동 시점 검증이 막는다(``require_pricing_for``).
        """
        price = self._pricing.get(model)
        if price is None:
            return Decimal(0)
        per_million = Decimal(1_000_000)
        return (
            Decimal(input_tokens) * Decimal(str(price.input_usd_per_1m)) / per_million
            + Decimal(output_tokens) * Decimal(str(price.output_usd_per_1m)) / per_million
        )

    def _usage_cost(self, usage: MaximumTokenUsage) -> Decimal:
        return self._cost(usage.model, usage.input_tokens, usage.output_tokens)
