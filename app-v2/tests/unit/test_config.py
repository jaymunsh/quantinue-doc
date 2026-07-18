"""Configuration boundary tests."""

from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from quantinue.core.config import BrokerMode, DataMode, LlmMode, Settings


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


def test_trading_enabled_requires_selected_alpaca_mode_and_credentials() -> None:
    with pytest.raises(ValidationError, match="broker_mode=alpaca"):
        _ = Settings.model_validate({"trading_enabled": True})


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


def test_daily_new_order_cap_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _ = Settings.model_validate({"daily_new_order_cap": 0})


def test_first_cycle_order_controls_default_to_one_thousand_usd_and_one_attempt() -> None:
    # Given / When
    settings = IsolatedSettings()

    # Then
    assert settings.max_app_order_exposure_usd == Decimal("1000.00")
    assert settings.daily_new_order_cap == 1


def test_simulated_opening_cash_is_one_million_and_independent_of_exposure_cap() -> None:
    # Given / When
    settings = IsolatedSettings(
        simulated_account_opening_cash_usd=Decimal("1000000.00"),
        max_app_order_exposure_usd=Decimal("1000.00"),
    )

    # Then
    assert settings.simulated_account_opening_cash_usd == Decimal("1000000.00")
    assert settings.max_app_order_exposure_usd == Decimal("1000.00")


@pytest.mark.parametrize("opening_cash", ["0", "-0.01", "1000000.001"])
def test_simulated_opening_cash_requires_a_positive_usd_cent_amount(
    opening_cash: str,
) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = IsolatedSettings.model_validate({"simulated_account_opening_cash_usd": opening_cash})


@pytest.mark.parametrize("exposure", ["0", "-0.01", "1000.001"])
def test_app_order_exposure_requires_a_positive_usd_cent_amount(exposure: str) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = IsolatedSettings.model_validate({"max_app_order_exposure_usd": exposure})


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
