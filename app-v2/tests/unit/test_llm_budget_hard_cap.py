"""Pre-dispatch maximum-cost coverage for the process-local LLM budget."""

from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256

import anyio
import pytest
from anyio.lowlevel import checkpoint
from openai import AsyncOpenAI
from pydantic import SecretStr

from quantinue.core.config import LlmMode, Settings
from quantinue.core.ontology import ModelProvider
from quantinue.llm.budget import (
    BudgetedAnalyzer,
    LlmBudgetExceededError,
    LlmUsageBoundExceededError,
    LlmUsageRecord,
    ModelPrice,
)
from quantinue.llm.provider import (
    AnalysisMetadata,
    AnalysisResult,
    AnalysisTask,
    DeterministicAnalyzer,
)
from quantinue.llm.provider_factory import build_llm_analyzer
from quantinue.llm.usage_limits import MaximumTokenUsage, TokenUsage


class _Ledger:
    def __init__(self, committed: Decimal, *, stale: bool = False) -> None:
        self.committed = committed
        self.records: list[LlmUsageRecord] = []
        self.stale = stale

    async def llm_spend_on(self, day: date) -> Decimal:
        _ = day
        recorded = sum(
            (record.est_cost_usd for record in self.records), Decimal(0)
        )
        return self.committed if self.stale else self.committed + recorded

    async def record_llm_usage(self, record: LlmUsageRecord) -> None:
        self.records.append(record)


class _BoundedAnalyzer:
    def __init__(
        self, maximum: MaximumTokenUsage, actual: TokenUsage, expected_entries: int = 0
    ) -> None:
        self.maximum = maximum
        self.actual = actual
        self.calls: list[str] = []
        self.expected_entries = expected_entries
        self.entered = anyio.Event()
        self.release = anyio.Event()

    def maximum_usage(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> MaximumTokenUsage:
        _ = (task, prompt, profile)
        return self.maximum

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        _ = (task, prompt, profile)
        self.calls.append(prompt)
        if len(self.calls) == self.expected_entries:
            self.entered.set()
        if self.expected_entries:
            await self.release.wait()
        return AnalysisResult(
            score=0.5,
            label="buy",
            reason="fixture",
            usage=self.actual,
            metadata=AnalysisMetadata(
                model="gpt-x",
                provider=ModelProvider.OPENAI,
                prompt_version="v1",
                policy_version="p1",
                input_hash=sha256(b"prompt").hexdigest(),
            ),
        )


def _analyzer(inner: _BoundedAnalyzer, ledger: _Ledger) -> BudgetedAnalyzer:
    return BudgetedAnalyzer(
        inner,
        ledger=ledger,
        daily_limit_usd=3,
        pricing={"gpt-x": ModelPrice(input_usd_per_1m=1)},
        now=lambda: datetime(2026, 7, 21, 4, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_maximum_cost_above_remaining_budget_skips_provider() -> None:
    # Given
    ledger = _Ledger(Decimal("2.997"))
    inner = _BoundedAnalyzer(
        maximum=MaximumTokenUsage(
            model="gpt-x", input_tokens=4_000, output_tokens=0
        ),
        actual=TokenUsage(input_tokens=4_000, output_tokens=0),
    )

    # When / Then
    with pytest.raises(LlmBudgetExceededError):
        await _analyzer(inner, ledger).analyze(AnalysisTask.STRATEGY, "prompt")

    assert inner.calls == []
    assert ledger.records == []


@pytest.mark.anyio
async def test_maximum_cost_exact_fit_records_full_actual_usage() -> None:
    ledger = _Ledger(Decimal("2.996"))
    usage = TokenUsage(input_tokens=4_000, output_tokens=0)
    maximum = MaximumTokenUsage(
        model="gpt-x",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    inner = _BoundedAnalyzer(maximum, usage)

    _ = await _analyzer(inner, ledger).analyze(AnalysisTask.STRATEGY, "prompt")

    assert inner.calls == ["prompt"]
    assert ledger.records[0].est_cost_usd == Decimal("0.004")


@pytest.mark.anyio
async def test_unused_reservation_is_released_to_a_later_call() -> None:
    ledger = _Ledger(Decimal("2.992"))
    maximum = MaximumTokenUsage(model="gpt-x", input_tokens=5_000, output_tokens=0)
    inner = _BoundedAnalyzer(
        maximum, TokenUsage(input_tokens=3_000, output_tokens=0)
    )
    analyzer = _analyzer(inner, ledger)

    _ = await analyzer.analyze(AnalysisTask.STRATEGY, "first")
    _ = await analyzer.analyze(AnalysisTask.STRATEGY, "second")

    assert analyzer.reserved_usd == 0
    assert [record.est_cost_usd for record in ledger.records] == [
        Decimal("0.003"),
        Decimal("0.003"),
    ]


@pytest.mark.anyio
async def test_local_commit_blocks_against_a_stale_ledger_read() -> None:
    ledger = _Ledger(Decimal("2.997"), stale=True)
    usage = TokenUsage(input_tokens=3_000, output_tokens=0)
    maximum = MaximumTokenUsage(
        model="gpt-x",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    inner = _BoundedAnalyzer(maximum, usage)
    analyzer = _analyzer(inner, ledger)

    _ = await analyzer.analyze(AnalysisTask.STRATEGY, "first")
    with pytest.raises(LlmBudgetExceededError):
        await analyzer.analyze(AnalysisTask.STRATEGY, "second")

    assert inner.calls == ["first"]
    assert ledger.records[0].est_cost_usd == Decimal("0.003")


@pytest.mark.anyio
async def test_bound_violation_records_full_cost_and_blocks_followup() -> None:
    ledger = _Ledger(Decimal("2.997"), stale=True)
    maximum = MaximumTokenUsage(model="gpt-x", input_tokens=3_000, output_tokens=0)
    inner = _BoundedAnalyzer(
        maximum, TokenUsage(input_tokens=4_000, output_tokens=0)
    )
    analyzer = _analyzer(inner, ledger)

    with pytest.raises(LlmUsageBoundExceededError):
        await analyzer.analyze(AnalysisTask.STRATEGY, "dishonest")
    with pytest.raises(LlmBudgetExceededError):
        await analyzer.analyze(AnalysisTask.STRATEGY, "followup")

    assert ledger.records[0].est_cost_usd == Decimal("0.004")
    assert inner.calls == ["dishonest"]


@pytest.mark.anyio
@pytest.mark.parametrize("run", range(12))
async def test_concurrent_callers_reserve_only_their_maximum(run: int) -> None:
    ledger = _Ledger(Decimal("2.994"))
    maximum = MaximumTokenUsage(model="gpt-x", input_tokens=3_000, output_tokens=0)
    inner = _BoundedAnalyzer(
        maximum, TokenUsage(input_tokens=3_000, output_tokens=0), expected_entries=2
    )
    analyzer = _analyzer(inner, ledger)
    exhausted: list[str] = []

    async def call(prompt: str) -> None:
        try:
            _ = await analyzer.analyze(AnalysisTask.STRATEGY, prompt)
        except LlmBudgetExceededError:
            exhausted.append(prompt)

    async with anyio.create_task_group() as task_group:
        for contender in range(3):
            _ = task_group.start_soon(call, f"{run}-{contender}")
        await inner.entered.wait()
        await checkpoint()
        assert analyzer.reserved_usd == Decimal("0.006")
        inner.release.set()

    assert len(inner.calls) == 2
    assert len(exhausted) == 1
    assert analyzer.reserved_usd == 0


@pytest.mark.anyio
async def test_cancellation_releases_maximum_for_replacement_call() -> None:
    ledger = _Ledger(Decimal("2.997"))
    maximum = MaximumTokenUsage(model="gpt-x", input_tokens=3_000, output_tokens=0)
    inner = _BoundedAnalyzer(
        maximum, TokenUsage(input_tokens=3_000, output_tokens=0), expected_entries=1
    )
    analyzer = _analyzer(inner, ledger)
    scope = anyio.CancelScope()
    cancelled = anyio.Event()

    async def cancel_me() -> None:
        try:
            with scope:
                _ = await analyzer.analyze(AnalysisTask.STRATEGY, "cancelled")
        finally:
            cancelled.set()

    async with anyio.create_task_group() as task_group:
        _ = task_group.start_soon(cancel_me)
        await inner.entered.wait()
        scope.cancel()
        await cancelled.wait()
        assert analyzer.reserved_usd == 0
        inner.expected_entries = 0
        _ = await analyzer.analyze(AnalysisTask.STRATEGY, "replacement")

    assert inner.calls == ["cancelled", "replacement"]
    assert ledger.records[0].est_cost_usd == Decimal("0.003")


def test_deterministic_provider_declares_zero_maximum_usage() -> None:
    analyzer = DeterministicAnalyzer()

    maximum = analyzer.maximum_usage(AnalysisTask.STRATEGY, "prompt")

    assert maximum == MaximumTokenUsage(
        model="deterministic-mock-v1", input_tokens=0, output_tokens=0
    )


def test_billable_provider_bound_includes_all_configured_attempts() -> None:
    settings = Settings(
        llm_mode=LlmMode.OPENAI,
        openai_api_key=SecretStr("placeholder"),
        openai_model="gpt-x",
        llm_max_input_tokens=4_000,
        llm_max_output_tokens=500,
        llm_max_retries=2,
    )
    analyzer = build_llm_analyzer(
        settings, openai_client=AsyncOpenAI(api_key="placeholder")
    )

    maximum = analyzer.maximum_usage(AnalysisTask.STRATEGY, "prompt")

    assert maximum == MaximumTokenUsage(
        model="gpt-x", input_tokens=4_000, output_tokens=1_500
    )
