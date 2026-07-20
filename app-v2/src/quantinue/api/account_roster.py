"""The account roster — every account the ledger has, one row each.

관제실이 오래 계좌 **하나**를 보고 있었다. 구 러너가 남긴 유물 계좌였고
체결이 0건이라, 실제로 움직인 돈 전부가 화면 밖에 있었다(§1-1). 이 모듈은
그 패널의 대체물이다.

합계만 그리지 않는 이유: "어느 계좌가 멈췄나"는 합계가 답할 수 없는 질문이고,
계좌별 성향 격차(공격형 20% · 안전형 10% 사이징)도 행이 갈려야 보인다.
합계를 함께 두는 것은 한눈에 규모를 보기 위해서지 행을 대신하려는 게 아니다.

여기 있는 것은 순수 투영이다 — 원장 레코드를 받아 화면 모델을 만들 뿐 DB를
모른다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from quantinue.db.control_room_reads import AccountOverviewRecord


class AccountRosterRowView(BaseModel):
    """One account as the roster shows it."""

    model_config = ConfigDict(frozen=True)

    broker_account_id: str
    inv_type: str | None
    status: str
    cash: Decimal
    equity: Decimal
    open_position_count: int
    order_count: int
    fill_count: int


class AccountRosterView(BaseModel):
    """Every account, plus the sums that say how much money is in play."""

    model_config = ConfigDict(frozen=True)

    accounts: tuple[AccountRosterRowView, ...] = ()
    total_cash: Decimal = Decimal(0)
    total_equity: Decimal = Decimal(0)
    total_fills: int = 0


def account_roster_view(records: tuple[AccountOverviewRecord, ...]) -> AccountRosterView:
    """Project ledger account rows into the roster panel."""
    rows = tuple(
        AccountRosterRowView(
            broker_account_id=record.broker_account_id,
            inv_type=record.inv_type,
            status=record.status,
            cash=record.cash,
            equity=record.equity,
            open_position_count=record.open_position_count,
            order_count=record.order_count,
            fill_count=record.fill_count,
        )
        for record in records
    )
    return AccountRosterView(
        accounts=rows,
        total_cash=sum((row.cash for row in rows), start=Decimal(0)),
        total_equity=sum((row.equity for row in rows), start=Decimal(0)),
        total_fills=sum(row.fill_count for row in rows),
    )
