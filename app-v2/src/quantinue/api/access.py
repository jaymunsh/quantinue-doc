"""Control-room authorization and same-origin checks for paper trading."""

from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, Request, status
from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class ControlRoomAccess:
    """Authorize a paper-enabled control-room mutation without exposing its token."""

    token: SecretStr

    async def __call__(
        self,
        request: Request,
        x_quantinue_control_token: Annotated[
            str | None, Header(alias="X-Quantinue-Control-Token")
        ] = None,
    ) -> None:
        """Provide the FastAPI dependency shape used by protected mutation routes."""
        self.require(request, x_quantinue_control_token)

    def require(self, request: Request, supplied_token: str | None) -> None:
        """Reject cross-origin or unauthenticated mutation requests."""
        origin = request.headers.get("origin")
        expected_origin = str(request.base_url).rstrip("/")
        if origin is not None and origin != expected_origin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-origin request rejected")
        expected_token = self.token.get_secret_value()
        if supplied_token is None or not compare_digest(supplied_token, expected_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "control-room authorization required")
