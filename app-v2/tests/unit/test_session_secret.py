"""Session-signing key resolution at the application boundary."""

from pydantic import SecretStr

from quantinue.api.sessions import resolve_session_secret
from quantinue.core.config import Settings


def test_configured_secret_is_used_verbatim() -> None:
    # Given
    settings = Settings(session_secret=SecretStr("configured-signing-key"))

    # When
    resolved = resolve_session_secret(settings)

    # Then
    assert resolved == "configured-signing-key"


def test_missing_secret_is_generated_rather_than_defaulted() -> None:
    """No fixed development key exists, so none can be shipped by accident.

    기본값을 상수로 두면 그 상수가 그대로 배포된다. 없으면 만들되, 만든 값은
    프로세스 밖으로 나가지 않으므로 재시작하면 세션이 전부 만료된다.
    """
    # Given: explicitly unset, so a developer's .env cannot decide this test
    settings = Settings(session_secret=None)

    # When
    first = resolve_session_secret(settings)
    second = resolve_session_secret(settings)

    # Then
    assert first != second
    assert len(first) >= 32


def test_blank_secret_is_treated_as_missing() -> None:
    """An empty string in .env must not become the signing key."""
    # Given
    settings = Settings(session_secret=SecretStr("   "))

    # When
    resolved = resolve_session_secret(settings)

    # Then
    assert resolved.strip() != ""
    assert resolved != "   "
