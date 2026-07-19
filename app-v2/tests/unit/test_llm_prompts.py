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


@pytest.mark.parametrize("profile", ["aggressive", "conservative"])
def test_personas_name_what_our_evidence_cannot_answer(profile: str) -> None:
    """근거 프레임이 요구하지만 우리가 못 가진 것을 명시해야 LLM이 안 지어낸다.

    두 성향 모두 실제 트레이딩 방법론에 정박했지만, 그 방법론들은 재무제표를
    요구한다. 우리 원장에는 일봉·공시 종류·뉴스뿐이다. 범위 밖이라고 적어두지
    않으면 모델이 매출 성장률과 적정가를 지어낸다.
    """
    prompt = load_system_prompt("strategy", profile=profile)

    assert "증거 범위 밖" in prompt.content
    assert "지어내" in prompt.content


def test_the_critic_knows_which_evidence_we_deliberately_do_not_have() -> None:
    """07 페르소나에는 있고 08에는 없던 처방.

    두 페르소나에는 "이 프레임이 요구하지만 우리가 못 가진 것"(⚠️ 섹션)을 박아
    지어내기를 막았다. 그런데 크리틱은 그 처방을 못 받아서, 재무 근거 부재를
    **반박 사유**로 삼는다 — 우리가 의도적으로 갖지 않기로 한 것을 근거로
    모든 제안을 영원히 기각하는 셈이다. 범위를 아는 반박자만 범위 안에서
    반박할 수 있다.
    """
    # When
    prompt = load_system_prompt("critic")

    # Then
    assert "재무제표" in prompt.content
    assert "범위" in prompt.content


def test_the_critic_is_told_to_check_claims_against_the_numbers_it_is_given() -> None:
    """실측: 08이 07의 산문만 보고 "네가 없다고 했다"를 반복했다. 이제 같은
    지표를 받으므로, 반박은 데이터와의 대조여야 한다."""
    # When
    prompt = load_system_prompt("critic")

    # Then
    assert "지표" in prompt.content


@pytest.mark.parametrize("profile", ["aggressive", "conservative"])
def test_the_persona_may_not_cite_facts_that_were_not_in_its_input(profile: str) -> None:
    """실측: 07이 "애널리스트 상향 조정"을 근거로 들었다 — 입력 어디에도 없는 사실이다.

    기존 ⚠️ 섹션은 **재무제표**만 금지했다. 그래서 애널리스트 의견·목표주가처럼
    재무가 아닌 외부 사실은 그물을 빠져나갔고, 08이 그것을 잡아 기각했다
    (그 기각 자체는 옳다). 금지 범위를 "입력에 없는 모든 사실"로 넓힌다.
    """
    # When
    prompt = load_system_prompt("strategy", profile=profile)

    # Then
    assert "입력에 없는" in prompt.content


@pytest.mark.parametrize("profile", ["aggressive", "conservative"])
def test_the_persona_separates_a_weak_reading_from_a_missing_one(profile: str) -> None:
    """실측: `vol_ratio=1.12`(평균 이상)인 종목에 07이 "거래량 부재"라고 적었다.

    08은 그것을 "데이터가 없다는 주장"으로 읽고 모순을 지적했다 — 두 역할이
    같은 단어를 다르게 이해했다. 값이 낮은 것과 값이 없는 것은 다른 사실이고,
    매도 판단에서는 정반대의 결론을 부른다.
    """
    # When
    prompt = load_system_prompt("strategy", profile=profile)

    # Then
    assert "indicators=none" in prompt.content
