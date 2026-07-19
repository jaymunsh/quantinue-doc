"""Configuration boundary tests."""

from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from quantinue.core.config import BrokerMode, DataMode, LlmMode, Settings
from quantinue.orchestration.policy import AllocationConfig
from quantinue.roles.role_09_risk_portfolio.contracts import RiskPortfolioInput


class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(env_file=None, env_prefix="QUANTINUE_", extra="ignore")


def test_mock_mode_needs_no_credentials() -> None:
    settings = Settings(llm_mode=LlmMode.MOCK)

    assert settings.llm_mode is LlmMode.MOCK
    assert settings.data_mode is DataMode.FIXTURE


def test_public_data_mode_needs_no_credentials() -> None:
    # Given / When
    settings = Settings(data_mode=DataMode.PUBLIC)

    # Then
    assert settings.data_mode is DataMode.PUBLIC


def test_selected_openai_mode_rejects_empty_key_without_echoing_secret() -> None:
    with pytest.raises(ValidationError) as captured:
        _ = Settings.model_validate({"llm_mode": LlmMode.OPENAI, "openai_api_key": ""})

    message = str(captured.value)
    assert "openai_api_key" in message
    assert "sk-" not in message


def test_secret_values_are_redacted() -> None:
    settings = Settings.model_validate(
        {"llm_mode": LlmMode.OPENAI, "openai_api_key": "secret-test-value"}
    )

    assert "secret-test-value" not in repr(settings)
    assert "secret-test-value" not in settings.model_dump_json()


def test_local_mode_requires_an_http_endpoint() -> None:
    with pytest.raises(ValidationError):
        _ = Settings.model_validate(
            {
                "llm_mode": LlmMode.LOCAL,
                "local_llm_base_url": "file:///tmp/model",
            }
        )


def test_timeout_and_retry_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        _ = Settings(llm_timeout_seconds=0, llm_max_retries=-1)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.alpaca.markets",
        "https://example.com",
        "http://paper-api.alpaca.markets",
        "file:///tmp/alpaca",
    ],
)
def test_alpaca_url_accepts_only_the_exact_paper_endpoint(base_url: str) -> None:
    with pytest.raises(ValidationError):
        _ = Settings.model_validate({"alpaca_base_url": base_url})


def test_exact_alpaca_paper_endpoint_is_valid() -> None:
    settings = Settings.model_validate({"alpaca_base_url": "https://paper-api.alpaca.markets"})

    assert str(settings.alpaca_base_url).rstrip("/") == "https://paper-api.alpaca.markets"


@pytest.mark.parametrize("trading_enabled", [False, True])
def test_selected_alpaca_mode_requires_both_credentials(
    monkeypatch: pytest.MonkeyPatch, *, trading_enabled: bool
) -> None:
    monkeypatch.delenv("QUANTINUE_ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("QUANTINUE_ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError, match="alpaca_api_key"):
        _ = IsolatedSettings(
            broker_mode=BrokerMode.ALPACA,
            trading_enabled=trading_enabled,
        )


def test_trading_enabled_is_legal_with_the_local_simulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """체결이 로컬 시뮬로 확정된 뒤(D1)에는 mock이 정상 거래 경로다.

    예전 규칙은 trading_enabled=true면 broker_mode=alpaca를 요구했다. 무장
    개념이 사라진(D2) 지금 그 규칙은 mock + 거래 활성이라는 **최종 상태를
    기동 불가로 만든다** — 실제로 이 조합이 .env에 있었고 앱이 뜨지 않았다.
    """
    settings = IsolatedSettings(
        broker_mode=BrokerMode.MOCK,
        trading_enabled=True,
        control_room_token=SecretStr("test-token"),
    )

    assert settings.trading_enabled
    assert settings.broker_mode is BrokerMode.MOCK


def test_alpaca_mode_stays_valid_while_dormant() -> None:
    """결합을 끊는 것이 휴면 상태(alpaca + 거래 비활성)까지 없애면 안 된다."""
    settings = IsolatedSettings(
        broker_mode=BrokerMode.ALPACA,
        trading_enabled=False,
        alpaca_api_key=SecretStr("test-key"),
        alpaca_secret_key=SecretStr("test-secret"),
    )

    assert not settings.trading_enabled
    assert settings.broker_mode is BrokerMode.ALPACA


def test_trading_enabled_requires_control_room_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUANTINUE_CONTROL_ROOM_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="control_room_token"):
        _ = IsolatedSettings(
            broker_mode=BrokerMode.ALPACA,
            trading_enabled=True,
            alpaca_api_key=SecretStr("test-key"),
            alpaca_secret_key=SecretStr("test-secret"),
        )


def test_selected_alpaca_mode_accepts_nonempty_secret_credentials() -> None:
    settings = Settings.model_validate(
        {
            "broker_mode": BrokerMode.ALPACA,
            "alpaca_api_key": "paper-key-placeholder",
            "alpaca_secret_key": "paper-secret-placeholder",
        }
    )

    assert settings.broker_mode is BrokerMode.ALPACA


def test_simulated_opening_cash_is_one_million() -> None:
    """모의 계좌 초기 자본은 살아 있다 — 화면과 포트폴리오 투영이 읽는다."""
    # Given / When
    settings = IsolatedSettings(simulated_account_opening_cash_usd=Decimal("1000000.00"))

    # Then
    assert settings.simulated_account_opening_cash_usd == Decimal("1000000.00")


@pytest.mark.parametrize("opening_cash", ["0", "-0.01", "1000000.001"])
def test_simulated_opening_cash_requires_a_positive_usd_cent_amount(
    opening_cash: str,
) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = IsolatedSettings.model_validate({"simulated_account_opening_cash_usd": opening_cash})


def test_selected_local_mode_rejects_empty_key() -> None:
    with pytest.raises(ValidationError) as captured:
        _ = Settings.model_validate({"llm_mode": LlmMode.LOCAL, "local_llm_api_key": ""})

    assert "local_llm_api_key" in str(captured.value)


@pytest.mark.parametrize(
    ("mode", "field"),
    [(LlmMode.OPENAI, "openai_model"), (LlmMode.LOCAL, "local_llm_model")],
)
def test_selected_model_name_must_be_nonblank(mode: LlmMode, field: str) -> None:
    values: dict[str, str] = {"llm_mode": mode, field: "   "}
    if mode is LlmMode.OPENAI:
        values["openai_api_key"] = "placeholder"

    with pytest.raises(ValidationError):
        _ = Settings.model_validate(values)


def test_the_daily_order_cap_default_agrees_everywhere() -> None:
    """캡 기본값이 4곳에서 서로 달랐다(1·1·1·5) — redesign §7이 지적한 드리프트.

    실효값의 단일 소유자는 mvp2.allocation.daily_new_order_cap이고, 나머지는
    전부 그 값(5)에 정렬한다. 이 테스트는 다음 드리프트를 커밋 시점에 잡는다.

    확인 지점이 넷에서 **하나**로 줄었다. 나머지 셋(role_09 서비스 기본값 ·
    PipelinePolicy · Settings.daily_new_order_cap)은 전부 구 러너와 함께
    죽었고, 소비자를 잃은 설정 키를 남겨두면 그게 다음 세대의 유령이 된다.
    남은 확인은 배분 잡이 실제로 만드는 입력(RiskPortfolioInput)이 소유자와
    같은 기본값을 갖는가 하나다.
    """
    owner = AllocationConfig().daily_new_order_cap
    assert RiskPortfolioInput.model_fields["daily_new_order_cap"].default == owner
