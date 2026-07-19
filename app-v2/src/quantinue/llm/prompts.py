"""Typed access to packaged system-prompt resources."""

from __future__ import annotations

from importlib.resources import files

from pydantic import BaseModel, ConfigDict


class SystemPrompt(BaseModel):
    """Immutable prompt text and audit versions."""

    model_config = ConfigDict(frozen=True)

    task: str
    version: str
    policy_version: str
    content: str
    # 어느 페르소나 파일이 실제로 쓰였는지. None이면 성향 없는 일반문이다.
    # 폴백을 조용히 하면 "성향별로 돌렸다"고 믿으면서 실은 한 프롬프트로
    # 돌고 있는 상태가 되는데, 그게 정확히 이 커밋이 고치는 결함이다.
    variant: str | None = None


class PromptLoadError(Exception):
    """A requested packaged prompt could not be loaded."""

    def __init__(self, resource: str) -> None:
        """Retain the safe resource name for callers and diagnostics."""
        self.resource = resource
        super().__init__(f"system prompt resource is unavailable: {resource}")


_RESOURCE_BY_TASK = {
    "disclosure": "role_05_disclosure.md",
    "news": "role_06_news.md",
    "strategy": "role_07_strategist.md",
    "critic": "role_08_critic.md",
    "review": "role_11_reviewer.md",
}
# 성향이 프롬프트를 바꾸는 태스크. 판단(07)만 여기 있는 것이 의도다 —
# 공시 요약이나 뉴스 채점은 "무엇이 사실인가"를 묻고, 그 답은 공격적이든
# 보수적이든 같아야 한다. 성향이 갈리는 지점은 **같은 사실을 놓고 얼마나
# 확신하느냐**뿐이다.
_PERSONA_TASKS = frozenset({"strategy"})
PROMPT_VERSION = "2026-07-20.1"
POLICY_VERSION = "quantinue-mvp.1"


def _read(resource_name: str) -> str | None:
    resource = files("quantinue.prompts").joinpath(resource_name)
    try:
        return resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ModuleNotFoundError):
        return None


def load_system_prompt(task: str, *, profile: str | None = None) -> SystemPrompt:
    """Load one UTF-8 package resource or raise a typed boundary error.

    성향별 파일이 있으면 그것을, 없으면 일반문을 준다. 없다고 예외를 던지지
    않는 이유: yaml에 성향 하나를 추가했다는 이유로 그날 분석 전체가 죽으면
    운영이 더 위험하다. 대신 어느 파일이 쓰였는지를 ``variant``에 남겨서
    폴백이 조용히 지나가지 않게 한다.
    """
    resource_name = _RESOURCE_BY_TASK.get(task)
    if resource_name is None:
        raise PromptLoadError(task)
    variant: str | None = None
    content: str | None = None
    if profile and task in _PERSONA_TASKS:
        stem = resource_name.removesuffix(".md")
        content = _read(f"{stem}_{profile}.md")
        if content is not None:
            variant = profile
    if content is None:
        content = _read(resource_name)
    if content is None:
        raise PromptLoadError(resource_name)
    return SystemPrompt(
        task=task,
        version=PROMPT_VERSION,
        policy_version=POLICY_VERSION,
        content=content,
        variant=variant,
    )
