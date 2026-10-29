"""Assemble configured providers and switch the current coding session."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx

from ..ai.provider import Provider
from ..ai.providers.anthropic_messages import AnthropicProvider
from ..ai.providers.openai_compatible import OpenAICompatibleProvider
from ..ai.types import ModelOptions, ModelSpec
from ..coding.coding_session import CodingSession
from ..coding.context_hooks import SystemPromptHooks
from ..coding.project_context import ProjectContextFile
from ..coding.skill_hooks import SkillHooks
from ..coding.skills import SkillManager
from ..agent.hooks import AgentHooks
from ..settings import SettingsStore, provider_credentials
from .prompts import build_system_prompt


@dataclass(frozen=True)
class ModelChoice:
    reference: str
    label: str
    description: str


class ModelController:
    def __init__(
        self, store: SettingsStore, client: httpx.AsyncClient,
        cwd: Path, tool_names: list[str], context_files: list[ProjectContextFile],
        max_output_tokens: int, *, fake_provider: Optional[Provider] = None,
        skills: Optional[SkillManager] = None,
    ) -> None:
        self.store, self.client = store, client
        self.cwd, self.tool_names, self.context_files = cwd, tool_names, context_files
        self.max_output_tokens = max_output_tokens
        self.fake_provider = fake_provider
        self.skills = skills
        self.settings = store.load()
        self.effort = "max"

    def effort_for(self, model: ModelSpec) -> Optional[str]:
        if (model.api == "chat-completions" and urlsplit(model.base_url).hostname == "api.deepseek.com"
                and model.id in {"deepseek-flash", "deepseek-v4-pro"}):
            return self.effort
        return None

    def set_effort(self, model: ModelSpec, effort: str) -> None:
        if self.effort_for(model) is None:
            raise ValueError("当前模型尚未适配思考强度设置。")
        if effort not in {"low", "high", "max"}:
            raise ValueError("用法：/effort low|high|max")
        self.effort = effort

    def reload(self) -> None:
        self.settings = self.store.load()

    def choices(self) -> list[ModelChoice]:
        choices = []
        for model in self.settings.model_specs():
            provider = self.settings.providers[model.provider]
            has_key = bool(provider.api_key.get_secret_value()) or provider.auth_type == "none" or any(
                name.lower() in {"authorization", "x-api-key"} and value.get_secret_value()
                for name, value in provider.headers.items()
            )
            choices.append(ModelChoice(
                reference=f"{model.provider}/{model.id}", label=model.name,
                description=f"{provider.name or model.provider} · {model.api}" + (" · 未配置密钥" if not has_key else ""),
            ))
        if self.fake_provider is not None:
            choices.extend(ModelChoice(f"fake/{model.id}", model.name, "离线固定响应") for model in self.fake_provider.get_models())
        return choices

    def resolve(self, reference: Optional[str] = None, *, provider_id: Optional[str] = None) -> ModelSpec:
        if self.fake_provider is not None and (provider_id == "fake" or reference in {"fake/fake-text", "fake-text"}):
            return self.fake_provider.get_models()[0]
        if not self.settings.providers:
            raise ValueError(f"尚未配置模型。运行 citrus --init-config，然后编辑 {self.store.path}。")
        return self.settings.resolve(reference, provider_id=provider_id)

    def provider(self, model: ModelSpec) -> Provider:
        if model.provider == "fake" and self.fake_provider is not None:
            return self.fake_provider
        config = self.settings.providers[model.provider]
        api_key, headers = provider_credentials(config)
        models = [item for item in self.settings.model_specs() if item.provider == model.provider]
        if config.api == "anthropic-messages":
            return AnthropicProvider(
                self.client, models, api_key, provider_id=model.provider, name=config.name,
                auth_type=config.auth_type, headers=headers, extra_body=config.extra_body,
            )
        return OpenAICompatibleProvider(
            self.client, models, api_key, provider_id=model.provider, name=config.name,
            auth_type=config.auth_type, headers=headers, extra_body=config.extra_body,
            include_usage=config.include_usage, max_tokens_field=config.max_tokens_field,
            reasoning_effort=(lambda: self.effort) if self.effort_for(model) is not None else None,
        )

    def options(self, model: ModelSpec) -> ModelOptions:
        return ModelOptions(max_output_tokens=min(self.max_output_tokens, model.max_output_tokens or self.max_output_tokens))

    def hooks(self, model: ModelSpec) -> AgentHooks:
        base = SystemPromptHooks(build_system_prompt(
            self.cwd, self.tool_names, self.context_files,
            model_id=f"{model.provider}/{model.id}", supports_images=model.supports_images,
        ))
        return SkillHooks(base, self.skills) if self.skills is not None else base

    def select(self, reference: str, session: CodingSession) -> ModelSpec:
        self.reload()
        model = self.resolve(reference)
        session.switch_model(self.provider(model), model, options=self.options(model), hooks=self.hooks(model))
        return model
