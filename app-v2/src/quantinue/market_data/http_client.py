"""Optimized HTTP/2 client construction for public market feeds."""

import os
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2


@dataclass(frozen=True, slots=True)
class HttpClientPolicy:
    """Observable production policy applied by the owned client factory."""

    http2: bool = True
    retries: int = 3
    max_connections: int = 200
    max_keepalive_connections: int = 40
    keepalive_expiry: float = 30.0
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    write_timeout: float = 10.0
    pool_timeout: float = 10.0
    tcp_nodelay: int = 1
    user_agent: str = ""


# SEC 공정접근 정책은 연락처가 담긴 User-Agent를 요구한다. 제품 토큰만 보내면
# www.sec.gov가 403을 준다(data.sec.gov는 강제하지 않아 한동안 드러나지 않았다).
# 배포 시 실제 연락 가능한 주소로 바꿀 것 — 기본값은 동작하지만 응답받을 수 없다.
DEFAULT_USER_AGENT = "quantinue/0.1 admin@quantinue.local"
USER_AGENT_ENV = "QUANTINUE_HTTP_USER_AGENT"


def sec_user_agent() -> str:
    """Return the contact-bearing User-Agent required by SEC fair access."""
    return os.environ.get(USER_AGENT_ENV) or DEFAULT_USER_AGENT


HTTP_CLIENT_POLICY = HttpClientPolicy()


def _build_http_client(
    policy: HttpClientPolicy,
    transport: httpx2.AsyncBaseTransport | None,
) -> httpx2.AsyncClient:
    limits = httpx2.Limits(
        max_connections=policy.max_connections,
        max_keepalive_connections=policy.max_keepalive_connections,
        keepalive_expiry=policy.keepalive_expiry,
    )
    timeout = httpx2.Timeout(
        connect=policy.connect_timeout,
        read=policy.read_timeout,
        write=policy.write_timeout,
        pool=policy.pool_timeout,
    )
    selected = transport or httpx2.AsyncHTTPTransport(
        http2=policy.http2,
        retries=policy.retries,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, policy.tcp_nodelay)],
    )
    return httpx2.AsyncClient(
        transport=selected,
        timeout=timeout,
        follow_redirects=True,
        headers={
            "Accept-Encoding": "br, zstd, gzip",
            "User-Agent": policy.user_agent or sec_user_agent(),
        },
    )


def build_http_client(*, transport: httpx2.AsyncBaseTransport | None = None) -> httpx2.AsyncClient:
    """Create a tuned client; a supplied transport supports deterministic wire fakes."""
    return _build_http_client(HTTP_CLIENT_POLICY, transport)


@asynccontextmanager
async def public_http_client() -> AsyncGenerator[httpx2.AsyncClient]:
    """Own and close a production-configured public-feed client."""
    async with build_http_client() as client:
        yield client
