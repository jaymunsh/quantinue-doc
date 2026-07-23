"""Configuration boundary for deterministic, local, and OpenAI analyzers."""

from typing import assert_never

from openai import AsyncOpenAI
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIChatModelSettings,
    OpenAIResponsesModel,
)
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from quantinue.core.config import LlmMode, Settings
from quantinue.core.ontology import ModelProvider
from quantinue.llm.provider import (
    DeterministicAnalyzer,
    LlmAnalyzer,
    PydanticAiAnalyzer,
)
from quantinue.llm.usage_limits import AnalyzerProviderConfig, ProviderUsageLimit


def build_llm_analyzer(
    settings: Settings, openai_client: AsyncOpenAI | None = None
) -> LlmAnalyzer:
    """Select an LLM adapter exhaustively from validated configuration."""
    uses_injected_client = openai_client is not None
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
            model = OpenAIChatModel(
                model_name,
                provider=OpenAIProvider(openai_client=client),
                profile=OpenAIModelProfile(
                    openai_chat_supports_max_completion_tokens=False
                ),
                settings=OpenAIChatModelSettings(
                    max_tokens=settings.llm_max_output_tokens,
                    temperature=0,
                    parallel_tool_calls=False,
                    openai_reasoning_effort="none",
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False}
                    },
                ),
            )
            return PydanticAiAnalyzer(
                model,
                AnalyzerProviderConfig(
                    model_name=model_name,
                    retries=settings.llm_max_retries,
                    provider=provider,
                ),
            )
        case unreachable:
            assert_never(unreachable)
    usage_limit = ProviderUsageLimit(
        max_input_tokens=settings.llm_max_input_tokens,
        max_output_tokens=settings.llm_max_output_tokens,
        max_requests=settings.llm_max_retries + 1,
        count_input_before_request=not uses_injected_client,
    )
    model_type = OpenAIChatModel if uses_injected_client else OpenAIResponsesModel
    return PydanticAiAnalyzer(
        model_type(
            model_name,
            provider=OpenAIProvider(openai_client=client),
            settings=OpenAIChatModelSettings(
                max_tokens=settings.llm_max_output_tokens
            ),
        ),
        AnalyzerProviderConfig(
            model_name=model_name,
            retries=settings.llm_max_retries,
            provider=provider,
            usage_limit=usage_limit,
        ),
    )
