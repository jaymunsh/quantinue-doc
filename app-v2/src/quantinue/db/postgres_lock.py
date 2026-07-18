"""PostgreSQL session advisory-lock primitives."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection


async def try_lock(connection: AsyncConnection, key: str) -> bool:
    """Acquire a crash-released session lock without waiting."""
    locked = await connection.scalar(
        select(func.pg_try_advisory_lock(func.hashtextextended(key, 0)))
    )
    await connection.commit()
    return bool(locked)


async def unlock(connection: AsyncConnection, key: str) -> None:
    """Release a session lock and its dedicated connection."""
    _ = await connection.scalar(select(func.pg_advisory_unlock(func.hashtextextended(key, 0))))
    await connection.commit()
    await connection.close()
