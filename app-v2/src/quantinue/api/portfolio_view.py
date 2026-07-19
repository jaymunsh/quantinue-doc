"""Project the local simulated ledger for the control room.

구 관제실의 ``presentation.py``에서 이 함수만 살아남았다. 나머지는 전부 11단계
런의 모양(스테이지·시도·역할 상세)을 그리는 것이었고 러너와 함께 죽었다.
모의 계좌 원장은 러너가 아니라 **주문·체결**에서 나오므로 잡 경로에서도 그대로
참이다 — 그래서 여기로 옮겨 살렸다.
"""

from quantinue.api.schemas import (
    PortfolioAccountView,
    PortfolioPositionView,
    SimulatedFillView,
    SimulatedOrderView,
    SimulatedPortfolioView,
)
from quantinue.db.simulated_portfolio import SimulatedPortfolioSnapshot


def simulated_portfolio_view(snapshot: SimulatedPortfolioSnapshot) -> SimulatedPortfolioView:
    """Project the local ledger without implying an Alpaca account balance."""
    return SimulatedPortfolioView(
        account=PortfolioAccountView(
            opening_cash=snapshot.account.opening_cash,
            current_cash=snapshot.account.current_cash,
            equity=snapshot.account.equity,
            buying_power=snapshot.account.buying_power,
            currency=snapshot.account.currency,
        ),
        positions=tuple(
            PortfolioPositionView(
                ticker=position.ticker,
                quantity=position.quantity,
                average_cost=position.average_cost,
                mark_price=position.mark.price,
                mark_source=position.mark.source.value,
                mark_as_of=position.mark.as_of,
                market_value=position.market_value,
                unrealized_pnl=position.unrealized_pnl,
                allocation=position.allocation,
            )
            for position in snapshot.positions
        ),
        orders=tuple(
            SimulatedOrderView(
                order_id=order.order_id,
                ticker=order.ticker,
                quantity=order.quantity,
                reference_price=order.reference_price,
                status=order.status.value,
                created_at=order.created_at,
            )
            for order in snapshot.orders
        ),
        fills=tuple(
            SimulatedFillView(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                ticker=fill.ticker,
                quantity=fill.quantity,
                price=fill.price,
                filled_at=fill.filled_at,
            )
            for fill in snapshot.fills
        ),
        realized_pnl_label="해당 없음 · 1차 매수 전용",
    )
