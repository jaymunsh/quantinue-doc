"""Hardened Alpaca PAPER broker adapter."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Final, Literal, assert_never

import anyio
import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quantinue.broker.reservations import (
    CompletedClaim,
    InFlightClaim,
    InMemoryOrderReservations,
    OrderReservations,
    OwnerClaim,
    ReservationOwnerToken,
)
from quantinue.core.config import BrokerMode, Settings
from quantinue.core.contracts import OrderResult
from quantinue.core.errors import (
    AuthenticationFailureError,
    HttpFailureError,
    TradingDisabledError,
    TransientFailureError,
    ValidationFailureError,
)

if TYPE_CHECKING:
    from quantinue.broker.contracts import OrderPlan

PAPER_BASE_URL: Final = "https://paper-api.alpaca.markets"
WAIT_FOR_OWNER_SECONDS: Final = 35.0
HTTP_ERROR_MIN: Final = 400
HTTP_UNAUTHORIZED: Final = 401
HTTP_NOT_FOUND: Final = 404
HTTP_CONFLICT: Final = 409
PROVIDER: Final = "alpaca"
AlpacaStatus = Literal[
    "submitted",
    "new",
    "pending_new",
    "accepted",
    "pending_replace",
    "replaced",
    "filled",
    "canceled",
    "expired",
    "done_for_day",
    "rejected",
    "stopped",
    "suspended",
]


class _AlpacaLeg(BaseModel):
    """Identifier and type of one bracket child returned by Alpaca."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    type: Literal["limit", "stop", "stop_limit", "market"]


class _AlpacaOrderResponse(BaseModel):
    """Strict subset of the Alpaca order resource used by the MVP."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    client_order_id: str = Field(min_length=1)
    status: AlpacaStatus
    qty: str
    filled_avg_price: str | None = None
    legs: tuple[_AlpacaLeg, ...] = ()


class AlpacaBroker:
    """Idempotent Alpaca adapter that can only reach the PAPER endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        reservations: OrderReservations | None = None,
    ) -> None:
        """Keep validated settings and optional test/persistence seams."""
        self._settings = settings
        self._transport = transport
        self._reservations = reservations or InMemoryOrderReservations()

    async def submit(self, plan: OrderPlan) -> OrderResult:
        """Reserve, submit once, and reconcile ambiguous timeout outcomes."""
        self._require_paper_triple_gate()
        claim = await self._reservations.claim(plan.client_order_id)
        match claim:
            case CompletedClaim(result=result):
                return result
            case InFlightClaim():
                completed = await self._reservations.wait(
                    plan.client_order_id, WAIT_FOR_OWNER_SECONDS
                )
                if completed is not None:
                    return completed
                return await self._reconcile_with_new_client(plan.client_order_id)
            case OwnerClaim(owner_token=owner_token):
                return await self._submit_as_owner(plan, owner_token)
            case unreachable:
                assert_never(unreachable)

    def _require_paper_triple_gate(self) -> None:
        selected = self._settings.broker_mode is BrokerMode.ALPACA
        enabled = self._settings.trading_enabled
        paper_url = str(self._settings.alpaca_base_url).rstrip("/") == PAPER_BASE_URL
        if not (selected and enabled and paper_url):
            raise TradingDisabledError

    async def _submit_as_owner(
        self, plan: OrderPlan, owner_token: ReservationOwnerToken
    ) -> OrderResult:
        completed = False
        try:
            async with self._create_client() as client:
                try:
                    response = await client.post("/v2/orders", json=self._payload(plan))
                except httpx2.TimeoutException:
                    result = await self._reconcile(client, plan.client_order_id)
                else:
                    result = await self._parse_submission(client, response, plan.client_order_id)
            published = await self._reservations.complete(plan.client_order_id, owner_token, result)
            if not published:
                winner = await self._reservations.wait(plan.client_order_id, 0)
                if winner is None:
                    reason = "reservation generation changed without a completed result"
                    raise TransientFailureError(PROVIDER, reason)
                result = winner
            completed = True
            return result
        finally:
            if not completed:
                with anyio.CancelScope(shield=True):
                    _ = await self._reservations.release(plan.client_order_id, owner_token)

    async def _parse_submission(
        self, client: httpx2.AsyncClient, response: httpx2.Response, client_order_id: str
    ) -> OrderResult:
        if response.status_code == HTTP_UNAUTHORIZED:
            raise AuthenticationFailureError(PROVIDER)
        if response.status_code == HTTP_CONFLICT:
            return await self._reconcile(client, client_order_id)
        if response.status_code >= HTTP_ERROR_MIN:
            raise HttpFailureError(response.status_code)
        return self._normalize(response)

    async def _reconcile_with_new_client(self, client_order_id: str) -> OrderResult:
        async with self._create_client() as client:
            return await self._reconcile(client, client_order_id)

    async def _reconcile(self, client: httpx2.AsyncClient, client_order_id: str) -> OrderResult:
        try:
            response = await client.get(
                "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id}
            )
        except httpx2.TimeoutException as error:
            reason = "order reconciliation timed out"
            raise TransientFailureError(PROVIDER, reason) from error
        if response.status_code == HTTP_NOT_FOUND:
            reason = "order acceptance was not confirmed"
            raise TransientFailureError(PROVIDER, reason)
        if response.status_code == HTTP_UNAUTHORIZED:
            raise AuthenticationFailureError(PROVIDER)
        if response.status_code >= HTTP_ERROR_MIN:
            raise HttpFailureError(response.status_code)
        return self._normalize(response)

    def _normalize(self, response: httpx2.Response) -> OrderResult:
        try:
            parsed = _AlpacaOrderResponse.model_validate_json(response.content)
            quantity = int(parsed.qty)
            filled_price = float(parsed.filled_avg_price or 0)
        except (ValidationError, ValueError) as error:
            field = "alpaca_order"
            reason = "malformed provider response"
            raise ValidationFailureError(field, reason) from error
        return OrderResult(
            order_id=parsed.id,
            client_order_id=parsed.client_order_id,
            status=self._normalize_status(parsed.status),
            quantity=quantity,
            filled_avg_price=filled_price,
            parent_order_id=parsed.id,
            stop_leg_order_id=next(
                (leg.id for leg in parsed.legs if leg.type in {"stop", "stop_limit"}), None
            ),
            take_profit_leg_order_id=next(
                (leg.id for leg in parsed.legs if leg.type == "limit"), None
            ),
        )

    @staticmethod
    def _normalize_status(
        raw: AlpacaStatus,
    ) -> Literal["submitted", "accepted", "filled", "canceled", "rejected"]:
        match raw:
            case "new" | "pending_new" | "accepted" | "pending_replace" | "replaced":
                return "accepted"
            case "filled":
                return "filled"
            case "canceled" | "expired" | "done_for_day":
                return "canceled"
            case "rejected" | "stopped" | "suspended":
                return "rejected"
            case "submitted":
                return "submitted"
            case unreachable:
                assert_never(unreachable)

    def _create_client(self) -> httpx2.AsyncClient:
        limits = httpx2.Limits(
            max_connections=200, max_keepalive_connections=40, keepalive_expiry=30.0
        )
        timeout = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
        transport = self._transport or httpx2.AsyncHTTPTransport(
            http2=True,
            retries=0,
            limits=limits,
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        )
        headers = {
            "APCA-API-KEY-ID": self._settings.alpaca_api_key.get_secret_value(),
            "APCA-API-SECRET-KEY": self._settings.alpaca_secret_key.get_secret_value(),
        }
        return httpx2.AsyncClient(
            transport=transport,
            timeout=timeout,
            base_url=PAPER_BASE_URL,
            headers=headers,
            follow_redirects=False,
        )

    @staticmethod
    def _payload(plan: OrderPlan) -> dict[str, str | dict[str, str]]:
        return {
            "symbol": plan.ticker,
            "qty": str(plan.quantity),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "client_order_id": plan.client_order_id,
            "take_profit": {"limit_price": f"{plan.take_profit:.2f}"},
            "stop_loss": {"stop_price": f"{plan.stop_loss:.2f}"},
        }
