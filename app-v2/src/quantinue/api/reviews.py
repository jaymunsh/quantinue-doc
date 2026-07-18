"""Explicit scheduler/API seam for advancing delayed reviews."""

from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict

from quantinue.api.access import ControlRoomAccess
from quantinue.db.reviews import PostgresReviewRepository
from quantinue.roles.role_11_reviewer.calendar import Clock
from quantinue.roles.role_11_reviewer.processor import ClosingPriceProvider, ReviewProcessor


class ReviewProcessResponse(BaseModel):
    """Public result of one idempotent review advancement."""

    model_config = ConfigDict(frozen=True)

    signal_id: int
    status: str
    captured_offsets: tuple[int, ...]


def build_review_router(
    processor: ReviewProcessor, *, access: ControlRoomAccess | None = None
) -> APIRouter:
    """Bind an injected processor for HTTP or scheduler-driven invocation."""
    router = APIRouter(prefix="/api/reviews", tags=["reviews"])

    @router.post("/{signal_id}/process")
    async def process_due_review(
        signal_id: int,
        request: Request,
        x_quantinue_control_token: Annotated[
            str | None, Header(alias="X-Quantinue-Control-Token")
        ] = None,
    ) -> ReviewProcessResponse:
        if access is not None:
            access.require(request, x_quantinue_control_token)
        result = await processor.process(signal_id)
        return ReviewProcessResponse(
            signal_id=result.signal_id,
            status=result.status.value,
            captured_offsets=result.captured_offsets,
        )

    return router


async def build_postgres_review_processor(
    database_url: str,
    prices: ClosingPriceProvider,
    clock: Clock,
) -> tuple[ReviewProcessor, PostgresReviewRepository]:
    """Create an initialized runtime processor and its closable repository."""
    repository = PostgresReviewRepository(database_url)
    await repository.initialize()
    return ReviewProcessor(repository, prices, clock), repository
