"""The exit job: enumerate holdings, apply the exit rules, execute the closes.

11단계 선형 런의 스테이지가 아니라 **독립 잡**이다(재설계 §2). 분석이 실패한
날에도 청산은 돌아야 하기 때문이다 — 살 줄 모르는 날은 손해가 없지만 팔 줄
모르는 날은 손해가 쌓인다.

잡이 하는 일은 셋뿐이고, 판단은 하나도 하지 않는다:
  1. 열린 포지션을 읽는다      (db.open_positions)
  2. 각각을 규칙에 넣는다       (exits.decide_exit — 순수 함수)
  3. 나온 결정을 집행한다       (시그널 → 주문 → 브로커 → 체결)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from quantinue.broker.contracts import ClosePlan, ClosingBroker
from quantinue.core.market_calendar import NyseCalendar
from quantinue.core.ontology import FillSide
from quantinue.core.order_identity import derive_client_order_id
from quantinue.db.domain_records import (
    CloseOrderReservation,
    CompletedFillWrite,
    StrategistSignalWrite,
)
from quantinue.roles.exits.contracts import DailyObservation, decide_exit

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from quantinue.roles.exits.contracts import ExitDecision, OpenPosition


@dataclass(frozen=True, slots=True)
class ExitJob:
    """Apply the exit rules to every open position exactly once per day."""

    store: object
    broker: object
    time_exit_bdays: int
    calendar: NyseCalendar = field(default_factory=NyseCalendar)

    async def run(
        self,
        *,
        as_of: date,
        observations: Mapping[str, DailyObservation],
    ) -> tuple[ExitDecision, ...]:
        """Close every position today's observations say must be closed.

        관측이 없는 종목은 건너뛴다 — 수집 실패를 매도로 둔갑시키지 않기 위해서.
        빈 관측(``DailyObservation()``)을 넣는 것과 아예 없는 것은 같은 뜻이고,
        규칙 쪽에서 "시세 없으면 아무것도 안 함"으로 이미 처리된다.
        """
        positions = await self._open_positions()
        closed: list[ExitDecision] = []
        for position in positions:
            observation = observations.get(position.ticker)
            if observation is None:
                continue
            decision = decide_exit(
                position,
                observation,
                as_of=as_of,
                time_exit_bdays=self.time_exit_bdays,
                calendar=self.calendar,
            )
            if decision is None:
                continue
            if await self._execute(decision, as_of=as_of):
                closed.append(decision)
        return tuple(closed)

    async def _open_positions(self) -> tuple[OpenPosition, ...]:
        """Read holdings through whichever store shape was injected."""
        domain = getattr(self.store, "domain", self.store)
        reader = getattr(domain, "open_positions", None)
        if reader is None:
            return ()
        return await reader()

    async def _execute(self, decision: ExitDecision, *, as_of: date) -> bool:
        """Turn one decision into a durable close, or report that it was a no-op.

        순서가 중요하다. 시그널 → 주문 → 브로커 → 체결 순으로 가는 이유는
        브로커가 체결한 뒤에 원장 자리가 없는 상황을 만들지 않기 위해서다.
        반대로 하면 체결은 됐는데 기록할 곳이 없는, 되돌릴 수 없는 상태가 된다.
        """
        position = decision.position
        client_order_id = derive_client_order_id(
            account_id=position.account_id,
            signal_id=position.signal_id,
            is_close=True,
        )
        order_id = await self._reserve_close(decision, client_order_id, as_of=as_of)
        if order_id is None:
            # 이미 닫힌 포지션이다 — 재실행이 두 번째 매도가 되지 않게 여기서 멈춘다.
            return False
        broker = self.broker
        if not isinstance(broker, ClosingBroker):
            # 청산을 못 하는 브로커(실 Alpaca — 로드맵 R1)에 붙었을 때 조용히
            # 실패하는 대신 명시적으로 건너뛴다. 주문 행은 planned로 남아
            # 다음 실행에서 다시 시도된다.
            return False
        result = await broker.close(
            ClosePlan(
                ticker=position.ticker,
                client_order_id=client_order_id,
                quantity=position.quantity,
                reference_price=float(decision.reference_price),
                closes_client_order_id=derive_client_order_id(
                    account_id=position.account_id, signal_id=position.signal_id
                ),
            )
        )
        domain = getattr(self.store, "domain", self.store)
        _ = await domain.record_completed_fill(
            CompletedFillWrite(
                idempotency_key=client_order_id,
                broker_order_id=result.order_id,
                broker_fill_id=f"{result.order_id}-fill",
                quantity=result.quantity,
                price=Decimal(str(result.filled_avg_price)),
                filled_at=datetime.now(UTC),
                side=FillSide.SELL,
            )
        )
        return True

    async def _reserve_close(
        self, decision: ExitDecision, client_order_id: str, *, as_of: date
    ) -> int | None:
        """Create the sell signal and the close order that pairs with the entry."""
        domain = getattr(self.store, "domain", self.store)
        position = decision.position
        # 스크리너에서 탈락한 보유 종목도 청산 대상이다. 시그널은 그날의 분석
        # 대상에만 남길 수 있으므로(FK), 보유가 범위 안이라는 사실을 먼저 기록한다.
        in_scope = await domain.ensure_holding_in_scope(as_of, position.ticker)
        if not in_scope:
            return None
        # 기계적 청산도 sell 시그널 행을 남긴다(재설계 D7). 계보가 균일해져
        # role_11이 매도도 채점할 수 있고, tb_order.signal_id NOT NULL과
        # UNIQUE(account_id, signal_id)가 그대로 유지된다.
        signal_id = await domain.save_signal(
            StrategistSignalWrite(
                run_id=f"exit:{as_of.isoformat()}:{position.order_id}",
                trade_date=as_of,
                ticker=position.ticker,
                # 시그널의 UNIQUE 축은 (ticker, cycle_ts, inv_type)인데 청산은
                # **포지션 단위**다. 한 계좌가 같은 종목을 두 번 사서 둘 다
                # 열려 있으면(다른 날 매수) 날짜만으로는 두 청산이 같은 시그널
                # 행을 공유하게 되고, 두 번째 close 주문이
                # UNIQUE(account_id, signal_id)에 걸려 죽는다 — 한 포지션이
                # 못 팔린 채 남는다. 실제로 실행 중에 이렇게 터졌다.
                #
                # 그래서 닫는 매수 주문 id로 시각을 갈라준다. 결정적이라 재실행이
                # 같은 값을 내고(멱등), 진입 시그널의 장중 시각과도 겹치지 않는다.
                cycle_ts=datetime.combine(as_of, time(), tzinfo=UTC)
                + timedelta(microseconds=position.order_id),
                side="sell",
                conviction=Decimal("1.000"),
                summary=f"{decision.reason.value} exit",
                decision_close=decision.reference_price,
                evidence=(),
                inv_type=position.inv_type,
            )
        )
        return await domain.reserve_close_order(
            CloseOrderReservation(
                signal_id=signal_id,
                account_id=position.account_id,
                ticker=position.ticker,
                quantity=position.quantity,
                reference_price=decision.reference_price,
                closes_order_id=position.order_id,
                idempotency_key=client_order_id,
            )
        )
