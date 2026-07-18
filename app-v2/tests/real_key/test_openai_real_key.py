"""Minimal schema-constrained OpenAI credential preflight."""

from __future__ import annotations

import os

import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict


class _PreflightAnswer(BaseModel):
    """Smallest useful structured result for a paid provider call."""

    model_config = ConfigDict(frozen=True)

    ok: bool


@pytest.mark.anyio
@pytest.mark.real_key
async def test_openai_key_supports_minimal_structured_call() -> None:
    # Given: an explicitly opted-in operator supplied a real key.
    api_key = os.getenv("QUANTINUE_OPENAI_API_KEY")
    if not api_key:
        pytest.skip("QUANTINUE_OPENAI_API_KEY is not set")
    model = os.getenv("QUANTINUE_OPENAI_MODEL", "gpt-4o-mini")

    # When: one minimal, schema-constrained request is sent.
    async with AsyncOpenAI(api_key=api_key, max_retries=0, timeout=30) as client:
        response = await client.responses.parse(
            model=model,
            input="Return ok=true. Do not add commentary.",
            text_format=_PreflightAnswer,
            max_output_tokens=16,
        )

    # Then: the SDK parsed the response against the declared schema.
    assert response.output_parsed == _PreflightAnswer(ok=True)
