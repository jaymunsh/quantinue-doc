"""Role 11 normal completion for intentionally absent orders."""

from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.roles.role_11_reviewer.service import Reviewer


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("side", "critic_state", "quantity"),
    [("hold", 0, None), ("buy", 0, 0)],
)
async def test_reviewer_completes_without_order_when_trade_is_blocked(
    side: str, critic_state: int, quantity: int | None
) -> None:
    # Given
    context = PipelineContext(
        request=PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 13, tzinfo=UTC)),
        side=side,
        critic_approved=bool(critic_state),
        quantity=quantity,
        last_price=100.0,
    )

    # When
    result = await Reviewer().execute(context)

    # Then
    assert result.review is not None
    assert result.review.outcome == "no_trade"
    assert result.stages[-1].status == "completed"
