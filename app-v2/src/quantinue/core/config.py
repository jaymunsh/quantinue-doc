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
    # 구조화 출력의 상한. 256 리터럴이 provider에 박혀 있었고 실측으로 유죄가
    # 확인됐다(2026-07-20 A/B: 256에서 성향당 2건이 잘려 model error skip,
    # 512에서 0건 · 승인율 동일). 이유 문장이 길어지면 잘리는 값이라 조정이
    # 배포가 되면 안 된다.
    llm_max_output_tokens: int = Field(default=512, ge=64, le=4096)
    broker_mode: BrokerMode = BrokerMode.MOCK
    alpaca_api_key: SecretStr = SecretStr("")
    alpaca_secret_key: SecretStr = SecretStr("")
    alpaca_base_url: AnyHttpUrl = AnyHttpUrl("https://paper-api.alpaca.markets")
    trading_enabled: bool = False
    control_room_token: SecretStr = SecretStr("")
    simulated_account_opening_cash_usd: SimulatedAccountOpeningCashUsd = Decimal("1000000.00")

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
        # 두 스위치의 결합은 D2(무장 개념 소멸)에서 끊겼다. 예전에는 "거래를
        # 켰으면 실브로커여야 한다"고 강제했지만, 체결이 로컬 시뮬로 확정된
        # 지금 mock은 대역이 아니라 정상 거래 경로이므로(D1) 그 규칙은 최종
        # 상태(mock + 거래 활성)를 기동 불가로 만든다 — 실제로 그 조합이
        # .env에 있었고 앱이 뜨지 않았다.
        #
        # 자물쇠를 없앤 게 아니라 각자 서게 뒀다: 실브로커를 고르면 자격증명이
        # 있어야 하고(위), 거래를 켜면 관제실 토큰이 있어야 한다(아래).
        # alpaca 모드 + 거래 비활성은 여전히 유효한 휴면 상태다.
        if self.trading_enabled and not self.control_room_token.get_secret_value().strip():
            msg = "control_room_token is required when trading_enabled=true"
            raise ValueError(msg)
        return self
