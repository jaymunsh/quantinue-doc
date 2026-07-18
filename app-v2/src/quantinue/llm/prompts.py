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
PROMPT_VERSION = "2026-07-13.1"
POLICY_VERSION = "quantinue-mvp.1"


def load_system_prompt(task: str) -> SystemPrompt:
    """Load one UTF-8 package resource or raise a typed boundary error."""
    resource_name = _RESOURCE_BY_TASK.get(task)
    if resource_name is None:
        raise PromptLoadError(task)
    resource = files("quantinue.prompts").joinpath(resource_name)
    try:
        content = resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise PromptLoadError(resource_name) from exc
    return SystemPrompt(
        task=task,
        version=PROMPT_VERSION,
        policy_version=POLICY_VERSION,
        content=content,
    )
