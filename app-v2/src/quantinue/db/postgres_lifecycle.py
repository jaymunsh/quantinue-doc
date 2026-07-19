"""Canonical domain writes triggered by completed PostgreSQL pipeline stages."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from quantinue.core.contracts import AccountOrderPlan, PipelineContext
from quantinue.db.domain import PostgresDomainRepository
from quantinue.db.domain_records import (
    AccountWrite,
    CompletedBuyWrite,
    CriticVerdictWrite,
    OrderPlanWrite,
    OrderReconciliation,
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


def _session_trade_date(result: PipelineContext) -> date:
    """Return the session date screening persisted, not the wall-clock date.

    tb_disclosure/tb_news/tb_strategist_signals all FK (trade_date, ticker) to
    tb_daily_pick, whose rows carry the last session's candle date (role_02).
    Outside regular hours (weekends, premarket) the wall clock has already moved
    past that session, so cycle_ts.date() would violate the FK.
    """
    if result.technical_output is not None:
        for snapshot in result.technical_output.snapshots:
            if snapshot.ticker == result.request.ticker:
                return snapshot.trade_date
    return result.request.cycle_ts.date()


def _persisted_plans(result: PipelineContext) -> tuple[OrderPlanWrite, ...]:
    """Project role 09's decisions into write records, one per account."""
    if result.account_plans:
        sources = result.account_plans
    elif result.risk_decision is not None:
        sources = (
            AccountOrderPlan(
                account_id=result.account_id or 1,
                signal_id=result.signal_id or 0,
                decision=result.risk_decision,
                quantity=result.quantity or 0,
                entry_price=result.risk_entry_price or 0.0,
                stop_loss=result.stop_loss or 0.0,
                take_profit=result.take_profit or 0.0,
                skipped_reason=result.risk_skipped_reason,
            ),
        )
    else:
        return ()
    return tuple(
        OrderPlanWrite(
            run_id=str(result.run_id),
            ticker=result.request.ticker,
            cycle_ts=result.request.cycle_ts,
            trade_date=_session_trade_date(result),
            account_id=plan.account_id,
            signal_id=plan.signal_id or None,
            decision=plan.decision,
            skipped_reason=plan.skipped_reason,
            quantity=plan.quantity,
            entry_price=Decimal(str(plan.entry_price)) if plan.entry_price else None,
            stop_price=Decimal(str(plan.stop_loss)) if plan.stop_loss else None,
            take_profit_price=Decimal(str(plan.take_profit)) if plan.take_profit else None,
        )
        for plan in sources
    )


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
        signal = StrategistSignalWrite(
            run_id=str(result.run_id),
            trade_date=_session_trade_date(result),
            ticker=result.request.ticker,
            cycle_ts=result.request.cycle_ts,
            side=result.side or "hold",
            conviction=Decimal(str(result.conviction or 0)),
            summary="pipeline strategist decision",
            decision_close=price,
            evidence=tuple(item.evidence_id for item in result.evidence_trace),
            disclosure_score=Decimal(str(result.disclosure_score or 0)),
            news_score=Decimal(str(result.news_score or 0)),
            signal_consensus=result.signal_consensus or 0,
        )
        if result.disclosure_source is not None and result.news_source is not None:
            await domain.save_source_records(signal, result.disclosure_source, result.news_source)
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
    if component == "09":
        # 계좌별 계획을 전부 남긴다 — 하나만 남기면 팬아웃이 관측되지 않는다.
        for plan in _persisted_plans(result):
            await domain.save_order_plan(plan)
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
