"""Reads over ``tb_user`` — the web login's view of who may sign in.

``tb_user``는 1차부터 스키마에만 있고 소비자가 0이던 테이블이다. 이 모듈이
그 첫 소비자다. ``control_room_reads``와 마찬가지로 ``domain.py``에서 분리해
두는 이유는 방향이다 — 여기 있는 것은 **누가 접속했는가**를 묻고, 판단 경로의
질의와 섞이면 인증을 고치다 배분 잡의 쿼리를 건드리게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
    # 돈을 소유권 질의와 **같은 행**에서 읽는다. 따로 읽으면 계좌를 한 번 더
    # 찾아야 하고, 그 두 번째 조회에 WHERE user_id를 빠뜨리는 순간 남의 잔고가
    # 자기 화면에 뜬다. 소유는 한 곳에서만 판정한다.
    cash: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class UserWrite:
    """One account holder to create or refresh, with an already-hashed secret."""

    login_id: str
    display_name: str
    role: str
    password_hash: str


async def save_user(engine: AsyncEngine, write: UserWrite, *, reset_password: bool) -> int:
    """Create or refresh one login and return its id, keeping the password by default.

    재실행이 비밀번호를 되돌리지 않는다 — 계좌 프로비저닝이 잔고를 안 건드리는
    것과 같은 규칙이다. 다만 잊었을 때 복구할 길이 하나는 있어야 해서
    ``reset_password``로 명시할 때만 덮어쓴다(계좌 CRUD 화면은 W3-3).
    """
    password_clause = (
        ":password_hash" if reset_password else "COALESCE(tb_user.password_hash, :password_hash)"
    )
    statement = text(
        dedent(
            f"""
            INSERT INTO tb_user (login_id, display_name, role, password_hash)
            VALUES (:login_id, :display_name, :role, :password_hash)
            ON CONFLICT (login_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                role = EXCLUDED.role,
                password_hash = {password_clause}
            RETURNING user_id
            """  # noqa: S608 - password_clause is one of two literals above
        )
    )
    async with engine.begin() as connection:
        user_id = await connection.scalar(
            statement,
            {
                "login_id": write.login_id,
                "display_name": write.display_name,
                "role": write.role,
                "password_hash": write.password_hash,
            },
        )
    return int(user_id or 0)


async def set_account_owner(engine: AsyncEngine, broker_account_id: str, user_id: int) -> bool:
    """Attach one account to its owner, reporting whether such an account exists.

    없는 계좌를 조용히 넘기지 않고 False로 알린다 — 시드가 계좌 명부보다
    앞서거나 이름이 어긋나면 "유저는 생겼는데 계좌가 없는" 상태가 되고,
    그 상태의 유저는 로그인해서 404만 본다.
    """
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                dedent(
                    """
                    UPDATE tb_account SET user_id = :user_id, updated_at = now()
                    WHERE broker_account_id = :broker_account_id
                    """
                )
            ),
            {"user_id": user_id, "broker_account_id": broker_account_id},
        )
    return result.rowcount > 0


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
                        SELECT id, broker_account_id, inv_type, status, cash, equity
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
        cash=Decimal(str(row.cash)),
        equity=Decimal(str(row.equity)),
    )
