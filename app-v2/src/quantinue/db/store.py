"""Public persistence adapter selection."""

from typing import assert_never

from quantinue.core.config import DatabaseMode, Settings
from quantinue.db.contracts import RunStore
from quantinue.db.memory import InMemoryRunStore
from quantinue.db.postgres import PostgresRunStore

__all__ = [
    "InMemoryRunStore",
    "PostgresRunStore",
    "RunStore",
    "build_run_store",
]


def build_run_store(settings: Settings) -> RunStore:
    """Select a persistence adapter exhaustively from configuration."""
    match settings.database_mode:
        case DatabaseMode.MEMORY:
            return InMemoryRunStore(settings.simulated_account_opening_cash_usd)
        case DatabaseMode.POSTGRES:
            return PostgresRunStore(
                str(settings.database_url)
            )
        case unreachable:
            assert_never(unreachable)
