"""OpenAI, local OpenAI-compatible, and deterministic LLM adapters."""

from __future__ import annotations

from enum import StrEnum, unique
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, assert_never

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, RunUsage, UsageLimits
from pydantic_ai.exceptions import ModelAPIError

from quantinue.core.errors import TransientFailureError
from quantinue.core.ontology import ModelProvider
from quantinue.llm.prompts import SystemPrompt, load_system_prompt
from quantinue.llm.transport import has_transient_transport_cause
from quantinue.llm.usage_limits import (
    AnalyzerProviderConfig,
    MaximumTokenUsage,
    TokenUsage,
)

if TYPE_CHECKING:
    from pydantic_ai.models import Model


@unique
class AnalysisTask(StrEnum):
    """Closed set of LLM responsibilities in the MVP."""

    DISCLOSURE = "disclosure"
    NEWS = "news"
    STRATEGY = "strategy"
    CRITIC = "critic"
    REVIEW = "review"


class AnalysisMetadata(BaseModel):
    """Auditable model and prompt lineage for one result."""

    model_config = ConfigDict(frozen=True)

    model: str
    provider: ModelProvider
    prompt_version: str
    policy_version: str
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisResult(BaseModel):
    """Structured result accepted from any LLM provider."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0, le=1)
    label: str
    reason: str
    # 과금 원장이 읽는 자리. mock은 공짜라 None이고, 로컬도 요율이 없으면
    # 비용 0으로 적힌다 — 토큰 수 자체는 남겨 콜 규모를 볼 수 있게 한다.
    usage: TokenUsage | None = None
    # 전략 태스크만 채우는 판단 서사. None 기본값이 설계다 — 서사는 부가물이라
    # 모델이 빼먹어도 분석이 죽으면 안 되고(구조화 출력 실패 = 종목 skip의
    # 전례), 서사가 없는 태스크(공시·뉴스·크리틱)는 애초에 만들지 않는다.
    bull_case: str | None = None
    key_risk: str | None = None
    metadata: AnalysisMetadata


class ModelOutput(BaseModel):
    """Schema exposed to remote models; operational metadata stays trusted."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0, le=1)
    label: str
    reason: str


class StrategyModelOutput(ModelOutput):
    """The strategy task's extended schema: the judgement plus its narrative.

    전략 태스크에만 이 스키마를 쓴다. ModelOutput에 필드를 더하면 크리틱·공시
    채점까지 다섯 태스크 전부가 서사 필드를 보게 되는데, 그건 관계없는 콜의
    토큰을 낭비하고 구조화 출력 실패 표면만 넓힌다. 길이 상한은 스키마가
    강제한다 — 프롬프트의 부탁만으로는 장문이 온다(max_tokens 512 실측 근거).
    """

    bull_case: str = Field(default="", max_length=200)
    key_risk: str = Field(default="", max_length=200)


def _output_type_for(task: AnalysisTask) -> type[ModelOutput]:
    """Return the structured-output schema this task is allowed to fill."""
    return StrategyModelOutput if task is AnalysisTask.STRATEGY else ModelOutput


def _narrative(field: str) -> str | None:
    """Map an absent narrative to None so the ledger stays NULL, not ""."""
    return field.strip() or None


class LlmAnalyzer(Protocol):
    """Narrow analysis capability used by LLM-backed roles."""

    def maximum_usage(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> MaximumTokenUsage:
        """Return the provider-enforced aggregate usage ceiling for this call."""
        ...

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        """Return a schema-validated analysis with lineage metadata.

        ``profile``은 판단을 내리는 성향이다. 기본값이 None인 이유는 성향이
        없는 태스크(공시 요약·뉴스 채점)가 대부분이기 때문이고, 성향별 프롬프트가
        없는 태스크에서는 무시된다 — 어느 파일이 실제로 쓰였는지는
        ``SystemPrompt.variant``가 기록한다.
        """
        ...


def _metadata(
    model: str, provider: ModelProvider, system_prompt: SystemPrompt, prompt: str
) -> AnalysisMetadata:
    return AnalysisMetadata(
        model=model,
        provider=provider,
        prompt_version=system_prompt.version,
        policy_version=system_prompt.policy_version,
        input_hash=sha256(prompt.encode()).hexdigest(),
    )


class DeterministicAnalyzer:
    """Stable local analyzer for fixtures, tests, and offline development."""

    def __init__(self, model_name: str = "deterministic-mock-v1") -> None:
        """Configure the model identifier recorded in result lineage."""
        self._model_name = model_name

    def maximum_usage(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> MaximumTokenUsage:
        """Report that the deterministic provider cannot incur token charges."""
        _ = (task, prompt, profile)
        return MaximumTokenUsage(
            model=str(self._model_name), input_tokens=0, output_tokens=0
        )

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        """Map each task to a deterministic, fully traced result."""
        system_prompt = load_system_prompt(task.value, profile=profile)
        metadata = _metadata(self._model_name, ModelProvider.MOCK, system_prompt, prompt)
        match task:
            case AnalysisTask.DISCLOSURE:
                output = ModelOutput(
                    score=0.78, label="positive", reason="실적 성장과 가이던스 유지"
                )
            case AnalysisTask.NEWS:
                output = ModelOutput(
                    score=0.74, label="positive", reason="AI 수요 관련 긍정 기사 흐름"
                )
            case AnalysisTask.STRATEGY:
                # mock도 서사를 채운다 — 배선이 mock 스모크에서 이미 검증되게.
                output = StrategyModelOutput(
                    score=0.76,
                    label="buy",
                    reason="기술·공시·뉴스 합의",
                    bull_case="상대강도와 거래량이 돌파를 확인",
                    key_risk="시장 국면 반전 시 모멘텀 소멸",
                )
            case AnalysisTask.CRITIC:
                output = ModelOutput(
                    score=0.82, label="approved", reason="강한 반증과 하드 블로커 없음"
                )
            case AnalysisTask.REVIEW:
                output = ModelOutput(
                    score=0.70, label="consistent", reason="결정 근거와 결과가 일치"
                )
            case unreachable:
                assert_never(unreachable)
        return AnalysisResult(
            score=output.score,
            label=output.label,
            reason=output.reason,
            bull_case=_narrative(output.bull_case)
            if isinstance(output, StrategyModelOutput)
            else None,
            key_risk=_narrative(output.key_risk)
            if isinstance(output, StrategyModelOutput)
            else None,
            metadata=metadata,
        )


class PydanticAiAnalyzer:
    """PydanticAI adapter for OpenAI and OpenAI-compatible local servers."""

    def __init__(
        self,
        model: Model,
        config: AnalyzerProviderConfig,
    ) -> None:
        """Store the provider model and bounded structured-output retry policy."""
        self._model = model
        self._config = config
        self._retries = config.retries

    def maximum_usage(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> MaximumTokenUsage:
        """Return the token ceiling enforced by this provider adapter."""
        _ = (task, prompt, profile)
        if self._config.usage_limit is None:
            return MaximumTokenUsage(
                model=self._config.model_name, input_tokens=0, output_tokens=0
            )
        return self._config.usage_limit.maximum_usage(self._config.model_name)

    async def analyze(
        self, task: AnalysisTask, prompt: str, *, profile: str | None = None
    ) -> AnalysisResult:
        """Run one schema-constrained call with external text isolated as data."""
        system_prompt = load_system_prompt(task.value, profile=profile)
        agent = Agent(
            self._model,
            output_type=_output_type_for(task),
            instructions=system_prompt.content,
            retries=self._retries,
        )
        try:
            usage_limits = None
            if self._config.usage_limit is not None:
                usage_limits = UsageLimits(
                    request_limit=self._config.usage_limit.max_requests,
                    input_tokens_limit=self._config.usage_limit.max_input_tokens,
                    output_tokens_limit=(
                        self._config.usage_limit.max_output_tokens
                        * self._config.usage_limit.max_requests
                    ),
                    count_tokens_before_request=(
                        self._config.usage_limit.count_input_before_request
                    ),
                )
            result = await agent.run(
                ModelInput(external_data=prompt).model_dump_json(),
                usage_limits=usage_limits,
            )
        except ModelAPIError as error:
            if has_transient_transport_cause(error):
                raise TransientFailureError(
                    provider=self._config.provider.value,
                    reason="model transport unavailable",
                ) from error
            raise
        output = result.output
        return AnalysisResult(
            score=output.score,
            label=output.label,
            reason=output.reason,
            bull_case=_narrative(output.bull_case)
            if isinstance(output, StrategyModelOutput)
            else None,
            key_risk=_narrative(output.key_risk)
            if isinstance(output, StrategyModelOutput)
            else None,
            usage=_usage(result),
            metadata=_metadata(
                self._config.model_name,
                self._config.provider,
                system_prompt,
                prompt,
            ),
        )


class _UsageResult(Protocol):
    @property
    def usage(self) -> RunUsage | None: ...


def _usage(result: _UsageResult) -> TokenUsage | None:
    """Read what the provider said this run consumed, if it said anything.

    재시도가 있었으면 그 시도들까지 합산된 값이 온다 — 지갑이 실제로 치른
    것이 그것이므로 맞다. 값을 못 얻으면 0으로 적지 않고 None이다:
    0은 "공짜였다"는 주장이고, 모르는 것을 그렇게 적으면 예산이 샌다.
    """
    # pydantic-ai 2.9에서 ``usage``는 메서드가 아니라 속성이다(실측).
    usage = result.usage
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
    )


class ModelInput(BaseModel):
    """Quoted model input that marks all caller text as untrusted data."""

    model_config = ConfigDict(frozen=True)

    external_data: str
