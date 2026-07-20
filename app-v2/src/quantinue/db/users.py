"""Reads over ``tb_user`` — the web login's view of who may sign in.

``tb_user``는 1차부터 스키마에만 있고 소비자가 0이던 테이블이다. 이 모듈이
그 첫 소비자다. ``control_room_reads``와 마찬가지로 ``domain.py``에서 분리해
두는 이유는 방향이다 — 여기 있는 것은 **누가 접속했는가**를 묻고, 판단 경로의
질의와 섞이면 인증을 고치다 배분 잡의 쿼리를 건드리게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True, slots=True)
class UserRecord:
    """One account holder, exactly as the login path needs to see them."""

    user_id: int
    login_id: str
    display_name: str
    role: str
    password_hash: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class UserAccount:
    """The one account a signed-in user owns, if they own one."""

    account_id: int
    broker_account_id: str
    inv_type: str | None
    status: str


async def count_users(engine: AsyncEngine) -> int:
    """Count rows so an installation with no accounts can still open its console.

    부트스트랩 예외(W-D2)의 판정이다. 관리자 계정이 하나라도 생기면 그 순간
    관제실이 잠긴다 — 행 수를 캐시하지 않는 이유가 그것이다. 표본이 계정
    수준(십 단위)이라 매 요청 세는 비용은 무시할 수 있다.
    """
    async with engine.begin() as connection:
        return await connection.scalar(text("SELECT count(*) FROM tb_user")) or 0


async def find_user_by_login(engine: AsyncEngine, login_id: str) -> UserRecord | None:
    """Look up one sign-in candidate, returning None rather than raising.

    비활성 계정도 **찾아서** 돌려준다. 여기서 거르면 "없는 계정"과 "정지된
    계정"이 서로 다른 경로가 되고, 그 차이가 응답에 새어 나온다. 거절은
    호출자가 한 자리에서 한 가지 방법으로 한다.
    """
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    dedent(
                        """
                        SELECT user_id, login_id, display_name, role,
                               password_hash, is_active
                        FROM tb_user
                        WHERE login_id = :login_id
                        """
                    )
                ),
                {"login_id": login_id},
            )
        ).first()
    if row is None:
        return None
    return UserRecord(
        user_id=row.user_id,
        login_id=row.login_id,
        display_name=row.display_name,
        role=row.role,
        password_hash=row.password_hash,
        is_active=row.is_active,
    )


async def account_for_user(engine: AsyncEngine, user_id: int) -> UserAccount | None:
    """Return the account this user owns, scoping ownership in the query itself.

    화면이 전부 읽고 나서 거르는 방식을 쓰지 않는다 — 그 방식은 필터를
    빠뜨린 화면 하나가 남의 계좌를 보여주는 구조다. 소유는 WHERE 절이
    가진다. 1유저=1계좌는 부분 유니크 인덱스가 이미 DB로 강제하므로
    여기서 여러 행을 다룰 경우를 만들지 않는다.
    """
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    dedent(
                        """
                        SELECT id, broker_account_id, inv_type, status
                        FROM tb_account
                        WHERE user_id = :user_id
                        """
                    )
                ),
                {"user_id": user_id},
            )
        ).first()
    if row is None:
        return None
    return UserAccount(
        account_id=row.id,
        broker_account_id=row.broker_account_id,
        inv_type=row.inv_type,
        status=row.status,
    )
