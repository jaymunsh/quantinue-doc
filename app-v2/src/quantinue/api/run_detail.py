"""Dedicated terminal-detail endpoint composition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status

from quantinue.api.presentation import terminal_run_detail_view
from quantinue.api.schemas import TerminalRunDetailView

if TYPE_CHECKING:
    from quantinue.db.contracts import RunStore


def build_run_detail_router(store: RunStore) -> APIRouter:
    """Bind terminal detail reads to the configured run store."""
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    @router.get("/{run_id}/detail", response_model=TerminalRunDetailView)
    async def terminal_detail(run_id: str) -> TerminalRunDetailView:
        runs = await store.list_recent()
        run = next((item for item in runs if str(item.run_id) == run_id), None)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        return terminal_run_detail_view(run.detail)

    return router
