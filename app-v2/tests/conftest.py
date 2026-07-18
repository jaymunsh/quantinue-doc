"""Repository-wide pytest safety gates."""

from __future__ import annotations

import os
from typing import Final

import pytest

_REAL_KEY_OPT_IN: Final = "QUANTINUE_RUN_REAL_KEY_TESTS"
_TEST_RUNTIME_ENV: Final = {
    "QUANTINUE_BROKER_MODE": "mock",
    "QUANTINUE_DATA_MODE": "fixture",
    "QUANTINUE_DATABASE_MODE": "memory",
    "QUANTINUE_LLM_MODE": "mock",
    "QUANTINUE_LOCAL_LLM_MODEL": "qwen2.5:7b",
    "QUANTINUE_MOCK_MODEL": "deterministic-mock-v1",
    "QUANTINUE_OPENAI_MODEL": "gpt-4o-mini",
    "QUANTINUE_TRADING_ENABLED": "false",
}

if os.getenv(_REAL_KEY_OPT_IN) != "1":
    os.environ.update(_TEST_RUNTIME_ENV)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip stateful/cost-bearing tests unless the operator explicitly opts in."""
    if os.getenv(_REAL_KEY_OPT_IN) == "1":
        return
    skip = pytest.mark.skip(
        reason=f"real provider test disabled; set {_REAL_KEY_OPT_IN}=1 to opt in"
    )
    for item in items:
        if "real_key" in item.keywords:
            item.add_marker(skip)
