"""Environment-backed application configuration."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


@unique
class DatabaseMode(StrEnum):
    """Available persistence adapters."""

    MEMORY = "memory"
    POSTGRES = "postgres"


@unique
class LlmMode(StrEnum):
    """Available language model adapters."""

    MOCK = "mock"
    OPENAI = "openai"
    LOCAL = "local"


@unique
class BrokerMode(StrEnum):
    """Available order adapters."""

    MOCK = "mock"
    ALPACA = "alpaca"


@unique
class DataMode(StrEnum):
    """Available market-data adapters."""

    FIXTURE = "fixture"
    PUBLIC = "public"


NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
AppOrderExposureUsd = Annotated[
    Decimal,
    Field(
        gt=Decimal(0),
        max_digits=12,
        decimal_places=2,
    ),
]
SimulatedAccountOpeningCashUsd = Annotated[
    Decimal,
    Field(
        gt=Decimal(0),
        max_digits=14,
        decimal_places=2,
    ),
]


class Settings(BaseSettings):
    """Configuration parsed once at the application boundary."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="QUANTINUE_",
        extra="ignore",
    )

    app_name: str = "Quantinue Control Room"
    debug: bool = False
    data_mode: DataMode = DataMode.FIXTURE
    database_mode: DatabaseMode = DatabaseMode.MEMORY
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://quantinue:quantinue@db:5432/quantinue"
    )
    llm_mode: LlmMode = LlmMode.MOCK
    mock_model: NonBlankString = "deterministic-mock-v1"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: NonBlankString = "gpt-4o-mini"
    local_llm_base_url: AnyHttpUrl = AnyHttpUrl("http://host.docker.internal:11434/v1")
    local_llm_api_key: SecretStr = SecretStr("local-not-secret")
    local_llm_model: NonBlankString = "qwen2.5:7b"
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_retries: int = Field(default=1, ge=0, le=5)
    broker_mode: BrokerMode = BrokerMode.MOCK
    alpaca_api_key: SecretStr = SecretStr("")
    alpaca_secret_key: SecretStr = SecretStr("")
    alpaca_base_url: AnyHttpUrl = AnyHttpUrl("https://paper-api.alpaca.markets")
    trading_enabled: bool = False
    control_room_token: SecretStr = SecretStr("")
    simulated_account_opening_cash_usd: SimulatedAccountOpeningCashUsd = Decimal("1000000.00")
    max_app_order_exposure_usd: AppOrderExposureUsd = Decimal("1000.00")
    daily_new_order_cap: int = Field(default=1, ge=1, le=100)
    default_ticker: str = Field(default="NVDA", min_length=1, max_length=12)

    @field_validator("alpaca_base_url")
    @classmethod
    def require_alpaca_paper_endpoint(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Reject every endpoint except Alpaca's HTTPS paper-trading API."""
        if str(value).rstrip("/") != "https://paper-api.alpaca.markets":
            msg = "alpaca_base_url must be the Alpaca paper endpoint"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def require_selected_credentials(self) -> Settings:
        """Fail closed when the selected remote provider has no credential."""
        if self.llm_mode is LlmMode.OPENAI and not self.openai_api_key.get_secret_value():
            msg = "openai_api_key is required when llm_mode=openai"
            raise ValueError(msg)
        if self.llm_mode is LlmMode.LOCAL and not self.local_llm_api_key.get_secret_value():
            msg = "local_llm_api_key is required when llm_mode=local"
            raise ValueError(msg)
        alpaca_key = self.alpaca_api_key.get_secret_value().strip()
        alpaca_secret = self.alpaca_secret_key.get_secret_value().strip()
        if self.broker_mode is BrokerMode.ALPACA and not alpaca_key:
            msg = "alpaca_api_key is required when broker_mode=alpaca"
            raise ValueError(msg)
        if self.broker_mode is BrokerMode.ALPACA and not alpaca_secret:
            msg = "alpaca_secret_key is required when broker_mode=alpaca"
            raise ValueError(msg)
        if self.trading_enabled and self.broker_mode is not BrokerMode.ALPACA:
            msg = "broker_mode=alpaca is required when trading_enabled=true"
            raise ValueError(msg)
        if self.trading_enabled and not self.control_room_token.get_secret_value().strip():
            msg = "control_room_token is required when trading_enabled=true"
            raise ValueError(msg)
        return self
