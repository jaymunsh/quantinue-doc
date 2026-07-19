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


def test_the_two_personas_are_actually_different_prompts() -> None:
    """실행에서 두 성향의 확신도가 완전히 동일하게 나왔다 — 프롬프트가 같았기 때문이다."""
    aggressive = load_system_prompt("strategy", profile="aggressive")
    conservative = load_system_prompt("strategy", profile="conservative")

    assert aggressive.content != conservative.content
    assert aggressive.variant == "aggressive"
    assert conservative.variant == "conservative"


@pytest.mark.parametrize("profile", ["aggressive", "conservative"])
def test_every_persona_keeps_the_non_negotiable_guardrails(profile: str) -> None:
    """페르소나는 태도를 바꾸는 것이지 방어선을 바꾸는 것이 아니다."""
    prompt = load_system_prompt("strategy", profile=profile)

    # 프롬프트 인젝션 방어와 게이트 불변은 성향과 무관하다.
    assert "비신뢰" in prompt.content
    assert "게이트" in prompt.content


@pytest.mark.parametrize("profile", ["aggressive", "conservative"])
def test_personas_do_not_hardcode_the_thresholds_config_owns(profile: str) -> None:
    """문턱은 config 소유다 — 프롬프트에 숫자를 박으면 두 곳이 조용히 갈린다."""
    prompt = load_system_prompt("strategy", profile=profile)

    assert "0.65" not in prompt.content
    assert "0.75" not in prompt.content
    assert "0.60" not in prompt.content


def test_a_profile_without_its_own_prompt_falls_back_and_says_so() -> None:
    """yaml에 성향을 추가했다고 그날 분석 전체가 죽으면 안 된다 — 다만 조용하면 안 된다."""
    prompt = load_system_prompt("strategy", profile="not-a-persona")

    assert prompt.content == load_system_prompt("strategy").content
    assert prompt.variant is None


def test_tasks_without_personas_ignore_the_profile() -> None:
    """성향은 판단(07)의 축이다 — 공시 요약에까지 번지면 페르소나가 의미를 잃는다."""
    assert load_system_prompt("disclosure", profile="aggressive").variant is None
