"""Semantic prompt resource tests."""

import pytest

from quantinue.llm.prompts import PromptLoadError, load_system_prompt
from quantinue.llm.provider import AnalysisTask


@pytest.mark.parametrize("task", tuple(AnalysisTask))
def test_prompt_resources_carry_policy_and_version(task: AnalysisTask) -> None:
    prompt = load_system_prompt(task)

    assert prompt.version
    assert prompt.policy_version
    assert prompt.content
    assert "외부" in prompt.content
    assert "데이터" in prompt.content


def test_missing_prompt_failure_is_typed() -> None:
    with pytest.raises(PromptLoadError) as captured:
        _ = load_system_prompt("unknown-role")

    assert captured.value.resource == "unknown-role"
