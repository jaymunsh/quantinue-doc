"""Typed provider-enforced bounds used by LLM budget admission."""

from pydantic import BaseModel, ConfigDict, Field

from quantinue.core.ontology import ModelProvider


class TokenUsage(BaseModel):
    """Token counts reported by a provider after one logical call."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class MaximumTokenUsage(TokenUsage):
    """Enforceable upper bound for one logical analyzer call."""

    model: str


class ProviderUsageLimit(BaseModel):
    """Provider-enforced token and request limits behind one analyzer call."""

    model_config = ConfigDict(frozen=True)

    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    max_requests: int = Field(ge=1)
    count_input_before_request: bool

    def maximum_usage(self, model: str) -> MaximumTokenUsage:
        """Return the aggregate charge ceiling enforced across all attempts."""
        return MaximumTokenUsage(
            model=model,
            input_tokens=self.max_input_tokens,
            output_tokens=self.max_output_tokens * self.max_requests,
        )


class AnalyzerProviderConfig(BaseModel):
    """Immutable identity, retry, and usage policy for one provider adapter."""

    model_config = ConfigDict(frozen=True)

    model_name: str
    retries: int = Field(ge=0)
    provider: ModelProvider = ModelProvider.LOCAL
    usage_limit: ProviderUsageLimit | None = None
