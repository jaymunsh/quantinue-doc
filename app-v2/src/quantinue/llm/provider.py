"""OpenAI, local OpenAI-compatible, and deterministic LLM adapters."""

from __future__ import annotations

from enum import StrEnum, unique
from hashlib import sha256
from typing import Protocol, assert_never

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from quantinue.core.config import LlmMode, Settings
from quantinue.core.errors import TransientFailureError
from quantinue.core.ontology import ModelProvider
from quantinue.llm.prompts import SystemPrompt, load_system_prompt


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
    metadata: AnalysisMetadata


class ModelOutput(BaseModel):
    """Schema exposed to remote models; operational metadata stays trusted."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0, le=1)
    label: str
    reason: str


class LlmAnalyzer(Protocol):
    """Narrow analysis capability used by LLM-backed roles."""

    async def analyze(self, task: AnalysisTask, prompt: str) -> AnalysisResult:
        """Return a schema-validated analysis with lineage metadata."""
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

    async def analyze(self, task: AnalysisTask, prompt: str) -> AnalysisResult:
        """Map each task to a deterministic, fully traced result."""
        system_prompt = load_system_prompt(task.value)
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
                output = ModelOutput(score=0.76, label="buy", reason="기술·공시·뉴스 합의")
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
            metadata=metadata,
        )


class PydanticAiAnalyzer:
    """PydanticAI adapter for OpenAI and OpenAI-compatible local servers."""

    def __init__(
        self,
        model: OpenAIChatModel,
        model_name: str,
        retries: int,
        provider: ModelProvider = ModelProvider.LOCAL,
    ) -> None:
        """Store the provider model and bounded structured-output retry policy."""
        self._model = model
        self._model_name = model_name
        self._retries = retries
        self._provider = provider

    async def analyze(self, task: AnalysisTask, prompt: str) -> AnalysisResult:
        """Run one schema-constrained call with external text isolated as data."""
        system_prompt = load_system_prompt(task.value)
        agent = Agent(
            self._model,
            output_type=ModelOutput,
            instructions=system_prompt.content,
            retries=self._retries,
        )
        try:
            result = await agent.run(ModelInput(external_data=prompt).model_dump_json())
        except ModelAPIError as error:
            if _has_transient_transport_cause(error):
                raise TransientFailureError(
                    provider=self._provider.value,
                    reason="model transport unavailable",
                ) from error
            raise
        return AnalysisResult(
            score=result.output.score,
            label=result.output.label,
            reason=result.output.reason,
            metadata=_metadata(self._model_name, self._provider, system_prompt, prompt),
        )


class ModelInput(BaseModel):
    """Quoted model input that marks all caller text as untrusted data."""

    model_config = ConfigDict(frozen=True)

    external_data: str


def _has_transient_transport_cause(error: ModelAPIError) -> bool:
    current: BaseException | None = error.__cause__
    while current is not None:
        if isinstance(
            current,
            (
                APITimeoutError,
                APIConnectionError,
                httpx.TimeoutException,
                httpx.TransportError,
                TimeoutError,
            ),
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def build_llm_analyzer(settings: Settings, openai_client: AsyncOpenAI | None = None) -> LlmAnalyzer:
    """Select an LLM adapter exhaustively from validated configuration."""
    match settings.llm_mode:
        case LlmMode.MOCK:
            return DeterministicAnalyzer(settings.mock_model)
        case LlmMode.OPENAI:
            model_name = settings.openai_model
            provider = ModelProvider.OPENAI
            client = openai_client or AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
        case LlmMode.LOCAL:
            model_name = settings.local_llm_model
            provider = ModelProvider.LOCAL
            client = openai_client or AsyncOpenAI(
                base_url=str(settings.local_llm_base_url),
                api_key=settings.local_llm_api_key.get_secret_value(),
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
            model_settings = OpenAIChatModelSettings(
                max_tokens=256,
                temperature=0,
                parallel_tool_calls=False,
                openai_reasoning_effort="none",
                # Local reasoning models (Qwen3.6 via omlx) ignore reasoning_effort
                # and dump chain-of-thought prose into `content`, which then fails
                # structured JSON parsing. The omlx/vLLM chat template honours
                # enable_thinking=false to suppress the <think> phase entirely.
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            model = OpenAIChatModel(
                model_name,
                provider=OpenAIProvider(openai_client=client),
                profile=OpenAIModelProfile(
                    openai_chat_supports_max_completion_tokens=False,
                ),
                settings=model_settings,
            )
            return PydanticAiAnalyzer(model, model_name, 0, provider)
        case unreachable:
            assert_never(unreachable)
    return PydanticAiAnalyzer(
        OpenAIChatModel(model_name, provider=OpenAIProvider(openai_client=client)),
        model_name,
        settings.llm_max_retries,
        provider,
    )
