"""Telegram failure alerts for the unattended run.

이 앱은 서버 없이 사람이 켜고 끄는 맥에서 돈다. 잡이 실패해도 아무도
화면을 안 보고 있으면 하루가 조용히 지나간다 — 알림은 그 침묵을 깬다.

**설계 규칙 셋.**

1. **키가 없으면 알림 경로 자체가 안 만들어진다.** 빈 토큰으로 호출을 보내
   매번 401을 받는 것은 유령이다. ``build_failure_notifier``가 ``None``을
   돌려주고, 부르는 쪽은 그때 아무것도 하지 않는다.
2. **알림 실패가 잡을 죽이지 않는다.** 텔레그램이 안 되는 것과 파이프라인이
   안 도는 것은 완전히 다른 사건이다. 여기서 예외를 밖으로 내보내면 알림을
   붙였다는 이유로 매매가 멈춘다.
3. **토큰은 로그에 안 남는다.** URL에 토큰이 들어가므로 실패를 기록할 때
   URL을 찍지 않는다 — 에러 로그가 비밀 유출 경로가 되는 흔한 방식이다.

⚠️ **이 알림이 못 잡는 것**: 앱이 아예 안 뜬 경우. 알림을 보내는 주체가
앱이라 앱이 죽으면 침묵한다. 그 사각지대는 앱 **밖**에서 확인해야 한다
(completion-plan.md §④ — launchd 워치독 + 매일 요약 푸시).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from quantinue.core.config import Settings

_TIMEOUT_SECONDS = 10.0
_logger: structlog.stdlib.BoundLogger = structlog.get_logger("notify")


def build_failure_notifier(settings: Settings) -> Callable[[str], Awaitable[None]] | None:
    """Return a sender, or None when this installation has no Telegram configured."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if token is None or not token.get_secret_value() or not chat_id:
        return None
    url = f"https://api.telegram.org/bot{token.get_secret_value()}/sendMessage"

    async def send(message: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url, json={"chat_id": chat_id, "text": message}
                )
                response.raise_for_status()
        except Exception as error:  # noqa: BLE001 - 알림 실패가 매매를 멈추면 안 된다
            # URL을 찍지 않는다 — 거기에 토큰이 들어 있다.
            await _logger.awarning("notify.failed", reason=type(error).__name__)

    return send
