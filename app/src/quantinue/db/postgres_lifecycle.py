"""Canonical domain writes triggered by completed PostgreSQL pipeline stages."""

from dataclasses import replace
from decimal import Decimal

from quantinue.core.contracts import PipelineContext
from quantinue.db.domain import PostgresDomainRepository
from quantinue.db.domain_records import (
    AccountWrite,
    CompletedBuyWrite,
    CriticVerdictWrite,
    OrderReconciliation,
    SourceRecordsWrite,
    StrategistSignalWrite,
)


class PostgresDomainLifecycleMixin:
    """Structural lifecycle implementation shared by the PostgreSQL run store."""

    def __init__(
        self,
        domain: PostgresDomainRepository,
        account_identity: str,
        opening_cash: Decimal,
    ) -> None:
        """Bind the canonical repository used by lifecycle callbacks."""
        self._domain = domain
        self._account_identity = account_identity
        self._account = AccountWrite(account_identity, opening_cash, opening_cash, opening_cash)

    async def record_completed_buy(self, value: CompletedBuyWrite) -> int:
        """Apply the shared completed-buy contract through atomic accounting."""
        return await self._domain.record_completed_buy(value)

    @property
    def account_identity(self) -> str:
        """Return the isolated app-owned account selected at composition time."""
        return self._account_identity

    async def stage_completed(
        self,
        component: str,
        previous: PipelineContext,
        result: PipelineContext,
    ) -> PipelineContext:
        """Persist canonical domain rows at their validated stage boundary."""
        del previous
        return await persist_domain_stage(self._domain, self._account, component, result)


async def persist_domain_stage(
    domain: PostgresDomainRepository,
    account: AccountWrite,
    component: str,
    result: PipelineContext,
) -> PipelineContext:
    """Persist each validated role output at its own stage boundary."""
    if component == "01" and result.universe_output is not None:
        await domain.save_universe(result.universe_output)
    if (
        component == "03"
        and result.daily_screener_output is not None
        and result.technical_output is not None
    ):
        await domain.save_daily_stage(
            result.daily_screener_output,
            result.technical_output,
        )
    if component == "04" and result.macro_output is not None:
        await domain.save_macro(result.macro_output)
    if component == "08":
        price = Decimal(str(result.last_price or 0))
        daily_pick = next(
            (
                pick
                for pick in (
                    result.daily_screener_output.picks
                    if result.daily_screener_output is not None
                    else ()
                )
                if pick.ticker == result.request.ticker
            ),
            None,
        )
        signal = StrategistSignalWrite(
            run_id=str(result.run_id),
            trade_date=(
                daily_pick.trade_date if daily_pick is not None else result.request.cycle_ts.date()
            ),
            ticker=result.request.ticker,
            cycle_ts=result.request.cycle_ts,
            side=result.side or "hold",
            conviction=Decimal(str(result.conviction or 0)),
            summary="pipeline strategist decision",
            decision_close=price,
            evidence=tuple(item.evidence_id for item in result.evidence_trace),
            disclosure_score=Decimal(str(result.disclosure_score or 0)),
            news_score=Decimal(str(result.news_score or 0)),
        )
        if result.disclosure_source is not None and result.news_sources:
            await domain.save_source_records(
                SourceRecordsWrite(
                    signal=signal,
                    disclosures=result.disclosure_sources or (result.disclosure_source,),
                    news=result.news_sources,
                    representative_disclosure=result.disclosure_source,
                    representative_news=result.news_source,
                )
            )
        signal_id = await domain.save_signal(signal)
        account_id = await domain.save_account(account)
        _ = await domain.save_verdict(
            CriticVerdictWrite(
                signal_id=signal_id,
                ticker=result.request.ticker,
                decision="pass" if result.critic_approved else "reject",
                category="pipeline_gate",
                objection="accepted" if result.critic_approved else "rejected",
                confidence=Decimal(str(result.conviction or 0)),
                decided_layer="gate",
            )
        )
        return replace(result, signal_id=signal_id, account_id=account_id)
    if component == "10" and result.order is not None:
        if result.order.status == "filled":
            _ = await domain.record_completed_buy(
                CompletedBuyWrite(
                    idempotency_key=result.order.client_order_id,
                    broker_order_id=result.order.order_id,
                    broker_fill_id=f"{result.order.order_id}:fill",
                    quantity=result.order.quantity,
                    price=Decimal(str(result.order.filled_avg_price)),
                    filled_at=result.request.cycle_ts,
                )
            )
        else:
            _ = await domain.reconcile_order(
                OrderReconciliation(
                    idempotency_key=result.order.client_order_id,
                    status=result.order.status,
                    broker_order_id=result.order.order_id,
                    parent_order_id=result.order.parent_order_id,
                    stop_leg_order_id=result.order.stop_leg_order_id,
                    take_profit_leg_order_id=result.order.take_profit_leg_order_id,
                )
            )
    return result
